"""Application facade — inbound adapters and tests talk to this object."""

from __future__ import annotations

import os
import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from arbiter.application.services.model_quorum import ModelQuorumService
from arbiter.application.voters_config import VotersConfig, assert_roster_matches_config
from arbiter.domain.errors import DomainError
from arbiter.domain.events import (
    BaselineVerdict,
    BreakGlassUsed,
    CoverageChecked,
    DecisionInvalidated,
    HoldAccepted,
    HoldAdjudicated,
    RuleEstablished,
)
from arbiter.domain.model import Decision
from arbiter.domain.services.dependencies import (
    cascade_invalidations,
    dependency_edges_from_wire,
)
from arbiter.domain.services.installed_rules import RULE_REQUIRE_CONTRACT_TEST
from arbiter.domain.timeutil import format_iso, parse_iso


class Application:
    def __init__(
        self,
        *,
        events: Any,
        evidence: Any,
        responses: Any,
        load_rules: Callable[[], dict[str, Any] | None],
        load_voters: Callable[[], VotersConfig | None],
        now: Callable[[], datetime],
        new_id: Callable[[], str],
        voter_gateway: Any | None = None,
        root: Path | None = None,
    ) -> None:
        self._events = events
        self._evidence = evidence
        self._responses = responses
        self._load_rules = load_rules
        self._load_voters = load_voters
        self._now = now
        self._new_id = new_id
        self._voter_gateway = voter_gateway
        self.root = Path(root) if root is not None else None
        self.commands = self

    @property
    def ledger_path(self) -> Path:
        if self.root is None:
            raise DomainError("application root not configured")
        return self.root / "ledger.jsonl"

    @property
    def bundles_dir(self) -> Path:
        if self.root is None:
            raise DomainError("application root not configured")
        return self.root / "bundles"

    def now(self) -> datetime:
        return self._now()

    def load_voters_config(self) -> VotersConfig | None:
        return self._load_voters()

    def load_rules(self) -> dict[str, Any] | None:
        return self._load_rules()

    def read_all_wire(self) -> list[dict[str, Any]]:
        return self._events.read_all_wire()

    def replay(self, decision_id: str) -> Decision | None:
        return self._events.load_decision(decision_id)

    def load_bundle(self, digest: str) -> dict[str, Any]:
        return self._evidence.load(digest)

    def store_response(self, **kwargs: Any) -> str:
        return self._responses.store(**kwargs)

    def open_decision(
        self,
        *,
        question: str,
        options: list[str],
        voters: list[str],
        evidence: dict[str, Any],
        criticality: str | None = None,
        ttl_seconds: int = 900,
        opened_by: str = "open_decision",
        now: datetime | None = None,
        check_voters_config: bool = True,
        scope: list[str] | None = None,
        mode: str | None = None,
        depends_on: list[str] | None = None,
        establishes_rule: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = self._load_voters()
        if check_voters_config and config is not None:
            assert_roster_matches_config(voters, config)
        resolved_mode = mode
        if resolved_mode is None:
            env_shadow = os.environ.get("ARBITER_SHADOW_MODE") == "1"
            cfg_shadow = bool(
                config is not None and getattr(config, "shadow_mode", False)
            )
            resolved_mode = "shadow" if (env_shadow or cfg_shadow) else "enforce"
        digest = self._evidence.store(evidence)
        moment = now or self._now()
        decision_id = self._new_id()
        event = Decision.open(
            decision_id=decision_id,
            question=question,
            options=options,
            voters=voters,
            evidence=evidence,
            rules=self._load_rules(),
            criticality=criticality,
            ttl_seconds=ttl_seconds,
            opened_by=opened_by,
            at=moment,
            bundle_sha256=digest,
            scope=scope,
            mode=resolved_mode,
            depends_on=depends_on,
            establishes_rule=establishes_rule,
            wire_events=self._events.read_all_wire(),
        )
        self._events.append(event)
        return {
            "decision_id": decision_id,
            "criticality": event.criticality,
            "options": list(options),
            "voters": list(voters),
            "deadline": event.deadline,
            "bundle_sha256": digest,
            "scope": list(event.scope),
            "mode": event.mode,
            "depends_on": list(event.depends_on),
            "establishes_rule": (
                dict(event.establishes_rule)
                if event.establishes_rule is not None
                else None
            ),
        }

    def expand_decision_scope(self, decision_id: str, patterns: list[str]) -> None:
        self._require(decision_id).expand_scope(patterns)

    def record_hold_accepted(self, event: HoldAccepted) -> None:
        self._events.append(event)

    def record_hold_adjudicated(self, event: HoldAdjudicated) -> None:
        self._events.append(event)

    def record_coverage_checked(self, event: CoverageChecked) -> None:
        self._events.append(event)

    def record_break_glass(self, event: BreakGlassUsed) -> None:
        self._events.append(event)

    def record_baseline_verdict(self, event: BaselineVerdict) -> None:
        self._events.append(event)

    def invalidate_decision(
        self,
        decision_id: str,
        *,
        reason: str,
        cascaded_from: str | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """Invalidate decision_id and cascade to dependents. Returns all invalidated ids."""
        moment = now or self._now()
        wire = self._events.read_all_wire()
        edges = dependency_edges_from_wire(wire)
        order = cascade_invalidations(decision_id, edges)
        already = {
            e["decision_id"]
            for e in wire
            if e.get("event") == DecisionInvalidated.TYPE
            and isinstance(e.get("decision_id"), str)
        }
        emitted: list[str] = []
        for did in order:
            if did in already:
                continue
            self._events.append(
                DecisionInvalidated(
                    at=format_iso(moment),
                    decision_id=did,
                    reason=reason if did == decision_id else "cascaded_invalidation",
                    cascaded_from=None
                    if did == decision_id
                    else (cascaded_from or decision_id),
                )
            )
            emitted.append(did)
        return emitted

    def establish_rule_from_decision(
        self, decision_id: str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """If the decision established a rule and resolved proceed-class, append rule.established."""
        state = self._require(decision_id)
        if state.establishes_rule is None or state.resolution is None:
            return None
        verdict = state.resolution.get("verdict")
        if verdict not in ("allow", "allow_narrow"):
            return None
        for raw in self._events.read_all_wire():
            if (
                raw.get("event") == RuleEstablished.TYPE
                and raw.get("decision_id") == decision_id
            ):
                return None
        spec = dict(state.establishes_rule)
        kind = str(spec.get("kind") or RULE_REQUIRE_CONTRACT_TEST)
        path_glob = str(spec.get("path_glob") or "")
        detail = str(spec.get("detail") or "")
        rule_id = str(spec.get("rule_id") or f"rule:{decision_id}")
        moment = now or self._now()
        event = RuleEstablished(
            at=format_iso(moment),
            rule_id=rule_id,
            decision_id=decision_id,
            kind=kind,
            path_glob=path_glob,
            detail=detail,
        )
        self._events.append(event)
        return {
            "rule_id": rule_id,
            "decision_id": decision_id,
            "kind": kind,
            "path_glob": path_glob,
        }

    def cast_vote(
        self,
        *,
        decision_id: str,
        voter: str,
        option: str,
        confidence: float,
        kill_criterion: str,
        bundle_sha256_hex: str,
        round: int = 1,
        revision_reason: str | None = None,
        now: datetime | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._require(decision_id)
        moment = now or self._now()
        event = state.cast_vote(
            voter=voter,
            option=option,
            confidence=confidence,
            kill_criterion=kill_criterion,
            bundle_sha256_hex=bundle_sha256_hex,
            round=round,
            revision_reason=revision_reason,
            at=moment,
            meta=meta,
        )
        self._events.append(event)
        cast = state.votes_cast_in_round(round) + 1
        return {
            "recorded": True,
            "votes_cast": cast,
            "votes_required": state.votes_required(),
            "round": round,
        }

    def record_vote_failed(
        self,
        *,
        decision_id: str,
        voter: str,
        round: int,
        reason: str,
        detail: str | None = None,
        now: datetime | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._require(decision_id)
        moment = now or self._now()
        event = state.fail_vote(
            voter=voter,
            round=round,
            reason=reason,
            at=moment,
            detail=detail,
            meta=meta,
        )
        self._events.append(event)
        return {"recorded": True, "voter": voter, "round": round, "reason": reason}

    def record_round2_labels(
        self,
        *,
        decision_id: str,
        labels: dict[str, str],
        now: datetime | None = None,
    ) -> None:
        state = self._require(decision_id)
        moment = now or self._now()
        self._events.append(state.open_round2(labels=labels, at=moment))

    def resolve_decision(
        self, decision_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        moment = now or self._now()
        state = self._events.load_decision(decision_id)
        if state is None:
            return {
                "verdict": "deny",
                "chosen_option": None,
                "reason": "quorum_not_met",
                "tally": {},
                "dissent": [],
                "quorum": {
                    "required": 0,
                    "got": 0,
                    "missing": [f"decision:{decision_id}"],
                },
            }
        result, event = state.resolve_at(at=moment)
        if state.resolution is not None:
            return {
                "verdict": state.resolution["verdict"],
                "chosen_option": state.resolution["chosen_option"],
                "reason": "already_resolved",
                "tally": dict(state.resolution["tally"]),
                "dissent": list(state.resolution["dissent"]),
                "quorum": result.as_dict()["quorum"],
            }
        assert event is not None
        self._events.append(event)
        state = self._events.load_decision(decision_id)
        if state is not None and state.establishes_rule is not None:
            self.establish_rule_from_decision(decision_id, now=moment)
        return result.as_dict()

    def get_decision(
        self, decision_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        state = self._events.load_decision(decision_id)
        moment = now or self._now()
        if state is None:
            return {
                "decision_id": decision_id,
                "status": "unknown",
                "verdict": "deny",
                "reason": "quorum_not_met",
                "missing": [f"decision:{decision_id}"],
                "votes": [],
                "seconds_to_deadline": None,
            }
        deadline = parse_iso(state.deadline)
        seconds_to_deadline = (deadline - moment).total_seconds()
        deadline_passed = moment >= deadline
        status = "resolved" if state.resolution else "open"
        eff = state.effective_votes()
        return {
            "decision_id": decision_id,
            "status": status,
            "question": state.question,
            "options": list(state.options),
            "scope": list(state.scope),
            "mode": state.mode,
            "criticality": state.criticality,
            "criticality_source": state.criticality_source,
            "voters": list(state.voters),
            "bundle_sha256": state.bundle_sha256,
            "deadline": state.deadline,
            "deadline_passed": deadline_passed,
            "seconds_to_deadline": seconds_to_deadline,
            "votes": [
                {
                    "voter": v,
                    "option": eff[v]["option"],
                    "confidence": eff[v]["confidence"],
                    "kill_criterion": eff[v]["kill_criterion"],
                    "round": eff[v]["round"],
                }
                for v in state.voters
                if v in eff
            ],
            "missing_voters": state.missing_voters(),
            "failures": list(state.failures),
            "reveal_labels": state.reveal_labels,
            "resolution": state.resolution,
        }

    def get_gate_policy(self) -> dict[str, Any]:
        from arbiter.application.services.plan_gate import get_gate_policy as _policy

        return _policy(self)

    async def ensure_plan(self, plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        from arbiter.application.services.plan_gate import ensure_plan as _ensure

        return await _ensure(self, plan, **kwargs)

    def check_coverage(self, **kwargs: Any) -> dict[str, Any]:
        from arbiter.application.services.coverage import check_coverage as _check

        return _check(self, rules=self._load_rules(), **kwargs)

    def eval_report(
        self, *, repo: Path | None = None, horizon_days: int = 14
    ) -> dict[str, Any]:
        from arbiter.application.services.eval_report import build_eval_report

        return build_eval_report(
            self, repo=repo or self.root, horizon_days=horizon_days
        )

    def verify_commit_paths(
        self,
        *,
        paths: list[str],
        decision_id: str | None,
        commit_at: datetime | None = None,
        allow_break_glass: bool = False,
    ) -> dict[str, Any]:
        from arbiter.application.services.commit_guard import verify_commit

        return verify_commit(
            self,
            paths=paths,
            decision_id=decision_id,
            commit_at=commit_at,
            allow_break_glass=allow_break_glass,
            rules=self._load_rules(),
        )

    async def run_model_quorum(
        self,
        decision_id: str,
        *,
        config: VotersConfig | None = None,
        rng: random.Random | None = None,
    ) -> dict[str, Any]:
        cfg = config if config is not None else self._load_voters()
        if cfg is None:
            raise DomainError(
                "arbiter.voters.yaml required for run_model_quorum "
                "(set ARBITER_VOTERS_PATH or place the file in cwd)"
            )
        if self._voter_gateway is None:
            raise DomainError("voter gateway not configured")
        service = ModelQuorumService(
            commands=self,
            events=self._events,
            evidence=self._evidence,
            responses=self._responses,
            voters=self._voter_gateway,
            clock=self,
            config=cfg,
            rng=rng or random.Random(),
        )
        return await service.run(decision_id)

    def _require(self, decision_id: str) -> Decision:
        state = self._events.load_decision(decision_id)
        if state is None:
            raise DomainError(f"unknown decision_id: {decision_id}")
        return state
