"""Correlate held tool calls with prior decisions; open quorum only when needed."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from arbiter.application.app import Application
from arbiter.domain.errors import DomainError
from arbiter.domain.events import HoldAccepted, HoldAdjudicated
from arbiter.domain.services.call_identity import call_identity
from arbiter.domain.services.dependencies import (
    dependencies_still_hold,
    dependency_edges_from_wire,
)
from arbiter.domain.services.installed_rules import (
    check_installed_rules,
    rules_from_wire,
)
from arbiter.domain.services.intercept import InterceptRules
from arbiter.domain.services.narrowing import narrowing_candidates
from arbiter.domain.services.option_kind import is_proceed_kind, option_kind, parse_narrow_spec
from arbiter.domain.services.preconditions import check_preconditions
from arbiter.domain.services.scope import call_ref, covers, paths_from_arguments
from arbiter.domain.timeutil import format_iso, parse_iso

# Default floor when voters config is absent; otherwise derived from voter timeouts.
DEFAULT_MIN_ROUND_SECONDS = 15.0
DEFAULT_HOLD_MARGIN_SECONDS = 5.0


@dataclass(frozen=True)
class HeldCall:
    approval_id: str
    mcp_server_id: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str
    expires_at: datetime
    requested_by: str | None = None
    tenant_id: str | None = None
    correlation_id: str = ""
    agent_rationale: str | None = None


@dataclass(frozen=True)
class AdjudicationResult:
    approved: bool
    reason: str
    decision_id: str | None
    path: str
    call_id: str
    duration_ms: float
    quorum_latency_ms: float | None = None
    quorum_cost: dict[str, Any] | None = None


class HoldAdjudicator:
    """Application service: coverage first, then quorum, else deny (I1–I5)."""

    def __init__(
        self,
        app: Application,
        *,
        intercept: InterceptRules,
        resolver_principal: str,
        hold_margin_seconds: float = DEFAULT_HOLD_MARGIN_SECONDS,
        min_round_seconds: float | None = None,
        enable_narrowing: bool = True,
        include_escalate: bool = False,
    ) -> None:
        self._app = app
        self._intercept = intercept
        self._resolver_principal = resolver_principal
        self._hold_margin_seconds = hold_margin_seconds
        self._min_round_seconds_override = min_round_seconds
        self._enable_narrowing = enable_narrowing
        self._include_escalate = include_escalate

    def accept(self, held: HeldCall) -> str:
        """Record immediate receipt (Z1). Returns call_id."""
        call_id = call_identity(
            mcp_server_id=held.mcp_server_id,
            tool_name=held.tool_name,
            arguments_hash=held.arguments_hash,
        )
        event = HoldAccepted(
            at=format_iso(self._app.now()),
            approval_id=held.approval_id,
            call_id=call_id,
            mcp_server_id=held.mcp_server_id,
            tool_name=held.tool_name,
            arguments_hash=held.arguments_hash,
            expires_at=format_iso(held.expires_at),
            requested_by=held.requested_by,
            tenant_id=held.tenant_id,
            correlation_id=held.correlation_id,
        )
        self._app.commands.record_hold_accepted(event)
        return call_id

    async def adjudicate(self, held: HeldCall) -> AdjudicationResult:
        started = time.perf_counter()
        call_id = call_identity(
            mcp_server_id=held.mcp_server_id,
            tool_name=held.tool_name,
            arguments_hash=held.arguments_hash,
        )
        try:
            prior = self._prior_for_call(call_id)
            if prior is not None:
                result = AdjudicationResult(
                    approved=bool(prior["approved"]),
                    reason=str(prior["reason"]),
                    decision_id=prior.get("decision_id"),
                    path="duplicate",
                    call_id=call_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(held, result)
                return result

            if not self._intercept.matches(held.mcp_server_id, held.tool_name):
                result = AdjudicationResult(
                    approved=True,
                    reason="not_intercepted",
                    decision_id=None,
                    path="passthrough",
                    call_id=call_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(held, result)
                return result

            # S6 — installed rules before quorum.
            rule = check_installed_rules(
                rules_from_wire(self._app.read_all_wire()),
                tool_name=held.tool_name,
                arguments=held.arguments,
                wire_events=self._app.read_all_wire(),
            )
            if rule.path == "rule_deny":
                result = AdjudicationResult(
                    approved=False,
                    reason=rule.reason,
                    decision_id=None,
                    path="rule_deny",
                    call_id=call_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(held, result)
                return result
            if rule.path == "rule_allow":
                result = AdjudicationResult(
                    approved=True,
                    reason=rule.reason,
                    decision_id=None,
                    path="rule_allow",
                    call_id=call_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(held, result)
                return result

            # S2 — ledger precondition (pure over events).
            pre = check_preconditions(
                self._app.read_all_wire(),
                tool_name=held.tool_name,
                arguments_hash=held.arguments_hash,
                mcp_server_id=held.mcp_server_id,
            )
            if pre.applicable and not pre.ok:
                result = AdjudicationResult(
                    approved=False,
                    reason=f"precondition:{pre.predicate}:{pre.reason}",
                    decision_id=None,
                    path="precondition_denied",
                    call_id=call_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(held, result)
                return result
            if pre.applicable and pre.ok:
                # Satisfied precondition still needs coverage/quorum for policy,
                # but we record the hit for measurement when callers opt in via
                # meta on later paths. Fall through.
                pass

            covered = self._find_covering_allow(held)
            if covered is not None:
                result = AdjudicationResult(
                    approved=True,
                    reason=f"covered_by:{covered}",
                    decision_id=covered,
                    path="covered",
                    call_id=call_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self._record(held, result)
                return result

            return await self._open_and_quorum(held, call_id, started)
        except Exception as exc:  # noqa: BLE001 — I2 fail closed
            result = AdjudicationResult(
                approved=False,
                reason=f"adapter_error:{type(exc).__name__}:{exc}",
                decision_id=None,
                path="deny",
                call_id=call_id,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            try:
                self._record(held, result)
            except Exception:  # noqa: BLE001
                pass
            return result

    def _quorum_budget_seconds(self, held: HeldCall) -> float:
        """Quorum budget = time-to-expiry minus margin. No dual config."""
        now = self._app.now()
        expires = held.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=now.tzinfo)
        remaining = (expires - now).total_seconds()
        return remaining - self._hold_margin_seconds

    def _min_round_seconds(self) -> float:
        if self._min_round_seconds_override is not None:
            return self._min_round_seconds_override
        config = self._app.load_voters_config()
        if config is None:
            return DEFAULT_MIN_ROUND_SECONDS
        slowest = max(v.timeout_seconds for v in config.voters)
        return max(DEFAULT_MIN_ROUND_SECONDS, slowest + 2.0)

    async def _open_and_quorum(
        self, held: HeldCall, call_id: str, started: float
    ) -> AdjudicationResult:
        budget = self._quorum_budget_seconds(held)
        min_round = self._min_round_seconds()
        if budget < min_round:
            result = AdjudicationResult(
                approved=False,
                reason=(
                    f"insufficient_time_for_quorum:"
                    f"budget_s={budget:.3f}:min_round_s={min_round:.3f}"
                ),
                decision_id=None,
                path="insufficient_time",
                call_id=call_id,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            self._record(held, result)
            return result

        evidence = self._evidence_bundle(held)
        config = self._app.load_voters_config()
        if config is None:
            raise DomainError(
                "arbiter.voters.yaml required for hold adjudication "
                "(missing voters config)"
            )
        voters = config.ids
        shadow = bool(config.shadow_mode) or os.environ.get("ARBITER_SHADOW_MODE") == "1"
        if self._enable_narrowing:
            options = narrowing_candidates(
                tool_name=held.tool_name,
                arguments=held.arguments,
                mcp_server_id=held.mcp_server_id,
                include_escalate=self._include_escalate,
            )
        else:
            options = ["allow", "deny"]
            if self._include_escalate:
                options.append("escalate_to_human")
        paths = paths_from_arguments(held.arguments)
        scope = [call_ref(held.mcp_server_id, held.tool_name), *paths]
        opened = self._app.open_decision(
            question=(
                "Does this held tool call fall within what was previously agreed? "
                f"{call_ref(held.mcp_server_id, held.tool_name)}"
            ),
            options=options,
            voters=voters,
            evidence=evidence,
            criticality="critical",
            ttl_seconds=max(1, int(budget)),
            opened_by="hold_adjudicator",
            scope=scope,
            mode="shadow" if shadow else "enforce",
        )
        decision_id = opened["decision_id"]
        q_started = time.perf_counter()
        resolved = await self._app.run_model_quorum(decision_id)
        quorum_latency_ms = (time.perf_counter() - q_started) * 1000.0
        kind = option_kind(str(resolved.get("chosen_option") or "deny"))
        if resolved.get("verdict") in (
            "allow",
            "deny",
            "allow_narrow",
            "escalate_to_human",
        ):
            kind = str(resolved["verdict"])  # type: ignore[assignment]
        # Escalation never passes (invariant).
        quorum_proceed = is_proceed_kind(kind)  # type: ignore[arg-type]
        if kind == "escalate_to_human":
            quorum_proceed = False
        if opened.get("mode") == "shadow":
            # Shadow: full quorum + baseline recorded; never gates the call.
            approved = True
            reason = (
                f"shadow:{decision_id}:quorum="
                f"{resolved.get('verdict')}/{resolved.get('chosen_option')}"
            )
        else:
            approved = quorum_proceed
            if kind == "escalate_to_human":
                reason = f"decision:{decision_id}:escalate_to_human"
            elif kind == "allow_narrow" and approved:
                spec = parse_narrow_spec(str(resolved.get("chosen_option") or ""))
                reason = f"decision:{decision_id}:allow_narrow:{spec}"
            elif approved:
                reason = f"decision:{decision_id}:allow"
            else:
                reason = (
                    f"decision:{decision_id}:{resolved.get('reason', 'resolved')}"
                )
        cost = {
            "p50_ms": resolved.get("p50_ms"),
            "p95_ms": resolved.get("p95_ms"),
            "verdict": resolved.get("verdict"),
            "chosen_option": resolved.get("chosen_option"),
        }
        result = AdjudicationResult(
            approved=approved,
            reason=reason,
            decision_id=decision_id,
            path="quorum",
            call_id=call_id,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            quorum_latency_ms=quorum_latency_ms,
            quorum_cost=cost,
        )
        self._record(held, result)
        return result

    def _evidence_bundle(self, held: HeldCall) -> dict[str, Any]:
        bundle: dict[str, Any] = {
            "observation": {
                "mcp_server_id": held.mcp_server_id,
                "tool_name": held.tool_name,
                "arguments": held.arguments,
                "arguments_hash": held.arguments_hash,
                "requested_by": held.requested_by,
                "tenant_id": held.tenant_id,
                "correlation_id": held.correlation_id,
            }
        }
        if held.agent_rationale:
            bundle["unverified_claim"] = {
                "kind": "agent_rationale",
                "text": held.agent_rationale,
                "verified": False,
            }
        return bundle

    def _find_covering_allow(self, held: HeldCall) -> str | None:
        now = self._app.now()
        decision_ids = self._decision_ids()
        edges = dependency_edges_from_wire(self._app.read_all_wire())
        invalidated = {
            e["decision_id"]
            for e in self._app.read_all_wire()
            if e.get("event") == "decision.invalidated"
            and isinstance(e.get("decision_id"), str)
        }

        def is_valid_allow(dep_id: str) -> bool:
            if dep_id in invalidated:
                return False
            st = self._app.replay(dep_id)
            if st is None or st.resolution is None or st.invalidated:
                return False
            v = st.resolution.get("verdict")
            if v not in ("allow", "allow_narrow"):
                return False
            if now >= parse_iso(st.deadline):
                return False
            return True

        paths = paths_from_arguments(held.arguments)
        for decision_id in decision_ids:
            if decision_id in invalidated:
                continue
            state = self._app.replay(decision_id)
            if state is None or state.resolution is None or state.invalidated:
                continue
            verdict = state.resolution.get("verdict")
            if verdict not in ("allow", "allow_narrow"):
                continue
            if now >= parse_iso(state.deadline):
                continue
            if not covers(
                state.scope,
                mcp_server_id=held.mcp_server_id,
                tool_name=held.tool_name,
                paths=paths,
            ):
                continue
            # S5 — coverage counted over the dependency graph.
            ok, _bad = dependencies_still_hold(
                decision_id, edges, is_valid_allow=is_valid_allow
            )
            if not ok:
                continue
            # Narrow allows: enforce TTL/path constraints when present.
            if verdict == "allow_narrow":
                spec = parse_narrow_spec(str(state.resolution.get("chosen_option") or ""))
                ttl = spec.get("ttl")
                if ttl and ttl.isdigit():
                    opened_at = parse_iso(state.opened_at)
                    age = (now - opened_at).total_seconds()
                    if age > float(ttl):
                        continue
                path_pat = spec.get("paths")
                if path_pat:
                    from arbiter.domain.services.classify import path_matches

                    if path is None or not path_matches(path, path_pat):
                        continue
            return decision_id
        return None

    def _prior_for_call(self, call_id: str) -> dict[str, Any] | None:
        for raw in self._app.read_all_wire():
            if raw.get("event") != HoldAdjudicated.TYPE:
                continue
            if raw.get("call_id") != call_id:
                continue
            return {
                "approved": raw.get("approved"),
                "reason": raw.get("reason"),
                "decision_id": raw.get("decision_id"),
            }
        return None

    def _decision_ids(self) -> list[str]:
        seen: list[str] = []
        for raw in self._app.read_all_wire():
            if raw.get("event") != "decision.opened":
                continue
            did = raw.get("decision_id")
            if isinstance(did, str) and did not in seen:
                seen.append(did)
        return seen

    def _record(self, held: HeldCall, result: AdjudicationResult) -> None:
        event = HoldAdjudicated(
            at=format_iso(self._app.now()),
            call_id=result.call_id,
            approval_id=held.approval_id,
            mcp_server_id=held.mcp_server_id,
            tool_name=held.tool_name,
            path=result.path,
            approved=result.approved,
            decision_id=result.decision_id,
            reason=result.reason,
            duration_ms=result.duration_ms,
            quorum_latency_ms=result.quorum_latency_ms,
            quorum_cost=result.quorum_cost,
            resolver_principal=self._resolver_principal,
            requested_by=held.requested_by,
        )
        self._app.commands.record_hold_adjudicated(event)
