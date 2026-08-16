"""Command handlers — single write path through the event store."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from arbiter.application.ports import (
    Clock,
    EvidenceStore,
    EventStore,
    IdGenerator,
    RulesSource,
    VotersConfigSource,
)
from arbiter.application.voters_config import assert_roster_matches_config
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
from arbiter.domain.timeutil import format_iso


class CommandHandlers:
    def __init__(
        self,
        *,
        events: EventStore,
        evidence: EvidenceStore,
        rules: RulesSource,
        voters_config: VotersConfigSource,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._events = events
        self._evidence = evidence
        self._rules = rules
        self._voters_config = voters_config
        self._clock = clock
        self._ids = ids

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
        config = self._voters_config.load()
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
        moment = now or self._clock.now()
        decision_id = self._ids.new_decision_id()
        event = Decision.open(
            decision_id=decision_id,
            question=question,
            options=options,
            voters=voters,
            evidence=evidence,
            rules=self._rules.load(),
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

    def expand_decision_scope(
        self, decision_id: str, patterns: list[str]
    ) -> None:
        state = self._require(decision_id)
        state.expand_scope(patterns)

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
        moment = now or self._clock.now()
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
                    cascaded_from=None if did == decision_id else (cascaded_from or decision_id),
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
        moment = now or self._clock.now()
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
        moment = now or self._clock.now()
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
        moment = now or self._clock.now()
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
        moment = now or self._clock.now()
        self._events.append(state.open_round2(labels=labels, at=moment))

    def resolve_decision(
        self, decision_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        moment = now or self._clock.now()
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
        # Re-apply so state.resolution is set for rule installation.
        state = self._events.load_decision(decision_id)
        if state is not None and state.establishes_rule is not None:
            self.establish_rule_from_decision(decision_id, now=moment)
        return result.as_dict()

    def _require(self, decision_id: str) -> Decision:
        state = self._events.load_decision(decision_id)
        if state is None:
            raise DomainError(f"unknown decision_id: {decision_id}")
        return state
