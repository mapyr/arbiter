"""Deterministic narrowing candidate generation.

Voters may only pick from the closed list returned here — never invent values.
"""

from __future__ import annotations

from typing import Any, Mapping

from arbiter.domain.services.option_kind import ALLOW, DENY, ESCALATE, NARROW_PREFIX

# Call classes for which we cannot generate a useful narrowing set → stay binary.
_BINARY_ONLY_TOOLS = frozenset(
    {
        "shell.exec",
        "bash",
        "process.kill",
    }
)


def narrowing_candidates(
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    mcp_server_id: str = "",
    include_escalate: bool = False,
) -> list[str]:
    """Return closed option strings for open_decision (allow/deny + narrowings)."""
    args = dict(arguments or {})
    if tool_name in _BINARY_ONLY_TOOLS:
        options = [ALLOW, DENY]
        if include_escalate:
            options.append(ESCALATE)
        return options

    narrow: list[str] = []
    # Shorter TTL — always available for intercepted writes.
    narrow.append(f"{NARROW_PREFIX}ttl=60")
    # Path subset when a path-like argument is present.
    path = _first_path(args)
    if path is not None:
        parent = _parent_glob(path)
        narrow.append(f"{NARROW_PREFIX}ttl=300;paths={parent}")
        narrow.append(f"{NARROW_PREFIX}ttl=300;paths={path}")
    # Argument subset: drop free-text fields, keep identity keys only.
    keys = sorted(k for k in args if k in ("path", "file", "target", "table", "name"))
    if keys:
        key_list = ",".join(keys)
        narrow.append(f"{NARROW_PREFIX}ttl=120;args={key_list}")

    # Cap so total options stay within Decision.open 2..8.
    # allow + deny + up to 5 narrow (+ optional escalate).
    budget = 5 if not include_escalate else 4
    narrow = narrow[:budget]
    options = [ALLOW, DENY, *narrow]
    if include_escalate:
        options.append(ESCALATE)
    return options


def binary_only_tool(tool_name: str) -> bool:
    return tool_name in _BINARY_ONLY_TOOLS


def _first_path(args: Mapping[str, Any]) -> str | None:
    for key in ("path", "file", "target", "filepath"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\\", "/")
    return None


def _parent_glob(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if "/" not in normalized:
        return "**"
    parent = normalized.rsplit("/", 1)[0]
    return f"{parent}/**" if parent else "**"
