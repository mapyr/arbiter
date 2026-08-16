"""Transport contract: parity, deterministic tools/list, identity."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import Client

from arbiter.domain.services.canonical import canonical_json_bytes
from arbiter.adapters.inbound.mcp_server import (
    TOOL_DESCRIPTIONS,
    create_server,
    package_name,
    package_version,
)

pytestmark = pytest.mark.integration


def _tool_contract(tools) -> list[dict]:
    """Canonical comparable projection of a tools/list payload."""
    rows = []
    for t in tools:
        dump = t.model_dump(by_alias=True, exclude_none=True)
        rows.append(
            {
                "name": dump["name"],
                "description": dump.get("description"),
                "inputSchema": dump.get("inputSchema") or dump.get("input_schema"),
                "outputSchema": dump.get("outputSchema") or dump.get("output_schema"),
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.asyncio
async def test_A3_server_identity_matches_package_metadata(server) -> None:
    import importlib.metadata

    assert package_name() == "arbiter"
    assert package_version() == importlib.metadata.version("arbiter")
    async with Client(server) as client:
        tools = await client.list_tools()
        assert tools is not None
    assert server.name == package_name()
    assert server.version == package_version()


@pytest.mark.asyncio
async def test_A2_tools_list_deterministic_across_cwd_and_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tools/list depends only on package version — not cwd or rules file."""

    async def snapshot(cwd: Path, rules_text: str) -> bytes:
        monkeypatch.chdir(cwd)
        data = cwd / "decisions"
        data.mkdir(exist_ok=True)
        rules = cwd / "arbiter.rules.yaml"
        rules.write_text(rules_text, encoding="utf-8")
        monkeypatch.setenv("ARBITER_DATA_DIR", str(data))
        monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))
        srv = create_server()
        async with Client(srv) as client:
            listed = await client.list_tools()
        return canonical_json_bytes(_tool_contract(listed.tools))

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    snap_a = await snapshot(
        a,
        'critical:\n  paths:\n    - "**/auth/**"\n  any_of: []\ndefault: routine\n',
    )
    snap_b = await snapshot(
        b,
        'critical:\n  paths:\n    - "**/totally-different/**"\n  any_of: []\ndefault: critical\n',
    )
    assert snap_a == snap_b
    # Stable alphabetical order
    names = [row["name"] for row in json.loads(snap_a.decode())]
    assert names == sorted(names)
    assert names == sorted(TOOL_DESCRIPTIONS)


@pytest.mark.asyncio
async def test_A1_tools_list_identical_stdio_and_http(tmp_path: Path) -> None:
    """Byte-identical canonical tools/list across stdio (in-proc) and HTTP."""
    data = tmp_path / "decisions"
    data.mkdir()
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(
        'critical:\n  paths:\n    - "**/auth/**"\n  any_of: []\ndefault: routine\n',
        encoding="utf-8",
    )
    secret = "transport-contract-secret"
    env = os.environ.copy()
    env["ARBITER_DATA_DIR"] = str(data)
    env["ARBITER_RULES_PATH"] = str(rules)
    env["ARBITER_HTTP_SECRET"] = secret

    # In-process stdio surface
    monkey_server = create_server()
    async with Client(monkey_server) as client:
        stdio_listed = await client.list_tools()
    stdio_canon = canonical_json_bytes(_tool_contract(stdio_listed.tools))

    port = _free_port()
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
        url = f"http://127.0.0.1:{port}/mcp"
        deadline = time.time() + 15
        last_err: Exception | None = None
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        while time.time() < deadline:
            try:
                async with httpx2.AsyncClient(
                    headers={"X-Arbiter-Secret": secret}
                ) as http:
                    async with Client(
                        streamable_http_client(url, http_client=http)
                    ) as client:
                        http_listed = await client.list_tools()
                break
            except Exception as exc:  # noqa: BLE001 — retry until server is up
                last_err = exc
                await asyncio.sleep(0.1)
        else:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise AssertionError(f"HTTP server did not become ready: {last_err}\n{stderr}")

        http_canon = canonical_json_bytes(_tool_contract(http_listed.tools))
        assert http_canon == stdio_canon
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
