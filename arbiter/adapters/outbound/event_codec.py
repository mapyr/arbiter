"""Map domain events ↔ ledger.jsonl dicts (wire-compatible)."""

from __future__ import annotations

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


def to_wire(event: DomainEvent) -> dict[str, Any]:
    if isinstance(event, DecisionOpened):
        payload = {
            "event": DecisionOpened.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "question": event.question,
            "options": list(event.options),
            "criticality": event.criticality,
            "criticality_source": event.criticality_source,
            "voters": list(event.voters),
            "bundle_sha256": event.bundle_sha256,
            "deadline": event.deadline,
            "opened_by": event.opened_by,
        }
        if event.scope:
            payload["scope"] = list(event.scope)
        # Mode is always written so shadow cannot be mistaken for enforcement.
        payload["mode"] = event.mode
        if event.depends_on:
            payload["depends_on"] = list(event.depends_on)
        if event.establishes_rule is not None:
            payload["establishes_rule"] = dict(event.establishes_rule)
        return payload
    if isinstance(event, VoteCast):
        payload: dict[str, Any] = {
            "event": VoteCast.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "voter": event.voter,
            "option": event.option,
            "confidence": event.confidence,
            "kill_criterion": event.kill_criterion,
            "bundle_sha256": event.bundle_sha256,
            "round": event.round,
        }
        if event.revision_reason is not None:
            payload["revision_reason"] = event.revision_reason
        for key in _META_KEYS:
            if key in event.meta and event.meta[key] is not None:
                payload[key] = event.meta[key]
        return payload
    if isinstance(event, VoteFailed):
        payload = {
            "event": VoteFailed.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "voter": event.voter,
            "round": event.round,
            "reason": event.reason,
        }
        if event.detail:
            payload["detail"] = event.detail
        for key in ("prompt_sha256", "model", "latency_ms"):
            if key in event.meta and event.meta[key] is not None:
                payload[key] = event.meta[key]
        return payload
    if isinstance(event, QuorumRound2Opened):
        return {
            "event": QuorumRound2Opened.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "labels": dict(event.labels),
        }
    if isinstance(event, DecisionResolved):
        return {
            "event": DecisionResolved.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "verdict": event.verdict,
            "chosen_option": event.chosen_option,
            "reason": event.reason,
            "tally": dict(event.tally),
            "dissent": list(event.dissent),
        }
    if isinstance(event, HoldAccepted):
        payload = {
            "event": HoldAccepted.TYPE,
            "at": event.at,
            "approval_id": event.approval_id,
            "call_id": event.call_id,
            "mcp_server_id": event.mcp_server_id,
            "tool_name": event.tool_name,
            "arguments_hash": event.arguments_hash,
            "expires_at": event.expires_at,
        }
        if event.requested_by is not None:
            payload["requested_by"] = event.requested_by
        if event.tenant_id is not None:
            payload["tenant_id"] = event.tenant_id
        if event.correlation_id:
            payload["correlation_id"] = event.correlation_id
        return payload
    if isinstance(event, HoldAdjudicated):
        payload = {
            "event": HoldAdjudicated.TYPE,
            "at": event.at,
            "call_id": event.call_id,
            "approval_id": event.approval_id,
            "mcp_server_id": event.mcp_server_id,
            "tool_name": event.tool_name,
            "path": event.path,
            "approved": event.approved,
            "reason": event.reason,
            "duration_ms": event.duration_ms,
        }
        if event.decision_id is not None:
            payload["decision_id"] = event.decision_id
        if event.quorum_latency_ms is not None:
            payload["quorum_latency_ms"] = event.quorum_latency_ms
        if event.quorum_cost is not None:
            payload["quorum_cost"] = dict(event.quorum_cost)
        if event.resolver_principal is not None:
            payload["resolver_principal"] = event.resolver_principal
        if event.requested_by is not None:
            payload["requested_by"] = event.requested_by
        for key, value in event.meta.items():
            if value is not None:
                payload[key] = value
        return payload
    if isinstance(event, CoverageChecked):
        payload = {
            "event": CoverageChecked.TYPE,
            "at": event.at,
            "tool": event.tool,
            "paths": list(event.paths),
            "approved": event.approved,
            "path": event.path,
            "reason": event.reason,
        }
        if event.decision_id is not None:
            payload["decision_id"] = event.decision_id
        if event.actor is not None:
            payload["actor"] = event.actor
        return payload
    if isinstance(event, BreakGlassUsed):
        return {
            "event": BreakGlassUsed.TYPE,
            "at": event.at,
            "tool": event.tool,
            "paths": list(event.paths),
            "actor": event.actor,
            "reason": event.reason,
        }
    if isinstance(event, BaselineVerdict):
        payload = {
            "event": BaselineVerdict.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "voter": event.voter,
            "bundle_sha256": event.bundle_sha256,
            "ok": event.ok,
            "reason": event.reason,
        }
        if event.option is not None:
            payload["option"] = event.option
        if event.confidence is not None:
            payload["confidence"] = event.confidence
        if event.kill_criterion is not None:
            payload["kill_criterion"] = event.kill_criterion
        for key in (
            "prompt_sha256",
            "response_sha256",
            "model",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
        ):
            if key in event.meta and event.meta[key] is not None:
                payload[key] = event.meta[key]
        return payload
    if isinstance(event, DecisionInvalidated):
        payload = {
            "event": DecisionInvalidated.TYPE,
            "at": event.at,
            "decision_id": event.decision_id,
            "reason": event.reason,
        }
        if event.cascaded_from is not None:
            payload["cascaded_from"] = event.cascaded_from
        return payload
    if isinstance(event, RuleEstablished):
        return {
            "event": RuleEstablished.TYPE,
            "at": event.at,
            "rule_id": event.rule_id,
            "decision_id": event.decision_id,
            "kind": event.kind,
            "path_glob": event.path_glob,
            "detail": event.detail,
        }
    raise TypeError(f"unknown domain event: {type(event)!r}")


def from_wire(raw: dict[str, Any]) -> DomainEvent | None:
    kind = raw.get("event")
    if kind == DecisionOpened.TYPE:
        scope_raw = raw.get("scope") or ()
        deps_raw = raw.get("depends_on") or ()
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
            scope=tuple(scope_raw),
            mode=str(raw.get("mode") or "enforce"),
            depends_on=tuple(deps_raw),
            establishes_rule=dict(est) if isinstance(est, dict) else None,
        )
    if kind == VoteCast.TYPE:
        meta = {k: raw[k] for k in _META_KEYS if k in raw}
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
            meta=meta,
        )
    if kind == VoteFailed.TYPE:
        meta = {
            k: raw[k]
            for k in ("prompt_sha256", "model", "latency_ms")
            if k in raw
        }
        return VoteFailed(
            at=raw["at"],
            decision_id=raw["decision_id"],
            voter=raw["voter"],
            round=int(raw.get("round", 1)),
            reason=raw["reason"],
            detail=raw.get("detail"),
            meta=meta,
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
            meta={
                k: raw[k]
                for k in (
                    "prompt_sha256",
                    "response_sha256",
                    "model",
                    "latency_ms",
                    "prompt_tokens",
                    "completion_tokens",
                )
                if k in raw
            },
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
