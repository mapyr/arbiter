"""GET /health reflects ledger writability; stays outside the HTTP secret."""

from __future__ import annotations

from pathlib import Path

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
async def test_health_ok_without_secret(tmp_cwd: Path) -> None:
    inner = SharedSecretASGI(_ok_app, "s3cret")
    app = _with_health_route(inner)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        health = await client.get("/health")
        denied = await client.get("/mcp")
    assert health.status_code == 200
    assert health.content == b"ok"
    assert denied.status_code == 401
