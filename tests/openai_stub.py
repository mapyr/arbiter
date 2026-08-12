"""Offline OpenAI-compatible stub controlled by per-voter scenarios."""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass, field
from typing import Any, Callable

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class StubScenario:
    """Queue of behaviors per request ``model`` id."""

    scripts: dict[str, list[Callable[[dict[str, Any]], Any]]] = field(
        default_factory=dict
    )
    call_counts: dict[str, int] = field(default_factory=dict)

    def on(self, model: str, *handlers: Callable[[dict[str, Any]], Any]) -> None:
        self.scripts.setdefault(model, []).extend(handlers)

    def _next(self, model: str, body: dict[str, Any]) -> Any:
        self.call_counts[model] = self.call_counts.get(model, 0) + 1
        queue = self.scripts.get(model) or []
        if not queue:
            return _ok_vote("opt-a")
        idx = min(self.call_counts[model] - 1, len(queue) - 1)
        return queue[idx](body)


def _ok_vote(
    option: str,
    *,
    confidence: float = 0.9,
    kill_criterion: str = "Rollback if smoke fails.",
    revision_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "option": option,
        "confidence": confidence,
        "kill_criterion": kill_criterion,
    }
    if revision_reason is not None:
        payload["revision_reason"] = revision_reason
    return {
        "id": "chatcmpl-stub",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def vote_handler(
    option: str,
    *,
    confidence: float = 0.9,
    kill_criterion: str = "Rollback if smoke fails.",
    revision_reason: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    def _h(_body: dict[str, Any]) -> Any:
        return _ok_vote(
            option,
            confidence=confidence,
            kill_criterion=kill_criterion,
            revision_reason=revision_reason,
        )

    return _h


def text_handler(text: str) -> Callable[[dict[str, Any]], Any]:
    def _h(_body: dict[str, Any]) -> Any:
        return {
            "id": "chatcmpl-stub",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    return _h


def status_handler(code: int, body: str = "nope") -> Callable[[dict[str, Any]], Any]:
    def _h(_body: dict[str, Any]) -> Any:
        return Response(body, status_code=code, media_type="text/plain")

    return _h


def delay_handler(
    seconds: float, then: Callable[[dict[str, Any]], Any]
) -> Callable[[dict[str, Any]], Any]:
    def _h(body: dict[str, Any]) -> Any:
        return ("delay", seconds, then(body))

    return _h


def build_app(scenario: StubScenario) -> Starlette:
    async def chat_completions(request: Request) -> Response:
        body = await request.json()
        model = body.get("model", "")
        result = scenario._next(model, body)
        if isinstance(result, Response):
            return result
        if isinstance(result, tuple) and result and result[0] == "delay":
            _, seconds, payload = result
            await asyncio.sleep(float(seconds))
            if isinstance(payload, Response):
                return payload
            return JSONResponse(payload)
        return JSONResponse(result)

    return Starlette(
        routes=[Route("/v1/chat/completions", chat_completions, methods=["POST"])]
    )


@dataclass
class StubServer:
    scenario: StubScenario
    host: str = "127.0.0.1"
    port: int = field(default_factory=free_port)
    _server: uvicorn.Server | None = None
    _task: asyncio.Task | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    async def __aenter__(self) -> StubServer:
        app = build_app(self.scenario)
        config = uvicorn.Config(
            app, host=self.host, port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
