"""Read ``client_gate`` policy from arbiter.rules.yaml (plugin asks via MCP)."""

from __future__ import annotations

from typing import Any, Literal

from arbiter.domain.errors import DomainError

PlanGateMode = Literal["session", "on_uncovered"]

DEFAULT_PLAN_MODE: PlanGateMode = "on_uncovered"
DEFAULT_ARBITER_MCP_SERVER = "arbiter"


def parse_client_gate(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Return normalized client_gate policy (fail-open defaults for missing block)."""
    raw = (rules or {}).get("client_gate") if isinstance(rules, dict) else None
    plan_raw: dict[str, Any] = {}
    if isinstance(raw, dict) and isinstance(raw.get("plan"), dict):
        plan_raw = raw["plan"]

    mode_raw = plan_raw.get("mode", DEFAULT_PLAN_MODE)
    if not isinstance(mode_raw, str):
        raise DomainError(
            "client_gate.plan.mode must be 'session' or 'on_uncovered'"
        )
    mode = mode_raw.strip()
    if mode not in ("session", "on_uncovered"):
        raise DomainError(
            "client_gate.plan.mode must be 'session' or 'on_uncovered'"
        )
    server = plan_raw.get("arbiter_mcp_server", DEFAULT_ARBITER_MCP_SERVER)
    if not isinstance(server, str) or not server.strip():
        raise DomainError("client_gate.plan.arbiter_mcp_server must be a non-empty string")

    return {
        "plan": {
            "mode": mode,
            "arbiter_mcp_server": server.strip(),
        }
    }
