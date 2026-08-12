"""Outbound ports (driven interfaces)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from arbiter.domain.events import DomainEvent
from arbiter.domain.model import Decision


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_decision_id(self) -> str: ...


class EventStore(Protocol):
    def append(self, event: DomainEvent) -> None: ...

    def append_all(self, events: list[DomainEvent]) -> None: ...

    def load_stream(self, decision_id: str) -> list[DomainEvent]: ...

    def load_decision(self, decision_id: str) -> Decision | None: ...

    def read_all_wire(self) -> list[dict[str, Any]]: ...


class EvidenceStore(Protocol):
    def store(self, evidence: dict[str, Any]) -> str: ...

    def load(self, digest: str) -> dict[str, Any]: ...


class ResponseStore(Protocol):
    def store(
        self,
        *,
        decision_id: str,
        voter: str,
        round_n: int,
        payload: dict[str, Any],
    ) -> str: ...


class RulesSource(Protocol):
    def load(self) -> dict[str, Any] | None: ...


class VotersConfigSource(Protocol):
    def load(self) -> Any | None:
        """Return VotersConfig or None when file absent."""
        ...


class VoterCompletion(Protocol):
    ok: bool
    text: str | None
    raw: dict[str, Any] | None
    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None


class VoterGateway(Protocol):
    async def complete(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        messages: list[dict[str, str]],
        api_key: str | None,
    ) -> VoterCompletion: ...
