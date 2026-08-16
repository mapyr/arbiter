"""Shared fixtures for arbiter tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from arbiter.adapters.inbound.mcp_server import create_server
from arbiter.application.app import Application
from arbiter.bootstrap import create_application


@pytest.fixture
def tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "decisions"
    data.mkdir()
    monkeypatch.setenv("ARBITER_DATA_DIR", str(data))
    rules_src = Path(__file__).resolve().parents[1] / "arbiter.rules.yaml.example"
    rules_dst = tmp_path / "arbiter.rules.yaml"
    shutil.copy(rules_src, rules_dst)
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules_dst))
    # Do not inherit a demo/host ARBITER_VOTERS_PATH (e.g. from podman env.sh).
    monkeypatch.delenv("ARBITER_VOTERS_PATH", raising=False)
    return tmp_path


@pytest.fixture
def ledger(tmp_cwd: Path) -> Application:
    return create_application(
        root=Path(tmp_cwd) / "decisions",
        rules=Path(tmp_cwd) / "arbiter.rules.yaml",
    )


@pytest.fixture
def server(ledger: Application):
    return create_server(ledger)


def tool_payload(result) -> dict:
    """Extract JSON object from an MCP CallToolResult."""
    assert result.is_error is False, result.content
    if result.structured_content is not None:
        return dict(result.structured_content)
    text = result.content[0].text
    return json.loads(text)


def tool_error_text(result) -> str:
    assert result.is_error is True
    return result.content[0].text
