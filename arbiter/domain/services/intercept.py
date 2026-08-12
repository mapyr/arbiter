"""Intercept rule matching — which upstream calls arbiter holds."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class InterceptRule:
    """One allow-list entry: hold calls matching server and tool patterns."""

    mcp_server: str
    tool: str

    def matches(self, mcp_server_id: str, tool_name: str) -> bool:
        return fnmatch.fnmatchcase(mcp_server_id, self.mcp_server) and fnmatch.fnmatchcase(
            tool_name, self.tool
        )


@dataclass(frozen=True)
class InterceptRules:
    rules: tuple[InterceptRule, ...]

    def matches(self, mcp_server_id: str, tool_name: str) -> bool:
        return any(r.matches(mcp_server_id, tool_name) for r in self.rules)
