"""MCP tool surface — maps tools to CQRS handlers."""

from __future__ import annotations

import importlib.metadata
from typing import Any

from mcp.server.mcpserver import MCPServer

from arbiter.application.app import Application
from arbiter.domain.errors import DomainError

TOOL_DESCRIPTIONS: dict[str, str] = {
    "cast_vote": "Cast an immutable vote for a rostered voter on a closed option.",
    "check_coverage": (
        "Ask whether workspace paths are covered by a resolved allow decision "
        "(stage-5 client gate; classifier stays on the arbiter)."
    ),
    "ensure_plan": (
        "Validate a structured work plan, open a decision, run model quorum, "
        "and return whether mutations under plan.scope may proceed."
    ),
    "get_decision": "Read decision state recomputed from the ledger (no mutation).",
    "get_gate_policy": (
        "Return client_gate policy from arbiter.rules.yaml "
        "(plan mode: session | on_uncovered; which Hangar MCP server name)."
    ),
    "open_decision": "Open a decision with a closed option set and voter roster.",
    "resolve_decision": "Resolve a decision against quorum rules; idempotent.",
    "run_model_quorum": (
        "Collect model votes for an open decision via the blind quorum protocol."
    ),
}


def package_name() -> str:
    return "arbiter"


def package_version() -> str:
    return importlib.metadata.version("arbiter")


def create_server(app: Application | None = None) -> MCPServer:
    """Build the MCP server. Tools registered in sorted name order."""
    from arbiter.bootstrap import create_application

    store = app if app is not None else create_application()
    server = MCPServer(package_name(), version=package_version())

    @server.tool(
        name="check_coverage", description=TOOL_DESCRIPTIONS["check_coverage"]
    )
    def check_coverage(
        paths: list[str],
        tool: str = "edit",
        decision_id: str | None = None,
        actor: str | None = None,
        break_glass: bool = False,
        break_glass_reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return store.check_coverage(
                paths=paths,
                tool=tool,
                decision_id=decision_id,
                actor=actor,
                break_glass=break_glass,
                break_glass_reason=break_glass_reason,
            )
        except DomainError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(name="cast_vote", description=TOOL_DESCRIPTIONS["cast_vote"])
    def cast_vote(
        decision_id: str,
        voter: str,
        option: str,
        confidence: float,
        kill_criterion: str,
        bundle_sha256: str,
        round: int = 1,
        revision_reason: str | None = None,
    ) -> dict[str, Any]:
        try:
            return store.cast_vote(
                decision_id=decision_id,
                voter=voter,
                option=option,
                confidence=confidence,
                kill_criterion=kill_criterion,
                bundle_sha256_hex=bundle_sha256,
                round=round,
                revision_reason=revision_reason,
            )
        except DomainError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(name="get_decision", description=TOOL_DESCRIPTIONS["get_decision"])
    def get_decision(decision_id: str) -> dict[str, Any]:
        return store.get_decision(decision_id)

    @server.tool(
        name="get_gate_policy", description=TOOL_DESCRIPTIONS["get_gate_policy"]
    )
    def get_gate_policy() -> dict[str, Any]:
        try:
            return store.get_gate_policy()
        except DomainError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(name="ensure_plan", description=TOOL_DESCRIPTIONS["ensure_plan"])
    async def ensure_plan(
        plan: dict[str, Any],
        ttl_seconds: int = 900,
        criticality: str | None = None,
        voters: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            return await store.ensure_plan(
                plan,
                ttl_seconds=ttl_seconds,
                criticality=criticality,
                voters=voters,
            )
        except DomainError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(name="open_decision", description=TOOL_DESCRIPTIONS["open_decision"])
    def open_decision(
        question: str,
        options: list[str],
        voters: list[str],
        evidence: dict[str, Any],
        criticality: str | None = None,
        ttl_seconds: int = 900,
        scope: list[str] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        try:
            return store.open_decision(
                question=question,
                options=options,
                voters=voters,
                evidence=evidence,
                criticality=criticality,
                ttl_seconds=ttl_seconds,
                scope=scope,
                mode=mode,
            )
        except DomainError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(
        name="resolve_decision", description=TOOL_DESCRIPTIONS["resolve_decision"]
    )
    def resolve_decision(decision_id: str) -> dict[str, Any]:
        return store.resolve_decision(decision_id)

    @server.tool(
        name="run_model_quorum", description=TOOL_DESCRIPTIONS["run_model_quorum"]
    )
    async def run_model_quorum_tool(decision_id: str) -> dict[str, Any]:
        try:
            return await store.run_model_quorum(decision_id)
        except DomainError as exc:
            raise ValueError(str(exc)) from exc

    return server
