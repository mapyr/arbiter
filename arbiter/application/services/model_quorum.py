"""Blind model-quorum protocol — votes only via CastVote command path."""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter.application.handlers.commands import CommandHandlers
from arbiter.application.ports import Clock, EvidenceStore, EventStore, ResponseStore, VoterGateway
from arbiter.application.services.prompts import (
    build_blind_prompt,
    build_reveal_prompt,
    parse_vote_response,
    prompt_sha256,
)
from arbiter.application.voters_config import VotersConfig
from arbiter.domain.errors import DomainError
from arbiter.domain.events import BaselineVerdict
from arbiter.domain.services.quorum import resolve
from arbiter.domain.timeutil import format_iso, parse_iso


@dataclass
class _LatencyBucket:
    samples_ms: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.samples_ms.append(ms)

    def report(self) -> dict[str, Any]:
        if not self.samples_ms:
            return {"p50_ms": None, "p95_ms": None, "samples_ms": []}
        ordered = sorted(self.samples_ms)
        return {
            "p50_ms": _percentile(ordered, 50),
            "p95_ms": _percentile(ordered, 95),
            "samples_ms": list(self.samples_ms),
        }


def _percentile(ordered: list[float], pct: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


@dataclass
class ModelQuorumService:
    commands: CommandHandlers
    events: EventStore
    evidence: EvidenceStore
    responses: ResponseStore
    voters: VoterGateway
    clock: Clock
    config: VotersConfig
    rng: random.Random = field(default_factory=random.Random)

    async def run(self, decision_id: str) -> dict[str, Any]:
        state = self.events.load_decision(decision_id)
        if state is None:
            raise DomainError(f"unknown decision_id: {decision_id}")
        if state.resolution is not None:
            raise DomainError(f"decision already resolved: {decision_id}")
        if set(state.voters) != set(self.config.ids):
            raise DomainError(
                f"voters mismatch: decision={state.voters!r}, "
                f"arbiter.voters.yaml={self.config.ids!r}"
            )

        evidence = self.evidence.load(state.bundle_sha256)
        latencies = {v.id: _LatencyBucket() for v in self.config.voters}
        prompts_r1: dict[str, str] = {}

        # Baseline runs beside round 1 on the same blind prompt — never sees
        # peer votes; quorum never sees the baseline reply (separate event).
        baseline_task = None
        if self.config.baseline_voter:
            baseline_task = asyncio.create_task(
                self._collect_baseline(
                    decision_id=decision_id,
                    voter_id=self.config.baseline_voter,
                    question=state.question,
                    options=state.options,
                    evidence=evidence,
                    bundle_sha256=state.bundle_sha256,
                )
            )

        round1 = await self._run_round(
            decision_id=decision_id,
            round_n=1,
            state_options=state.options,
            question=state.question,
            evidence=evidence,
            bundle_sha256=state.bundle_sha256,
            latencies=latencies,
            prompts_out=prompts_r1,
            labeled_by_voter=None,
            prior_by_voter=None,
        )

        if baseline_task is not None:
            await baseline_task

        moment = self.clock.now()
        if moment >= parse_iso(state.deadline):
            return self._finish_resolve(decision_id, latencies, prompts_r1)

        if len(round1) < len(self.config.voters):
            return self._finish_resolve(decision_id, latencies, prompts_r1)

        state = self.events.load_decision(decision_id)
        assert state is not None
        vote_map = {v: info["option"] for v, info in state.effective_votes().items()}
        probe = resolve(
            criticality=state.criticality,
            voters=state.voters,
            votes=vote_map,
            options=state.options,
            deadline_passed=False,
        )
        # Quorum met on any semantic kind (allow / narrow / escalate / deny-as-choice).
        if probe.reason == "quorum_met":
            return self._finish_resolve(decision_id, latencies, prompts_r1)

        if not self.config.reveal_round:
            return self._finish_resolve(decision_id, latencies, prompts_r1)

        labels = self._assign_labels(state.voters)
        self.commands.record_round2_labels(decision_id=decision_id, labels=labels)
        voter_to_label = {vid: lab for lab, vid in labels.items()}

        labeled_by_voter: dict[str, list[dict[str, Any]]] = {}
        prior = state.effective_votes()
        for viewer in state.voters:
            peers = []
            for other in state.voters:
                if other == viewer:
                    continue
                peers.append(
                    {
                        "label": voter_to_label[other],
                        "option": prior[other]["option"],
                        "confidence": prior[other]["confidence"],
                        "kill_criterion": prior[other]["kill_criterion"],
                    }
                )
            peers.sort(key=lambda row: row["label"])
            labeled_by_voter[viewer] = peers

        await self._run_round(
            decision_id=decision_id,
            round_n=2,
            state_options=state.options,
            question=state.question,
            evidence=evidence,
            bundle_sha256=state.bundle_sha256,
            latencies=latencies,
            prompts_out={},
            labeled_by_voter=labeled_by_voter,
            prior_by_voter=prior,
        )
        return self._finish_resolve(decision_id, latencies, prompts_r1)

    def _assign_labels(self, voters: list[str]) -> dict[str, str]:
        labels = ["A", "B", "C"][: len(voters)]
        order = list(voters)
        self.rng.shuffle(order)
        return {lab: vid for lab, vid in zip(labels, order, strict=True)}

    async def _run_round(self, **kwargs: Any) -> dict[str, dict[str, Any]]:
        deadline = time.monotonic() + self.config.round_deadline_seconds
        decision_id = kwargs["decision_id"]
        round_n = kwargs["round_n"]

        async def one(voter_id: str) -> tuple[str, dict[str, Any] | None]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.commands.record_vote_failed(
                    decision_id=decision_id,
                    voter=voter_id,
                    round=round_n,
                    reason="deadline_passed",
                )
                return voter_id, None
            try:
                result = await asyncio.wait_for(
                    self._collect_vote(voter_id=voter_id, **kwargs),
                    timeout=remaining,
                )
                return voter_id, result
            except asyncio.TimeoutError:
                self.commands.record_vote_failed(
                    decision_id=decision_id,
                    voter=voter_id,
                    round=round_n,
                    reason="deadline_passed",
                )
                return voter_id, None

        gathered = await asyncio.gather(*[one(v.id) for v in self.config.voters])
        return {vid: vote for vid, vote in gathered if vote is not None}

    async def _collect_vote(
        self,
        *,
        voter_id: str,
        decision_id: str,
        round_n: int,
        state_options: list[str],
        question: str,
        evidence: dict[str, Any],
        bundle_sha256: str,
        latencies: dict[str, _LatencyBucket],
        prompts_out: dict[str, str],
        labeled_by_voter: dict[str, list[dict[str, Any]]] | None,
        prior_by_voter: dict[str, dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        spec = self.config.by_id(voter_id)
        if round_n == 1:
            prompt = build_blind_prompt(
                question=question, options=state_options, evidence=evidence
            )
        else:
            assert prior_by_voter is not None and labeled_by_voter is not None
            prompt = build_reveal_prompt(
                question=question,
                options=state_options,
                evidence=evidence,
                labeled_votes=labeled_by_voter[voter_id],
                own_prior=prior_by_voter[voter_id],
            )
        p_hash = prompt_sha256(prompt)
        prompts_out[voter_id] = p_hash
        api_key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        messages = [{"role": "user", "content": prompt}]
        prior = prior_by_voter[voter_id] if prior_by_voter else None
        prior_option = prior["option"] if prior is not None else None
        return await self._vote_attempts(
            voter_id=voter_id,
            decision_id=decision_id,
            round_n=round_n,
            state_options=state_options,
            bundle_sha256=bundle_sha256,
            latencies=latencies,
            messages=messages,
            prompt=prompt,
            p_hash=p_hash,
            spec=spec,
            api_key=api_key,
            prior_option=prior_option,
            last_error="invalid_response",
        )

    async def _vote_attempts(
        self,
        *,
        voter_id: str,
        decision_id: str,
        round_n: int,
        state_options: list[str],
        bundle_sha256: str,
        latencies: dict[str, _LatencyBucket],
        messages: list[dict[str, str]],
        prompt: str,
        p_hash: str,
        spec: Any,
        api_key: str | None,
        prior_option: str | None,
        last_error: str,
    ) -> dict[str, Any] | None:
        for attempt in range(2):
            if attempt == 1:
                messages = [
                    {"role": "user", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply failed validation: "
                            f"{last_error}. Reply again with a single JSON object only."
                        ),
                    },
                ]
            completion = await self.voters.complete(
                base_url=spec.base_url,
                model=spec.model,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                timeout_seconds=spec.timeout_seconds,
                messages=messages,
                api_key=api_key,
            )
            latencies[voter_id].add(completion.latency_ms)
            raw_store = {
                "voter": voter_id,
                "round": round_n,
                "attempt": attempt + 1,
                "model": spec.model,
                "prompt_sha256": p_hash,
                "latency_ms": completion.latency_ms,
                "error": completion.error,
                "response": completion.raw,
                "text": completion.text,
                "at": format_iso(self.clock.now()),
            }
            response_sha = self.responses.store(
                decision_id=decision_id,
                voter=voter_id,
                round_n=round_n,
                payload=raw_store,
            )
            meta = {
                "prompt_sha256": p_hash,
                "response_sha256": response_sha,
                "model": spec.model,
                "temperature": spec.temperature,
                "max_tokens": spec.max_tokens,
                "latency_ms": completion.latency_ms,
                "prompt_tokens": completion.prompt_tokens,
                "completion_tokens": completion.completion_tokens,
            }
            if not completion.ok or completion.text is None:
                last_error = completion.error or "invalid_response"
                if completion.error in ("timeout", "http_401") or (
                    completion.error and completion.error.startswith("http_")
                ):
                    self.commands.record_vote_failed(
                        decision_id=decision_id,
                        voter=voter_id,
                        round=round_n,
                        reason=completion.error or "http_error",
                        meta=meta,
                    )
                    return None
                if attempt == 0:
                    continue
                self.commands.record_vote_failed(
                    decision_id=decision_id,
                    voter=voter_id,
                    round=round_n,
                    reason=last_error,
                    meta=meta,
                )
                return None

            parsed = parse_vote_response(
                completion.text,
                options=state_options,
                prior_option=prior_option if round_n > 1 else None,
            )
            if isinstance(parsed, str):
                last_error = parsed
                if attempt == 0:
                    continue
                self.commands.record_vote_failed(
                    decision_id=decision_id,
                    voter=voter_id,
                    round=round_n,
                    reason="invalid_response",
                    detail=parsed,
                    meta=meta,
                )
                return None

            try:
                self.commands.cast_vote(
                    decision_id=decision_id,
                    voter=voter_id,
                    option=parsed.option,
                    confidence=parsed.confidence,
                    kill_criterion=parsed.kill_criterion,
                    bundle_sha256_hex=bundle_sha256,
                    round=round_n,
                    revision_reason=parsed.revision_reason,
                    meta=meta,
                )
            except DomainError as exc:
                last_error = str(exc)
                if attempt == 0:
                    continue
                self.commands.record_vote_failed(
                    decision_id=decision_id,
                    voter=voter_id,
                    round=round_n,
                    reason="invalid_response",
                    detail=str(exc),
                    meta=meta,
                )
                return None
            return {
                "option": parsed.option,
                "confidence": parsed.confidence,
                "kill_criterion": parsed.kill_criterion,
            }

        self.commands.record_vote_failed(
            decision_id=decision_id,
            voter=voter_id,
            round=round_n,
            reason=last_error,
        )
        return None

    async def _collect_baseline(
        self,
        *,
        decision_id: str,
        voter_id: str,
        question: str,
        options: list[str],
        evidence: dict[str, Any],
        bundle_sha256: str,
    ) -> None:
        spec = self.config.by_id(voter_id)
        prompt = build_blind_prompt(
            question=question, options=options, evidence=evidence
        )
        p_hash = prompt_sha256(prompt)
        api_key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        completion = await self.voters.complete(
            base_url=spec.base_url,
            model=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            timeout_seconds=spec.timeout_seconds,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key,
        )
        meta: dict[str, Any] = {
            "prompt_sha256": p_hash,
            "model": spec.model,
            "latency_ms": completion.latency_ms,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
            "line": "baseline",
        }
        raw_store = {
            "voter": voter_id,
            "round": 0,
            "line": "baseline",
            "model": spec.model,
            "prompt_sha256": p_hash,
            "latency_ms": completion.latency_ms,
            "error": completion.error,
            "response": completion.raw,
            "text": completion.text,
            "at": format_iso(self.clock.now()),
        }
        response_sha = self.responses.store(
            decision_id=decision_id,
            voter=f"baseline-{voter_id}",
            round_n=0,
            payload=raw_store,
        )
        meta["response_sha256"] = response_sha
        if not completion.ok or completion.text is None:
            self.commands.record_baseline_verdict(
                BaselineVerdict(
                    at=format_iso(self.clock.now()),
                    decision_id=decision_id,
                    voter=voter_id,
                    option=None,
                    confidence=None,
                    kill_criterion=None,
                    bundle_sha256=bundle_sha256,
                    ok=False,
                    reason=completion.error or "invalid_response",
                    meta=meta,
                )
            )
            return
        parsed = parse_vote_response(completion.text, options=options, prior_option=None)
        if isinstance(parsed, str):
            self.commands.record_baseline_verdict(
                BaselineVerdict(
                    at=format_iso(self.clock.now()),
                    decision_id=decision_id,
                    voter=voter_id,
                    option=None,
                    confidence=None,
                    kill_criterion=None,
                    bundle_sha256=bundle_sha256,
                    ok=False,
                    reason=parsed,
                    meta=meta,
                )
            )
            return
        self.commands.record_baseline_verdict(
            BaselineVerdict(
                at=format_iso(self.clock.now()),
                decision_id=decision_id,
                voter=voter_id,
                option=parsed.option,
                confidence=parsed.confidence,
                kill_criterion=parsed.kill_criterion,
                bundle_sha256=bundle_sha256,
                ok=True,
                reason="baseline_ok",
                meta=meta,
            )
        )

    def _finish_resolve(
        self,
        decision_id: str,
        latencies: dict[str, _LatencyBucket],
        prompts_r1: dict[str, str],
    ) -> dict[str, Any]:
        resolved = self.commands.resolve_decision(decision_id, now=self.clock.now())
        state = self.events.load_decision(decision_id)
        return {
            **resolved,
            "decision_id": decision_id,
            "mode": state.mode if state is not None else "enforce",
            "prompt_sha256": dict(prompts_r1),
            "latency": {vid: bucket.report() for vid, bucket in latencies.items()},
        }
