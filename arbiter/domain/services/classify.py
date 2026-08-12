"""Criticality classifier — pure function, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Classification:
    criticality: str  # "critical" | "routine"
    reason: str


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    pattern = pattern.replace("\\", "/")
    if pattern.endswith("/**"):
        pattern = pattern[:-3] + "(?:/.*)?"
    parts: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("(?:/.*)?", i):
            parts.append("(?:/.*)?")
            i += len("(?:/.*)?")
        elif pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def path_match_candidates(path: str) -> list[str]:
    """Return path forms to match against workspace-relative globs.

    OpenCode and similar clients often send absolute paths
    (``/private/tmp/project/auth/handler.py``) while decision scope uses
    project-relative patterns (``auth/**``). For absolute paths only, also
    try each path suffix so relative scope still covers them. Relative
    paths stay anchored (``x/infra/tf`` does not match ``infra/**``).
    """
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    candidates = [normalized]
    if normalized.startswith("/"):
        parts = [p for p in normalized.split("/") if p]
        for i in range(1, len(parts)):
            candidates.append("/".join(parts[i:]))
    elif len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        # Windows absolute: C:/proj/auth/x.py → also auth/x.py, …
        parts = [p for p in normalized[3:].split("/") if p]
        for i in range(len(parts)):
            candidates.append("/".join(parts[i:]))
    return candidates


def path_matches(path: str, pattern: str) -> bool:
    regex = _glob_to_regex(pattern)
    return any(regex.match(candidate) is not None for candidate in path_match_candidates(path))


def classify(
    evidence: dict[str, Any],
    rules: dict[str, Any] | None,
) -> Classification:
    """Classify evidence criticality (fail-closed when rules missing/invalid)."""
    if rules is None:
        return Classification("critical", "no rules file")

    paths = evidence.get("paths")
    if not isinstance(paths, list) or len(paths) == 0:
        return Classification("critical", "no paths declared")

    critical = rules.get("critical")
    if critical is None:
        critical = {}
    if not isinstance(critical, dict):
        return Classification("critical", "invalid rules")

    path_patterns = critical.get("paths") or []
    if not isinstance(path_patterns, list):
        return Classification("critical", "invalid rules")
    for path in paths:
        if not isinstance(path, str):
            continue
        for pattern in path_patterns:
            if isinstance(pattern, str) and path_matches(path, pattern):
                return Classification("critical", f"path matched {pattern}")

    any_of = critical.get("any_of") or []
    if not isinstance(any_of, list):
        return Classification("critical", "invalid rules")
    for flag_entry in any_of:
        if not isinstance(flag_entry, dict):
            continue
        for flag_name, expected in flag_entry.items():
            if evidence.get(flag_name) == expected:
                return Classification("critical", f"flag {flag_name}")

    default = rules.get("default", "routine")
    if default not in ("critical", "routine"):
        return Classification("critical", "invalid rules")
    return Classification(default, "default")


def apply_criticality(
    classification: Classification, caller: str | None
) -> tuple[str, str]:
    """Merge classifier result with optional caller escalation."""
    from arbiter.domain.errors import DomainError

    base = classification.criticality
    if caller is None:
        return base, "classifier"
    if caller not in ("critical", "routine"):
        raise DomainError("criticality must be 'critical' or 'routine'")
    if base == "routine" and caller == "critical":
        return "critical", "caller_escalated"
    return base, "classifier"
