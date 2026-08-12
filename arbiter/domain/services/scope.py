"""Decision scope patterns — tool calls and workspace paths a decision covers."""

from __future__ import annotations

import fnmatch

from arbiter.domain.errors import DomainError
from arbiter.domain.services.classify import path_matches


def normalize_scope_patterns(patterns: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if patterns is None:
        return ()
    if not isinstance(patterns, (list, tuple)):
        raise DomainError("scope must be a list of patterns")
    out: list[str] = []
    for item in patterns:
        if not isinstance(item, str) or not item.strip():
            raise DomainError("scope patterns must be non-empty strings")
        out.append(item.strip())
    return tuple(out)


def call_ref(mcp_server_id: str, tool_name: str) -> str:
    return f"{mcp_server_id}/{tool_name}"


def scope_covers(scope: list[str] | tuple[str, ...], mcp_server_id: str, tool_name: str) -> bool:
    """Return True if any pattern matches ``server/tool`` (fnmatch, case-sensitive)."""
    target = call_ref(mcp_server_id, tool_name)
    for pattern in scope:
        if fnmatch.fnmatchcase(target, pattern):
            return True
        # Also allow patterns that omit the slash and match tool only / server only
        if "/" not in pattern and (
            fnmatch.fnmatchcase(tool_name, pattern)
            or fnmatch.fnmatchcase(mcp_server_id, pattern)
        ):
            return True
    return False


def scope_covers_path(scope: list[str] | tuple[str, ...], path: str) -> bool:
    """Return True if any scope pattern covers a workspace file path."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    for pattern in scope:
        if path_matches(normalized, pattern) or fnmatch.fnmatchcase(normalized, pattern):
            return True
    return False


def uncovered_paths(
    scope: list[str] | tuple[str, ...], paths: list[str]
) -> list[str]:
    return [p for p in paths if not scope_covers_path(scope, p)]
