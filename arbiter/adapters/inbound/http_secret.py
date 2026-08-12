"""Shared-secret gate for the HTTP transport (not a security model)."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

HEADER_NAME = b"x-arbiter-secret"
REJECT_BODY = b'{"error":"unauthorized"}'
REJECT_STATUS = 401


class SharedSecretASGI:
    """ASGI middleware: require ``X-Arbiter-Secret`` equal to the configured value."""

    def __init__(self, app: Any, secret: str) -> None:
        if not secret:
            raise ValueError("HTTP shared secret must be a non-empty string")
        self.app = app
        self._secret = secret.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        provided = headers.get(HEADER_NAME)
        if provided is None or not hmac.compare_digest(provided, self._secret):
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    async def _reject(self, send: Callable[..., Awaitable[None]]) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": REJECT_STATUS,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(REJECT_BODY)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": REJECT_BODY})
