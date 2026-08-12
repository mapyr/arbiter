"""Arbiter-only probe execution. Voters never run probes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from arbiter.application.handlers.commands import CommandHandlers
from arbiter.application.ports import Clock, EventStore
from arbiter.domain.errors import DomainError
from arbiter.domain.events import ProbeCompleted, ProbeRequested
from arbiter.domain.services.probes import (
    MAX_PROBE_RESULT_BYTES,
    MAX_PROBES_PER_VOTER_PER_ROUND,
    PROBE_SHOW_FILE,
    PROBE_SHOW_PATH_HISTORY,
    PROBE_SHOW_PRIOR_DECISIONS,
    PROBE_SHOW_TEST_SUMMARY,
    ProbeRequest,
    assert_probe_budget,
    probe_result_digest,
    truncate_probe_result,
)
from arbiter.domain.timeutil import format_iso


class ProbeExecutor:
    """Execute closed-catalog probes; results are ledger-backed for replay."""

    def __init__(
        self,
        *,
        commands: CommandHandlers,
        events: EventStore,
        clock: Clock,
        file_contents: Mapping[str, str] | None = None,
        test_summary: str = "",
        path_histories: Mapping[str, str] | None = None,
        enabled: bool = False,
        replay_only: bool = False,
    ) -> None:
        self._commands = commands
        self._events = events
        self._clock = clock
        self._files = dict(file_contents or {})
        self._test_summary = test_summary
        self._histories = dict(path_histories or {})
        self.enabled = enabled
        self.replay_only = replay_only
        self.executions = 0  # live executions (must stay 0 on replay)

    def stored_results(
        self, decision_id: str, *, voter: str | None = None, round_n: int | None = None
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in self._events.read_all_wire():
            if raw.get("event") != ProbeCompleted.TYPE:
                continue
            if raw.get("decision_id") != decision_id:
                continue
            if voter is not None and raw.get("voter") != voter:
                continue
            if round_n is not None and int(raw.get("round", 1)) != round_n:
                continue
            out.append(
                {
                    "voter": raw["voter"],
                    "round": int(raw.get("round", 1)),
                    "probe": raw["probe"],
                    "params": dict(raw.get("params") or {}),
                    "result_sha256": raw["result_sha256"],
                    "result_text": raw.get("result_text") or "",
                    "truncated": bool(raw.get("truncated")),
                }
            )
        return out

    def run(self, decision_id: str, request: ProbeRequest) -> dict[str, Any]:
        if not self.enabled and not self.replay_only:
            raise DomainError("probes disabled")

        prior = self.stored_results(
            decision_id, voter=request.voter, round_n=request.round
        )
        assert_probe_budget(len(prior), max_n=MAX_PROBES_PER_VOTER_PER_ROUND)

        # Replay path: never re-execute — refuse if result not already stored
        # for an identical request (caller should use stored_results instead).
        if self.replay_only:
            for row in prior:
                if row["probe"] == request.probe and row["params"] == request.params:
                    return row
            raise DomainError(
                "probe replay missing stored result; refusing re-execution"
            )

        moment = self._clock.now()
        self._commands.record_probe_requested(
            ProbeRequested(
                at=format_iso(moment),
                decision_id=decision_id,
                voter=request.voter,
                round=request.round,
                probe=request.probe,
                params=dict(request.params),
            )
        )
        text, truncated = self._execute(request)
        digest = probe_result_digest(text)
        completed = ProbeCompleted(
            at=format_iso(self._clock.now()),
            decision_id=decision_id,
            voter=request.voter,
            round=request.round,
            probe=request.probe,
            params=dict(request.params),
            result_sha256=digest,
            result_text=text,
            truncated=truncated,
        )
        self._commands.record_probe_completed(completed)
        self.executions += 1
        return {
            "voter": request.voter,
            "round": request.round,
            "probe": request.probe,
            "params": dict(request.params),
            "result_sha256": digest,
            "result_text": text,
            "truncated": truncated,
        }

    def _execute(self, request: ProbeRequest) -> tuple[str, bool]:
        if request.probe == PROBE_SHOW_FILE:
            path = request.params["path"]
            raw = self._files.get(path, f"<missing file {path}>")
        elif request.probe == PROBE_SHOW_TEST_SUMMARY:
            raw = self._test_summary or "<no test summary>"
        elif request.probe == PROBE_SHOW_PATH_HISTORY:
            path = request.params["path"]
            raw = self._histories.get(path, f"<no history for {path}>")
        elif request.probe == PROBE_SHOW_PRIOR_DECISIONS:
            raw = self._prior_decisions_text(request.params.get("scope", ""))
        else:
            raise DomainError(f"unknown probe {request.probe!r}")
        truncated_text = truncate_probe_result(raw, max_bytes=MAX_PROBE_RESULT_BYTES)
        truncated = truncated_text != raw
        return truncated_text, truncated

    def _prior_decisions_text(self, scope_filter: str) -> str:
        lines: list[str] = []
        for raw in self._events.read_all_wire():
            if raw.get("event") != "decision.resolved":
                continue
            did = raw.get("decision_id")
            line = (
                f"{did}: verdict={raw.get('verdict')} "
                f"option={raw.get('chosen_option')}"
            )
            if scope_filter and scope_filter not in line:
                continue
            lines.append(line)
        return "\n".join(lines) if lines else "<no prior decisions>"


def material_probe_rows(
    stored: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Stable rows for prompt material (ordered as stored)."""
    return [
        {
            "probe": r["probe"],
            "params": dict(r["params"]),
            "result_sha256": r["result_sha256"],
            "result_text": r["result_text"],
        }
        for r in stored
    ]
