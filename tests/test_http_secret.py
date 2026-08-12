"""Part 0.1 — HTTP shared-secret gate (not a security model)."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from arbiter.http_secret import REJECT_BODY, REJECT_STATUS, SharedSecretASGI  # noqa: E501

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.asyncio
async def test_missing_and_wrong_secret_same_rejection() -> None:
    async def ok_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    app = SharedSecretASGI(ok_app, "s3cret")
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        missing = await client.get("/mcp")
        wrong = await client.get("/mcp", headers={"X-Arbiter-Secret": "nope"})
        good = await client.get("/mcp", headers={"X-Arbiter-Secret": "s3cret"})

    assert missing.status_code == REJECT_STATUS
    assert wrong.status_code == REJECT_STATUS
    assert missing.content == wrong.content == REJECT_BODY
    assert good.status_code == 200
    assert good.content == b"ok"


@pytest.mark.asyncio
async def test_http_serve_requires_secret_env(tmp_path: Path) -> None:
    port = _free_port()
    env = os.environ.copy()
    env["ARBITER_DATA_DIR"] = str(tmp_path / "decisions")
    env.pop("ARBITER_HTTP_SECRET", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "arbiter",
            "serve",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        code = proc.wait(timeout=10)
        assert code != 0
        err = (proc.stderr.read() if proc.stderr else b"").decode()
        assert "ARBITER_HTTP_SECRET" in err
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.asyncio
async def test_http_mcp_accepts_secret_header(tmp_path: Path) -> None:
    data = tmp_path / "decisions"
    data.mkdir()
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(
        'critical:\n  paths: []\n  any_of: []\ndefault: routine\n',
        encoding="utf-8",
    )
    secret = "test-secret-value"
    port = _free_port()
    env = os.environ.copy()
    env["ARBITER_DATA_DIR"] = str(data)
    env["ARBITER_RULES_PATH"] = str(rules)
    env["ARBITER_HTTP_SECRET"] = secret
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "arbiter",
            "serve",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.time() + 15
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                async with httpx2.AsyncClient(
                    headers={"X-Arbiter-Secret": secret}
                ) as http:
                    async with Client(
                        streamable_http_client(url, http_client=http)
                    ) as client:
                        tools = await client.list_tools()
                        assert any(t.name == "open_decision" for t in tools.tools)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                await asyncio.sleep(0.1)
        else:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise AssertionError(f"HTTP not ready: {last_err}\n{stderr}")

        # Missing secret must not reach MCP
        async with httpx2.AsyncClient() as bare:
            denied = await bare.get(url)
        assert denied.status_code == REJECT_STATUS
        assert denied.content == REJECT_BODY
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
