"""Layer 3 — commit must cite a covering allow decision for critical paths."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from arbiter.application.app import Application
from arbiter.application.services.coverage import check_coverage, critical_paths
from arbiter.domain.events import BreakGlassUsed
from arbiter.domain.timeutil import parse_iso

DECISION_TRAILER_RE = re.compile(
    r"(?im)^(?:Arbiter-Decision|Decision):\s*([A-Za-z0-9_-]+)\s*$"
)


def extract_decision_id(commit_message: str) -> str | None:
    match = DECISION_TRAILER_RE.search(commit_message or "")
    return match.group(1) if match else None


def verify_commit(
    app: Application,
    *,
    paths: list[str],
    decision_id: str | None,
    commit_at: datetime | None = None,
    allow_break_glass: bool = False,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    moment = commit_at or app.now()
    crit = critical_paths(paths, rules)
    glass = _break_glass_touching(app, crit)
    if glass and not allow_break_glass:
        return {
            "ok": False,
            "reason": "break_glass_requires_human_ack",
            "critical_paths": crit,
            "break_glass": glass,
            "uncovered": crit,
            "decision_id": decision_id,
        }
    if not crit:
        return {
            "ok": True,
            "reason": "no_critical_paths",
            "critical_paths": [],
            "uncovered": [],
            "decision_id": decision_id,
            "break_glass": glass,
        }
    if not decision_id:
        return {
            "ok": False,
            "reason": "missing_decision_trailer",
            "critical_paths": crit,
            "uncovered": crit,
            "decision_id": None,
            "break_glass": glass,
        }
    result = check_coverage(
        app,
        rules=rules,
        paths=crit,
        tool="commit",
        decision_id=decision_id,
        record=False,
        now=moment,
    )
    if result["approved"]:
        return {
            "ok": True,
            "reason": result["reason"],
            "critical_paths": crit,
            "uncovered": [],
            "decision_id": decision_id,
            "break_glass": glass,
        }
    return {
        "ok": False,
        "reason": result["reason"],
        "critical_paths": crit,
        "uncovered": list(result.get("uncovered") or crit),
        "decision_id": decision_id,
        "break_glass": glass,
    }


def _break_glass_touching(app: Application, paths: list[str]) -> list[dict[str, Any]]:
    if not paths:
        # Still surface any recent break-glass in CI when checking a commit that
        # may have used glass on critical work earlier in the branch tip.
        pass
    hits: list[dict[str, Any]] = []
    path_set = set(paths)
    for raw in app.read_all_wire():
        if raw.get("event") != BreakGlassUsed.TYPE:
            continue
        glass_paths = set(raw.get("paths") or [])
        if path_set and not (glass_paths & path_set):
            continue
        hits.append(
            {
                "at": raw.get("at"),
                "actor": raw.get("actor"),
                "tool": raw.get("tool"),
                "paths": list(raw.get("paths") or []),
                "reason": raw.get("reason"),
            }
        )
    return hits


def commit_time_from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_iso(value)
