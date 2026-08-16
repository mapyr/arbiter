"""Intercept rule matching — which upstream calls arbiter holds."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from arbiter.domain.errors import DomainError


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


def parse_intercept_rules(raw: Any) -> InterceptRules:
    if not isinstance(raw, dict):
        raise DomainError("intercept rules must be a mapping")
    rules_raw = raw.get("hold")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise DomainError("intercept rules must declare a non-empty hold list")
    rules: list[InterceptRule] = []
    for item in rules_raw:
        if not isinstance(item, dict):
            raise DomainError("each hold rule must be a mapping")
        server = item.get("mcp_server")
        tool = item.get("tool")
        if not isinstance(server, str) or not server.strip():
            raise DomainError("hold rule mcp_server must be a non-empty string")
        if not isinstance(tool, str) or not tool.strip():
            raise DomainError("hold rule tool must be a non-empty string")
        rules.append(InterceptRule(mcp_server=server.strip(), tool=tool.strip()))
    return InterceptRules(rules=tuple(rules))
