"""Append-only JSONL event store (ledger.jsonl)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from arbiter.adapters.outbound.event_codec import from_wire, to_wire
from arbiter.domain.events import DomainEvent
from arbiter.domain.model import Decision


def _flock(fh: object, *, exclusive: bool) -> None:
    import fcntl

    fcntl.flock(
        fh.fileno(),  # type: ignore[attr-defined]
        fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
    )


def ledger_writable(path: Path) -> bool:
    """True when the ledger file can be opened, locked, and fsynced (no new event)."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            _flock(fh, exclusive=True)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except OSError:
        return False


class JsonlEventStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, event: DomainEvent) -> None:
        self.append_all([event])

    def append_all(self, events: list[DomainEvent]) -> None:
        if not events:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            _flock(fh, exclusive=True)
            for event in events:
                line = json.dumps(
                    to_wire(event), ensure_ascii=False, separators=(",", ":")
                )
                fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_all_wire(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            _flock(fh, exclusive=False)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    def load_stream(self, decision_id: str) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for raw in self.read_all_wire():
            if raw.get("decision_id") != decision_id:
                continue
            event = from_wire(raw)
            if event is not None:
                events.append(event)
        return events

    def load_decision(self, decision_id: str) -> Decision | None:
        return Decision.from_events(self.load_stream(decision_id))
