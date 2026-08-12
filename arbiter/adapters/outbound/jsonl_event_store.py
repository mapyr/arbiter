"""Append-only JSONL event store (ledger.jsonl)."""

from __future__ import annotations

import json
from pathlib import Path

from arbiter.adapters.outbound.event_codec import from_wire, to_wire
from arbiter.domain.events import DomainEvent
from arbiter.domain.model import Decision


class JsonlEventStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, event: DomainEvent) -> None:
        line = json.dumps(to_wire(event), ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def append_all(self, events: list[DomainEvent]) -> None:
        for event in events:
            self.append(event)

    def read_all_wire(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
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
