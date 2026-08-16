"""GET /health reflects ledger writability; stays outside the HTTP secret."""

from __future__ import annotations

import pytest
import httpx2

from arbiter.adapters.inbound.cli import _with_health_route
from arbiter.adapters.inbound.http_secret import SharedSecretASGI


async def _ok_app(scope, receive, send):
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"inner"})


@pytest.mark.asyncio
async def test_health_ok_without_secret() -> None:
    inner = SharedSecretASGI(_ok_app, "s3cret")
    app = _with_health_route(inner, ready=lambda: True)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        health = await client.get("/health")
        denied = await client.get("/mcp")
    assert health.status_code == 200
    assert health.content == b"ok"
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_health_unready_is_503() -> None:
    app = _with_health_route(_ok_app, ready=lambda: False)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        health = await client.get("/health")
        inner = await client.get("/mcp")
    assert health.status_code == 503
    assert health.content == b"unready"
    assert inner.status_code == 200
