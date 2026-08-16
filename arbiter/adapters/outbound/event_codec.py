"""Map domain events ↔ ledger.jsonl dicts (wire-compatible)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from arbiter.domain.events import (
    BaselineVerdict,
    BreakGlassUsed,
    CoverageChecked,
    DecisionInvalidated,
    DecisionOpened,
    DecisionResolved,
    DomainEvent,
    HoldAccepted,
    HoldAdjudicated,
    QuorumRound2Opened,
    RuleEstablished,
    VoteCast,
    VoteFailed,
)

_META_KEYS = (
    "prompt_sha256",
    "response_sha256",
    "model",
    "temperature",
    "max_tokens",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
)

_KNOWN = frozenset(
    {
        DecisionOpened,
        VoteCast,
        VoteFailed,
        QuorumRound2Opened,
        DecisionResolved,
        HoldAccepted,
        HoldAdjudicated,
        CoverageChecked,
        BreakGlassUsed,
        BaselineVerdict,
        DecisionInvalidated,
        RuleEstablished,
    }
)
_OMIT_EMPTY = {
    DecisionOpened: frozenset({"scope", "depends_on"}),
    VoteFailed: frozenset({"detail"}),
    HoldAccepted: frozenset({"correlation_id"}),
}
_KEEP_NONE = {DecisionResolved: frozenset({"chosen_option"})}
_META_BY_TYPE = {
    VoteCast: _META_KEYS,
    VoteFailed: ("prompt_sha256", "model", "latency_ms"),
    BaselineVerdict: (
        "prompt_sha256",
        "response_sha256",
        "model",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
    ),
}


def to_wire(event: DomainEvent) -> dict[str, Any]:
    cls = type(event)
    if cls not in _KNOWN:
        raise TypeError(f"unknown domain event: {cls!r}")
    data = asdict(event)
    meta = data.pop("meta", None) or {}
    omit_empty = _OMIT_EMPTY.get(cls, ())
    keep_none = _KEEP_NONE.get(cls, ())
    payload: dict[str, Any] = {"event": cls.TYPE}
    for key, value in data.items():
        if isinstance(value, tuple):
            value = list(value)
        if value is None and key not in keep_none:
            continue
        if key in omit_empty and not value:
            continue
        payload[key] = value
    if cls is HoldAdjudicated:
        items = meta.items()
    else:
        keys = _META_BY_TYPE.get(cls, ())
        items = ((k, meta[k]) for k in keys if k in meta)
    for key, value in items:
        if value is not None:
            payload[key] = value
    return payload


def _meta(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: raw[k] for k in keys if k in raw}


def from_wire(raw: dict[str, Any]) -> DomainEvent | None:
    kind = raw.get("event")
    if kind == DecisionOpened.TYPE:
        est = raw.get("establishes_rule")
        return DecisionOpened(
            at=raw["at"],
            decision_id=raw["decision_id"],
            question=raw["question"],
            options=tuple(raw["options"]),
            criticality=raw["criticality"],
            criticality_source=raw.get("criticality_source", "classifier"),
            voters=tuple(raw["voters"]),
            bundle_sha256=raw["bundle_sha256"],
            deadline=raw["deadline"],
            opened_by=raw["opened_by"],
            scope=tuple(raw.get("scope") or ()),
            mode=str(raw.get("mode") or "enforce"),
            depends_on=tuple(raw.get("depends_on") or ()),
            establishes_rule=dict(est) if isinstance(est, dict) else None,
        )
    if kind == VoteCast.TYPE:
        return VoteCast(
            at=raw["at"],
            decision_id=raw["decision_id"],
            voter=raw["voter"],
            option=raw["option"],
            confidence=raw["confidence"],
            kill_criterion=raw["kill_criterion"],
            bundle_sha256=raw["bundle_sha256"],
            round=int(raw.get("round", 1)),
            revision_reason=raw.get("revision_reason"),
            meta=_meta(raw, _META_KEYS),
        )
    if kind == VoteFailed.TYPE:
        return VoteFailed(
            at=raw["at"],
            decision_id=raw["decision_id"],
            voter=raw["voter"],
            round=int(raw.get("round", 1)),
            reason=raw["reason"],
            detail=raw.get("detail"),
            meta=_meta(raw, ("prompt_sha256", "model", "latency_ms")),
        )
    if kind == QuorumRound2Opened.TYPE:
        return QuorumRound2Opened(
            at=raw["at"],
            decision_id=raw["decision_id"],
            labels=dict(raw["labels"]),
        )
    if kind == DecisionResolved.TYPE:
        return DecisionResolved(
            at=raw["at"],
            decision_id=raw["decision_id"],
            verdict=raw["verdict"],
            chosen_option=raw["chosen_option"],
            reason=raw["reason"],
            tally=dict(raw["tally"]),
            dissent=tuple(raw["dissent"]),
        )
    if kind == HoldAccepted.TYPE:
        return HoldAccepted(
            at=raw["at"],
            approval_id=raw["approval_id"],
            call_id=raw["call_id"],
            mcp_server_id=raw["mcp_server_id"],
            tool_name=raw["tool_name"],
            arguments_hash=raw["arguments_hash"],
            expires_at=raw["expires_at"],
            requested_by=raw.get("requested_by"),
            tenant_id=raw.get("tenant_id"),
            correlation_id=str(raw.get("correlation_id") or ""),
        )
    if kind == HoldAdjudicated.TYPE:
        return HoldAdjudicated(
            at=raw["at"],
            call_id=raw["call_id"],
            approval_id=raw["approval_id"],
            mcp_server_id=raw["mcp_server_id"],
            tool_name=raw["tool_name"],
            path=raw["path"],
            approved=bool(raw["approved"]),
            decision_id=raw.get("decision_id"),
            reason=raw["reason"],
            duration_ms=float(raw["duration_ms"]),
            quorum_latency_ms=(
                float(raw["quorum_latency_ms"])
                if raw.get("quorum_latency_ms") is not None
                else None
            ),
            quorum_cost=dict(raw["quorum_cost"]) if raw.get("quorum_cost") else None,
            resolver_principal=raw.get("resolver_principal"),
            requested_by=raw.get("requested_by"),
        )
    if kind == CoverageChecked.TYPE:
        return CoverageChecked(
            at=raw["at"],
            tool=raw["tool"],
            paths=tuple(raw.get("paths") or ()),
            approved=bool(raw["approved"]),
            path=raw["path"],
            decision_id=raw.get("decision_id"),
            reason=raw["reason"],
            actor=raw.get("actor"),
        )
    if kind == BreakGlassUsed.TYPE:
        return BreakGlassUsed(
            at=raw["at"],
            tool=raw["tool"],
            paths=tuple(raw.get("paths") or ()),
            actor=raw["actor"],
            reason=raw["reason"],
        )
    if kind == BaselineVerdict.TYPE:
        return BaselineVerdict(
            at=raw["at"],
            decision_id=raw["decision_id"],
            voter=raw["voter"],
            option=raw.get("option"),
            confidence=raw.get("confidence"),
            kill_criterion=raw.get("kill_criterion"),
            bundle_sha256=raw["bundle_sha256"],
            ok=bool(raw["ok"]),
            reason=raw["reason"],
            meta=_meta(
                raw,
                (
                    "prompt_sha256",
                    "response_sha256",
                    "model",
                    "latency_ms",
                    "prompt_tokens",
                    "completion_tokens",
                ),
            ),
        )
    if kind == DecisionInvalidated.TYPE:
        return DecisionInvalidated(
            at=raw["at"],
            decision_id=raw["decision_id"],
            reason=raw["reason"],
            cascaded_from=raw.get("cascaded_from"),
        )
    if kind == RuleEstablished.TYPE:
        return RuleEstablished(
            at=raw["at"],
            rule_id=raw["rule_id"],
            decision_id=raw["decision_id"],
            kind=raw["kind"],
            path_glob=str(raw.get("path_glob") or ""),
            detail=str(raw.get("detail") or ""),
        )
    return None
