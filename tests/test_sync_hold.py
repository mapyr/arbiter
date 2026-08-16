"""Second interceptor — sync hold writes the same ledger events as Hangar delivery."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from arbiter.adapters.inbound.sync_hold import run_hold
from arbiter.bootstrap import create_application
from tests.test_hold_adjudication import (
    FakeApprovalRequest,
    HoldHarness,
    PRINCIPAL,
    create_delivery_for_tests,
    _adj,
    _write_intercept,
)

HOOK = Path(__file__).resolve().parents[1] / "client" / "cursor" / "hooks" / "arbiter-hold.py"


def _events(app) -> list[dict]:
    return app.read_all_wire()


def _hold_pair(app) -> tuple[dict, dict]:
    accepted = [e for e in _events(app) if e["event"] == "hold.accepted"]
    adjudicated = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert accepted and adjudicated
    return accepted[0], adjudicated[0]


@pytest.mark.asyncio
async def test_sync_hold_passthrough_matches_hangar_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intercept = tmp_path / "arbiter.intercept.yaml"
    _write_intercept(intercept)
    data_h = tmp_path / "hangar"
    data_s = tmp_path / "sync"
    monkeypatch.setenv("ARBITER_HANGAR_PRINCIPAL_ID", PRINCIPAL)
    app_h = create_application(root=data_h, rules=tmp_path / "arbiter.rules.yaml")
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=_adj(app_h, intercept),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app_h,
        )
    )
    await harness.run(
        FakeApprovalRequest(
            approval_id="h1",
            mcp_server_id="weather",
            tool_name="forecast",
            arguments={},
        )
    )
    monkeypatch.setenv("ARBITER_DATA_DIR", str(data_s))
    monkeypatch.setenv("ARBITER_INTERCEPT_PATH", str(intercept))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(tmp_path / "arbiter.rules.yaml"))
    result = await run_hold(mcp_server="weather", tool="forecast", arguments={})
    app_s = create_application(root=data_s, rules=tmp_path / "arbiter.rules.yaml")
    _, adj_h = _hold_pair(app_h)
    _, adj_s = _hold_pair(app_s)
    assert result["approved"] is True
    assert adj_h["path"] == adj_s["path"] == "passthrough"
    assert adj_h["approved"] is adj_s["approved"] is True
    assert {e["event"] for e in _events(app_h) if e["event"].startswith("hold.")} == {
        "hold.accepted",
        "hold.adjudicated",
    }
    assert {e["event"] for e in _events(app_s) if e["event"].startswith("hold.")} == {
        "hold.accepted",
        "hold.adjudicated",
    }


@pytest.mark.asyncio
async def test_sync_hold_covered_matches_hangar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intercept = tmp_path / "arbiter.intercept.yaml"
    _write_intercept(intercept)
    voters = tmp_path / "arbiter.voters.yaml"
    voters.write_text(
        yaml.safe_dump(
            {
                "voters": [
                    {
                        "id": f"voter-{i}",
                        "base_url": "http://127.0.0.1:9",
                        "model": f"m{i}",
                        "temperature": 0,
                        "max_tokens": 10,
                        "timeout_seconds": 1,
                    }
                    for i in (1, 2, 3)
                ],
                "round_deadline_seconds": 5,
                "reveal_round": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters))
    monkeypatch.setenv("ARBITER_HANGAR_PRINCIPAL_ID", PRINCIPAL)

    def _seed(root: Path):
        app = create_application(
            root=root, rules=tmp_path / "arbiter.rules.yaml", voters=voters
        )
        opened = app.open_decision(
            question="Allow github create_issue?",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"k": 1},
            criticality="critical",
            ttl_seconds=900,
            scope=["github/create_issue"],
        )
        for voter in ("voter-1", "voter-2", "voter-3"):
            app.commands.cast_vote(
                decision_id=opened["decision_id"],
                voter=voter,
                option="allow",
                confidence=1.0,
                kill_criterion="n/a",
                bundle_sha256_hex=opened["bundle_sha256"],
            )
        app.resolve_decision(opened["decision_id"])
        return app, opened["decision_id"]

    app_h, did_h = _seed(tmp_path / "hangar")
    _seed(tmp_path / "sync")
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=_adj(app_h, intercept),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app_h,
        )
    )
    approved_h, _ = await harness.run(
        FakeApprovalRequest(
            approval_id="h-cov",
            mcp_server_id="github",
            tool_name="create_issue",
            arguments={"title": "x"},
        )
    )
    monkeypatch.setenv("ARBITER_DATA_DIR", str(tmp_path / "sync"))
    monkeypatch.setenv("ARBITER_INTERCEPT_PATH", str(intercept))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(tmp_path / "arbiter.rules.yaml"))
    result = await run_hold(
        mcp_server="github",
        tool="create_issue",
        arguments={"title": "x"},
    )
    app_s = create_application(
        root=tmp_path / "sync", rules=tmp_path / "arbiter.rules.yaml", voters=voters
    )
    _, adj_h = _hold_pair(app_h)
    _, adj_s = _hold_pair(app_s)
    assert approved_h is True and result["approved"] is True
    assert adj_h["path"] == adj_s["path"] == "covered"
    assert adj_h["decision_id"] == did_h
    assert adj_s["decision_id"] == result["decision_id"]


def test_cursor_hook_maps_write_and_skips_hangar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "arbiter"
    fake.write_text(
        "#!/bin/sh\necho '{\"approved\": true, \"reason\": \"covered_by:d1\"}'\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {"path": "notes/a.txt", "contents": "x"},
                "cwd": str(tmp_path),
            }
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["permission"] == "allow"
    skip = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "hangar_hangar_call", "tool_input": {}}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(skip.stdout)["permission"] == "allow"
    monkeypatch.setenv("PATH", "/nonexistent")
    missing = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Write", "tool_input": {"path": "a.py"}}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(missing.stdout)["permission"] == "deny"


def test_sync_hold_missing_intercept_denies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARBITER_DATA_DIR", str(tmp_path / "decisions"))
    monkeypatch.setenv("ARBITER_INTERCEPT_PATH", str(tmp_path / "nope.yaml"))
    monkeypatch.chdir(tmp_path)
    from arbiter.adapters.inbound import cli as cli_mod

    code = cli_mod._cmd_hold(
        type(
            "A",
            (),
            {
                "mcp_server": "cursor",
                "tool": "write",
                "arguments_json": '{"path":"a.py"}',
                "timeout_seconds": 30.0,
                "approval_id": None,
            },
        )()
    )
    assert code == 2
