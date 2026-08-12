"""Typed domain events — wire field names match ledger.jsonl (adapter maps)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionOpened:
    at: str
    decision_id: str
    question: str
    options: tuple[str, ...]
    criticality: str
    criticality_source: str
    voters: tuple[str, ...]
    bundle_sha256: str
    deadline: str
    opened_by: str
    scope: tuple[str, ...] = ()
    # enforce | shadow — shadow must be visible on every open.
    mode: str = "enforce"
    # Parent decision ids; empty = independent.
    depends_on: tuple[str, ...] = ()
    # When set, a successful allow may install a runtime rule.
    establishes_rule: dict[str, Any] | None = None

    TYPE = "decision.opened"


@dataclass(frozen=True)
class VoteCast:
    at: str
    decision_id: str
    voter: str
    option: str
    confidence: float
    kill_criterion: str
    bundle_sha256: str
    round: int = 1
    revision_reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "vote.cast"


@dataclass(frozen=True)
class VoteFailed:
    at: str
    decision_id: str
    voter: str
    round: int
    reason: str
    detail: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "vote.failed"


@dataclass(frozen=True)
class QuorumRound2Opened:
    at: str
    decision_id: str
    labels: dict[str, str]

    TYPE = "quorum.round2.opened"


@dataclass(frozen=True)
class DecisionResolved:
    at: str
    decision_id: str
    verdict: str
    chosen_option: str | None
    reason: str
    tally: dict[str, int]
    dissent: tuple[dict[str, str], ...]

    TYPE = "decision.resolved"


@dataclass(frozen=True)
class HoldAccepted:
    """Immediate receipt trace for a Hangar delivery notification (Z1).

    Written before any adjudication so a swallowed delivery error is still
    distinguishable from "never arrived".
    """

    at: str
    approval_id: str
    call_id: str
    mcp_server_id: str
    tool_name: str
    arguments_hash: str
    expires_at: str
    requested_by: str | None = None
    tenant_id: str | None = None
    correlation_id: str = ""

    TYPE = "hold.accepted"


@dataclass(frozen=True)
class HoldAdjudicated:
    """Link a held tool call to the decision that covered or opened for it (I5)."""

    at: str
    call_id: str
    approval_id: str
    mcp_server_id: str
    tool_name: str
    path: str  # covered | quorum | passthrough | deny | insufficient_time
    approved: bool
    decision_id: str | None
    reason: str
    duration_ms: float
    quorum_latency_ms: float | None = None
    quorum_cost: dict[str, Any] | None = None
    resolver_principal: str | None = None
    requested_by: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "hold.adjudicated"


@dataclass(frozen=True)
class CoverageChecked:
    """Client built-in tool coverage check (layer 2)."""

    at: str
    tool: str
    paths: tuple[str, ...]
    approved: bool
    path: str  # covered | deny | break_glass
    decision_id: str | None
    reason: str
    actor: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "coverage.checked"


@dataclass(frozen=True)
class BaselineVerdict:
    """Single-model reference line — isolated from quorum votes."""

    at: str
    decision_id: str
    voter: str
    option: str | None
    confidence: float | None
    kill_criterion: str | None
    bundle_sha256: str
    ok: bool
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "baseline.verdict"


@dataclass(frozen=True)
class BreakGlassUsed:
    """Emergency bypass of layer 2 — must remain visible in the ledger."""

    at: str
    tool: str
    paths: tuple[str, ...]
    actor: str
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "break_glass.used"


@dataclass(frozen=True)
class ProbeRequested:
    """Voter asked for a closed-catalog probe — not a vote."""

    at: str
    decision_id: str
    voter: str
    round: int
    probe: str
    params: dict[str, str]
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "probe.requested"


@dataclass(frozen=True)
class ProbeCompleted:
    """Arbiter-executed probe result stored for replay."""

    at: str
    decision_id: str
    voter: str
    round: int
    probe: str
    params: dict[str, str]
    result_sha256: str
    result_text: str
    truncated: bool
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "probe.completed"


@dataclass(frozen=True)
class DecisionInvalidated:
    """Decision no longer covers; may cascade to dependents."""

    at: str
    decision_id: str
    reason: str
    cascaded_from: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "decision.invalidated"


@dataclass(frozen=True)
class RuleEstablished:
    """Deterministic runtime rule installed by a decision."""

    at: str
    rule_id: str
    decision_id: str
    kind: str
    path_glob: str
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)

    TYPE = "rule.established"


DomainEvent = (
    DecisionOpened
    | VoteCast
    | VoteFailed
    | QuorumRound2Opened
    | DecisionResolved
    | HoldAccepted
    | HoldAdjudicated
    | CoverageChecked
    | BaselineVerdict
    | BreakGlassUsed
    | ProbeRequested
    | ProbeCompleted
    | DecisionInvalidated
    | RuleEstablished
)
