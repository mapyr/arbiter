"""Decision aggregate — reconstituted from the event stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from arbiter.domain.errors import DomainError
from arbiter.domain.events import (
    DecisionOpened,
    DecisionResolved,
    DomainEvent,
    BaselineVerdict,
    BreakGlassUsed,
    CoverageChecked,
    DecisionInvalidated,
    HoldAccepted,
    HoldAdjudicated,
    ProbeCompleted,
    ProbeRequested,
    QuorumRound2Opened,
    RuleEstablished,
    VoteCast,
    VoteFailed,
)
from arbiter.domain.services.dependencies import (
    DEFAULT_MAX_DEPENDENCY_DEPTH,
    assert_depth_ok,
    assert_no_cycle,
    dependency_edges_from_wire,
)
from arbiter.domain.services.classify import apply_criticality, classify
from arbiter.domain.services.formulation import assert_formulation_allowed
from arbiter.domain.services.quorum import QuorumResult, resolve, votes_required
from arbiter.domain.services.scope import normalize_scope_patterns
from arbiter.domain.timeutil import format_iso, parse_iso


@dataclass
class Decision:
    decision_id: str
    question: str
    options: list[str]
    criticality: str
    criticality_source: str
    voters: list[str]
    bundle_sha256: str
    deadline: str
    opened_by: str
    opened_at: str
    scope: list[str] = field(default_factory=list)
    mode: str = "enforce"
    depends_on: list[str] = field(default_factory=list)
    establishes_rule: dict[str, Any] | None = None
    invalidated: bool = False
    rounds: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    reveal_labels: dict[str, str] | None = None
    resolution: dict[str, Any] | None = None
    probes: list[dict[str, Any]] = field(default_factory=list)

    def highest_round(self) -> int:
        return max(self.rounds) if self.rounds else 0

    def effective_votes(self) -> dict[str, dict[str, Any]]:
        if not self.rounds:
            return {}
        return dict(self.rounds[self.highest_round()])

    def missing_voters(self) -> list[str]:
        eff = self.effective_votes()
        return [v for v in self.voters if v not in eff]

    @classmethod
    def from_events(cls, events: list[DomainEvent]) -> Decision | None:
        state: Decision | None = None
        for event in events:
            state = cls._apply(state, event)
        return state

    @staticmethod
    def _apply(state: Decision | None, event: DomainEvent) -> Decision | None:
        if isinstance(event, DecisionOpened):
            return Decision(
                decision_id=event.decision_id,
                question=event.question,
                options=list(event.options),
                criticality=event.criticality,
                criticality_source=event.criticality_source,
                voters=list(event.voters),
                bundle_sha256=event.bundle_sha256,
                deadline=event.deadline,
                opened_by=event.opened_by,
                opened_at=event.at,
                scope=list(event.scope),
                mode=event.mode,
                depends_on=list(event.depends_on),
                establishes_rule=(
                    dict(event.establishes_rule)
                    if event.establishes_rule is not None
                    else None
                ),
            )
        if state is None:
            return None
        if isinstance(
            event,
            (
                HoldAccepted,
                HoldAdjudicated,
                CoverageChecked,
                BreakGlassUsed,
                BaselineVerdict,
                RuleEstablished,
            ),
        ):
            # Ledger linkage only — does not mutate decision aggregate state.
            return state
        if isinstance(event, DecisionInvalidated):
            if event.decision_id == state.decision_id:
                state.invalidated = True
            return state
        if isinstance(event, (ProbeRequested, ProbeCompleted)):
            if isinstance(event, ProbeCompleted):
                state.probes.append(
                    {
                        "voter": event.voter,
                        "round": event.round,
                        "probe": event.probe,
                        "params": dict(event.params),
                        "result_sha256": event.result_sha256,
                        "result_text": event.result_text,
                        "truncated": event.truncated,
                    }
                )
            return state
        if isinstance(event, VoteCast):
            vote: dict[str, Any] = {
                "voter": event.voter,
                "option": event.option,
                "confidence": event.confidence,
                "kill_criterion": event.kill_criterion,
                "at": event.at,
                "bundle_sha256": event.bundle_sha256,
                "round": event.round,
            }
            if event.revision_reason is not None:
                vote["revision_reason"] = event.revision_reason
            vote.update(event.meta)
            state.rounds.setdefault(event.round, {})[event.voter] = vote
        elif isinstance(event, VoteFailed):
            state.failures.append(
                {
                    "voter": event.voter,
                    "round": event.round,
                    "reason": event.reason,
                    "at": event.at,
                    "detail": event.detail,
                }
            )
        elif isinstance(event, QuorumRound2Opened):
            state.reveal_labels = dict(event.labels)
        elif isinstance(event, DecisionResolved):
            state.resolution = {
                "verdict": event.verdict,
                "chosen_option": event.chosen_option,
                "reason": event.reason,
                "tally": dict(event.tally),
                "dissent": list(event.dissent),
                "at": event.at,
            }
        return state

    @staticmethod
    def open(
        *,
        decision_id: str,
        question: str,
        options: list[str],
        voters: list[str],
        evidence: dict[str, Any],
        rules: dict[str, Any] | None,
        criticality: str | None,
        ttl_seconds: int,
        opened_by: str,
        at: datetime,
        bundle_sha256: str,
        scope: list[str] | tuple[str, ...] | None = None,
        mode: str = "enforce",
        depends_on: list[str] | tuple[str, ...] | None = None,
        establishes_rule: dict[str, Any] | None = None,
        wire_events: list[dict[str, Any]] | None = None,
    ) -> DecisionOpened:
        _validate_open(question, options, voters, ttl_seconds)
        if not isinstance(evidence, dict):
            raise DomainError("evidence must be an object")
        if not isinstance(bundle_sha256, str) or not bundle_sha256:
            raise DomainError("bundle_sha256 must be a non-empty string")
        if mode not in ("enforce", "shadow"):
            raise DomainError("mode must be 'enforce' or 'shadow'")
        deps = tuple(depends_on or ())
        for dep in deps:
            if not isinstance(dep, str) or not dep.strip():
                raise DomainError("depends_on entries must be non-empty strings")
        if establishes_rule is not None and not isinstance(establishes_rule, dict):
            raise DomainError("establishes_rule must be an object when present")
        max_depth = DEFAULT_MAX_DEPENDENCY_DEPTH
        if isinstance(rules, dict):
            raw_depth = (rules.get("dependencies") or {}).get("max_depth")
            if isinstance(raw_depth, int) and not isinstance(raw_depth, bool):
                max_depth = raw_depth
        if deps:
            edges = dependency_edges_from_wire(wire_events or [])
            assert_no_cycle(decision_id, deps, edges)
            assert_depth_ok(deps, edges, max_depth=max_depth)
        scope_patterns = normalize_scope_patterns(scope)
        assert_formulation_allowed(options=options, scope=scope_patterns, rules=rules)
        classification = classify(evidence, rules)
        final_criticality, source = apply_criticality(classification, criticality)
        deadline_dt = datetime.fromtimestamp(
            at.timestamp() + int(ttl_seconds), tz=timezone.utc
        )
        return DecisionOpened(
            at=format_iso(at),
            decision_id=decision_id,
            question=question,
            options=tuple(options),
            criticality=final_criticality,
            criticality_source=source,
            voters=tuple(voters),
            bundle_sha256=bundle_sha256,
            deadline=format_iso(deadline_dt),
            opened_by=opened_by,
            scope=scope_patterns,
            mode=mode,
            depends_on=deps,
            establishes_rule=(
                dict(establishes_rule) if establishes_rule is not None else None
            ),
        )

    def expand_scope(self, patterns: list[str] | tuple[str, ...]) -> None:
        """Scope is immutable after open; expansion is always a new decision."""
        raise DomainError(
            f"cannot expand scope of decision {self.decision_id!r}; open a new decision"
        )

    def cast_vote(
        self,
        *,
        voter: str,
        option: str,
        confidence: float,
        kill_criterion: str,
        bundle_sha256_hex: str,
        round: int = 1,
        revision_reason: str | None = None,
        at: datetime,
        meta: dict[str, Any] | None = None,
    ) -> VoteCast:
        if self.resolution is not None:
            raise DomainError(f"decision already resolved: {self.decision_id}")
        if not isinstance(round, int) or isinstance(round, bool) or round < 1:
            raise DomainError("round must be a positive integer")
        if voter not in self.voters:
            raise DomainError(
                f"voter {voter!r} not in roster; allowed: {self.voters!r}"
            )
        round_votes = self.rounds.get(round, {})
        if voter in round_votes:
            raise DomainError(
                f"vote already cast for ({self.decision_id!r}, {voter!r}, round={round}); "
                "immutable"
            )
        if round > 1:
            prev = self.rounds.get(round - 1, {}).get(voter)
            if prev is None:
                raise DomainError(
                    f"no round {round - 1} vote for {voter!r}; cannot cast round {round}"
                )
            if option != prev["option"]:
                if not isinstance(revision_reason, str) or not revision_reason.strip():
                    raise DomainError(
                        "revision_reason required when option changes in a later round"
                    )
        if option not in self.options:
            raise DomainError(
                f"option {option!r} not in closed set; allowed: {self.options!r}"
            )
        if bundle_sha256_hex != self.bundle_sha256:
            raise DomainError(
                "bundle_sha256 mismatch: vote evidence differs from decision.opened"
            )
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise DomainError("confidence must be a number in [0.0, 1.0]")
        if not 0.0 <= float(confidence) <= 1.0:
            raise DomainError("confidence must be a number in [0.0, 1.0]")
        if not isinstance(kill_criterion, str) or not kill_criterion.strip():
            raise DomainError("kill_criterion must be a non-empty sentence")
        if revision_reason is not None:
            if not isinstance(revision_reason, str) or not revision_reason.strip():
                raise DomainError(
                    "revision_reason must be a non-empty sentence when present"
                )
            revision_reason = revision_reason.strip()
        return VoteCast(
            at=format_iso(at),
            decision_id=self.decision_id,
            voter=voter,
            option=option,
            confidence=float(confidence),
            kill_criterion=kill_criterion,
            bundle_sha256=bundle_sha256_hex,
            round=round,
            revision_reason=revision_reason,
            meta=dict(meta or {}),
        )

    def fail_vote(
        self,
        *,
        voter: str,
        round: int,
        reason: str,
        at: datetime,
        detail: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> VoteFailed:
        if self.resolution is not None:
            raise DomainError(f"decision already resolved: {self.decision_id}")
        if voter not in self.voters:
            raise DomainError(
                f"voter {voter!r} not in roster; allowed: {self.voters!r}"
            )
        return VoteFailed(
            at=format_iso(at),
            decision_id=self.decision_id,
            voter=voter,
            round=round,
            reason=reason,
            detail=detail,
            meta=dict(meta or {}),
        )

    def open_round2(
        self, *, labels: dict[str, str], at: datetime
    ) -> QuorumRound2Opened:
        return QuorumRound2Opened(
            at=format_iso(at),
            decision_id=self.decision_id,
            labels=dict(labels),
        )

    def resolve_at(self, *, at: datetime) -> tuple[QuorumResult, DecisionResolved | None]:
        """Return quorum result; emit DecisionResolved only when not yet resolved."""
        eff = self.effective_votes()
        vote_map = {v: info["option"] for v, info in eff.items()}
        deadline_passed = at >= parse_iso(self.deadline)
        result = resolve(
            criticality=self.criticality,
            voters=self.voters,
            votes=vote_map,
            options=self.options,
            deadline_passed=deadline_passed,
        )
        if self.resolution is not None:
            return result, None
        payload = result.as_dict()
        event = DecisionResolved(
            at=format_iso(at),
            decision_id=self.decision_id,
            verdict=payload["verdict"],
            chosen_option=payload["chosen_option"],
            reason=payload["reason"],
            tally=dict(payload["tally"]),
            dissent=tuple(payload["dissent"]),
        )
        return result, event

    def votes_cast_in_round(self, round_n: int) -> int:
        return len(self.rounds.get(round_n, {}))

    def votes_required(self) -> int:
        return votes_required(self.voters, self.criticality)


def _validate_open(
    question: str, options: list[str], voters: list[str], ttl_seconds: int
) -> None:
    if not isinstance(question, str) or not question.strip():
        raise DomainError("question must be a non-empty sentence")
    if not isinstance(options, list) or not (2 <= len(options) <= 8):
        raise DomainError("options must contain 2..8 entries")
    if any(not isinstance(o, str) or not o for o in options):
        raise DomainError("options must be non-empty strings")
    if len(set(options)) != len(options):
        raise DomainError("options must be unique")
    if not isinstance(voters, list) or not (1 <= len(voters) <= 7):
        raise DomainError("voters must contain 1..7 entries")
    if any(not isinstance(v, str) or not v for v in voters):
        raise DomainError("voters must be non-empty strings")
    if len(set(voters)) != len(voters):
        raise DomainError("voters must be unique")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds < 0:
        raise DomainError("ttl_seconds must be a non-negative integer")
