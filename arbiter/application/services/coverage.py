"""Path coverage against resolved allow decisions (single rule source)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from arbiter.application.app import Application
from arbiter.domain.events import BreakGlassUsed, CoverageChecked
from arbiter.domain.services.classify import path_matches
from arbiter.domain.services.scope import uncovered_paths
from arbiter.domain.timeutil import format_iso, parse_iso


def critical_paths(paths: list[str], rules: dict[str, Any] | None) -> list[str]:
    """Return paths that match critical rules (fail-closed: no rules → all paths)."""
    if rules is None:
        return list(paths)
    critical = rules.get("critical") if isinstance(rules, dict) else None
    if not isinstance(critical, dict):
        return list(paths)
    patterns = critical.get("paths") or []
    if not isinstance(patterns, list) or not patterns:
        return []
    out: list[str] = []
    for path in paths:
        for pattern in patterns:
            if isinstance(pattern, str) and path_matches(path, pattern):
                out.append(path)
                break
    return out


class CoverageService:
    def __init__(self, app: Application, *, rules: dict[str, Any] | None) -> None:
        self._app = app
        self._rules = rules

    def check(
        self,
        *,
        paths: list[str],
        tool: str = "edit",
        decision_id: str | None = None,
        actor: str | None = None,
        record: bool = True,
        now: datetime | None = None,
        break_glass: bool = False,
        break_glass_reason: str | None = None,
    ) -> dict[str, Any]:
        moment = now or self._app.now()
        crit = critical_paths(paths, self._rules)
        if not crit:
            result = {
                "approved": True,
                "path": "not_critical",
                "decision_id": None,
                "reason": "no_critical_paths",
                "uncovered": [],
                "critical_paths": [],
            }
            if record:
                self._record_coverage(result, tool=tool, paths=paths, actor=actor, at=moment)
            return result

        if break_glass:
            actor_id = actor or "unknown"
            reason = break_glass_reason or "ARBITER_BREAK_GLASS"
            if record:
                self._app.commands.record_break_glass(
                    BreakGlassUsed(
                        at=format_iso(moment),
                        tool=tool,
                        paths=tuple(crit),
                        actor=actor_id,
                        reason=reason,
                    )
                )
                self._record_coverage(
                    {
                        "approved": True,
                        "path": "break_glass",
                        "decision_id": None,
                        "reason": f"break_glass:{reason}",
                        "uncovered": [],
                    },
                    tool=tool,
                    paths=crit,
                    actor=actor_id,
                    at=moment,
                )
            return {
                "approved": True,
                "path": "break_glass",
                "decision_id": None,
                "reason": f"break_glass:{reason}",
                "uncovered": [],
                "critical_paths": crit,
            }

        if decision_id:
            covered, uncovered, reason = self._evaluate_decision(
                decision_id, crit, moment
            )
            result = {
                "approved": covered,
                "path": "covered" if covered else "deny",
                "decision_id": decision_id,
                "reason": reason,
                "uncovered": uncovered,
                "critical_paths": crit,
            }
            if record:
                self._record_coverage(result, tool=tool, paths=crit, actor=actor, at=moment)
            return result

        for did in self._decision_ids():
            covered, uncovered, reason = self._evaluate_decision(did, crit, moment)
            if covered:
                result = {
                    "approved": True,
                    "path": "covered",
                    "decision_id": did,
                    "reason": reason,
                    "uncovered": [],
                    "critical_paths": crit,
                }
                if record:
                    self._record_coverage(
                        result, tool=tool, paths=crit, actor=actor, at=moment
                    )
                return result

        result = {
            "approved": False,
            "path": "deny",
            "decision_id": None,
            "reason": "no_covering_allow_decision",
            "uncovered": list(crit),
            "critical_paths": crit,
        }
        if record:
            self._record_coverage(result, tool=tool, paths=crit, actor=actor, at=moment)
        return result

    def _evaluate_decision(
        self, decision_id: str, paths: list[str], moment: datetime
    ) -> tuple[bool, list[str], str]:
        state = self._app.replay(decision_id)
        if state is None:
            return False, list(paths), f"unknown_decision:{decision_id}"
        if state.resolution is None:
            return False, list(paths), f"decision_not_resolved:{decision_id}"
        if getattr(state, "invalidated", False):
            return False, list(paths), f"decision_invalidated:{decision_id}"
        if state.resolution.get("verdict") not in ("allow", "allow_narrow"):
            return False, list(paths), f"decision_not_allow:{decision_id}"
        if moment >= parse_iso(state.deadline):
            return False, list(paths), f"decision_expired:{decision_id}"
        missing = uncovered_paths(state.scope, paths)
        if missing:
            return (
                False,
                missing,
                f"scope_incomplete:{decision_id}:uncovered={','.join(missing)}",
            )
        return True, [], f"covered_by:{decision_id}"

    def _decision_ids(self) -> list[str]:
        seen: list[str] = []
        for raw in self._app.read_all_wire():
            if raw.get("event") != "decision.opened":
                continue
            did = raw.get("decision_id")
            if isinstance(did, str) and did not in seen:
                seen.append(did)
        return seen

    def _record_coverage(
        self,
        result: dict[str, Any],
        *,
        tool: str,
        paths: list[str],
        actor: str | None,
        at: datetime,
    ) -> None:
        self._app.commands.record_coverage_checked(
            CoverageChecked(
                at=format_iso(at),
                tool=tool,
                paths=tuple(paths),
                approved=bool(result["approved"]),
                path=str(result["path"]),
                decision_id=result.get("decision_id"),
                reason=str(result["reason"]),
                actor=actor,
            )
        )
