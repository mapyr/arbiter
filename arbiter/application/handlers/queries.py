"""Query handlers — read models from event stream replay."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from arbiter.application.ports import Clock, EventStore
from arbiter.domain.timeutil import parse_iso


class QueryHandlers:
    def __init__(self, *, events: EventStore, clock: Clock) -> None:
        self._events = events
        self._clock = clock

    def get_decision(
        self, decision_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        state = self._events.load_decision(decision_id)
        moment = now or self._clock.now()
        if state is None:
            return {
                "decision_id": decision_id,
                "status": "unknown",
                "verdict": "deny",
                "reason": "quorum_not_met",
                "missing": [f"decision:{decision_id}"],
                "votes": [],
                "seconds_to_deadline": None,
            }
        deadline = parse_iso(state.deadline)
        seconds_to_deadline = (deadline - moment).total_seconds()
        deadline_passed = moment >= deadline
        status = "resolved" if state.resolution else "open"
        eff = state.effective_votes()
        return {
            "decision_id": decision_id,
            "status": status,
            "question": state.question,
            "options": list(state.options),
            "scope": list(state.scope),
            "mode": state.mode,
            "criticality": state.criticality,
            "criticality_source": state.criticality_source,
            "voters": list(state.voters),
            "bundle_sha256": state.bundle_sha256,
            "deadline": state.deadline,
            "deadline_passed": deadline_passed,
            "seconds_to_deadline": seconds_to_deadline,
            "votes": [
                {
                    "voter": v,
                    "option": eff[v]["option"],
                    "confidence": eff[v]["confidence"],
                    "kill_criterion": eff[v]["kill_criterion"],
                    "round": eff[v]["round"],
                }
                for v in state.voters
                if v in eff
            ],
            "missing_voters": state.missing_voters(),
            "failures": list(state.failures),
            "reveal_labels": state.reveal_labels,
            "resolution": state.resolution,
        }
