"""End-to-end MCP tool tests against the real SDK Client."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, ClientSession, StdioServerParameters, stdio_client

from arbiter.ledger import Ledger

pytestmark = pytest.mark.integration

ROUTINE_EVIDENCE = {"paths": ["docs/readme.md"], "note": "safe"}


def tool_payload(result) -> dict:
    assert result.is_error is False, result.content
    if result.structured_content is not None:
        return dict(result.structured_content)
    return json.loads(result.content[0].text)


def tool_error_text(result) -> str:
    assert result.is_error is True
    return result.content[0].text


@pytest.mark.asyncio
async def test_mcp_full_allow_path(server) -> None:
    async with Client(server) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools.tools)
        assert names == [
            "cast_vote",
            "check_coverage",
            "ensure_plan",
            "get_decision",
            "get_gate_policy",
            "open_decision",
            "resolve_decision",
            "run_model_quorum",
        ]

        opened = tool_payload(
            await client.call_tool(
                "open_decision",
                {
                    "question": "Ship it?",
                    "options": ["opt-a", "opt-b", "opt-c"],
                    "voters": ["voter-1", "voter-2", "voter-3"],
                    "evidence": ROUTINE_EVIDENCE,
                    "criticality": "routine",
                },
            )
        )
        digest = opened["bundle_sha256"]
        for voter in opened["voters"]:
            cast = tool_payload(
                await client.call_tool(
                    "cast_vote",
                    {
                        "decision_id": opened["decision_id"],
                        "voter": voter,
                        "option": "opt-a",
                        "confidence": 0.9,
                        "kill_criterion": "CI red on main.",
                        "bundle_sha256": digest,
                    },
                )
            )
            assert cast["recorded"] is True

        resolved = tool_payload(
            await client.call_tool(
                "resolve_decision", {"decision_id": opened["decision_id"]}
            )
        )
        assert resolved["verdict"] == "allow"
        assert resolved["chosen_option"] == "opt-a"
        assert resolved["reason"] == "quorum_met"

        again = tool_payload(
            await client.call_tool(
                "resolve_decision", {"decision_id": opened["decision_id"]}
            )
        )
        assert again["reason"] == "already_resolved"
        assert again["verdict"] == "allow"


@pytest.mark.asyncio
async def test_mcp_R1_rejects_unknown_option(server) -> None:
    async with Client(server) as client:
        opened = tool_payload(
            await client.call_tool(
                "open_decision",
                {
                    "question": "Ship it?",
                    "options": ["opt-a", "opt-b"],
                    "voters": ["voter-1"],
                    "evidence": ROUTINE_EVIDENCE,
                    "criticality": "routine",
                },
            )
        )
        err = await client.call_tool(
            "cast_vote",
            {
                "decision_id": opened["decision_id"],
                "voter": "voter-1",
                "option": "nope",
                "confidence": 0.5,
                "kill_criterion": "Fail.",
                "bundle_sha256": opened["bundle_sha256"],
            },
        )
        text = tool_error_text(err)
        assert "nope" in text
        assert "opt-a" in text


@pytest.mark.asyncio
async def test_mcp_get_decision_read_only(server, ledger: Ledger) -> None:
    async with Client(server) as client:
        opened = tool_payload(
            await client.call_tool(
                "open_decision",
                {
                    "question": "Ship it?",
                    "options": ["opt-a", "opt-b"],
                    "voters": ["voter-1", "voter-2"],
                    "evidence": ROUTINE_EVIDENCE,
                    "criticality": "routine",
                },
            )
        )
        before = [
            ln
            for ln in ledger.ledger_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        view = tool_payload(
            await client.call_tool(
                "get_decision", {"decision_id": opened["decision_id"]}
            )
        )
        after = [
            ln
            for ln in ledger.ledger_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert before == after
        assert view["status"] == "open"
        assert view["missing_voters"] == ["voter-1", "voter-2"]


@pytest.mark.asyncio
async def test_stdio_installed_package_happy_path(tmp_path: Path) -> None:
    """Acceptance: drive the server over stdio from an installed environment.

    Uses the current interpreter; CI/verification installs the package into a
    fresh venv first, then runs pytest with that interpreter.
    """
    data = tmp_path / "decisions"
    data.mkdir()
    rules = Path(__file__).resolve().parents[1] / "arbiter.rules.yaml.example"
    rules_copy = tmp_path / "arbiter.rules.yaml"
    rules_copy.write_text(rules.read_text(encoding="utf-8"), encoding="utf-8")

    env = os.environ.copy()
    env["ARBITER_DATA_DIR"] = str(data)
    env["ARBITER_RULES_PATH"] = str(rules_copy)
    # Ensure site-packages of this interpreter are used as-is.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "arbiter.server"],
        env=env,
        cwd=str(tmp_path),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            assert names == [
                "cast_vote",
                "check_coverage",
                "ensure_plan",
                "get_decision",
                "get_gate_policy",
                "open_decision",
                "resolve_decision",
                "run_model_quorum",
            ]

            opened_raw = await session.call_tool(
                "open_decision",
                {
                    "question": "Ship via stdio?",
                    "options": ["opt-a", "opt-b", "opt-c"],
                    "voters": ["voter-1", "voter-2", "voter-3"],
                    "evidence": ROUTINE_EVIDENCE,
                    "criticality": "routine",
                },
            )
            assert opened_raw.is_error is False
            opened = json.loads(opened_raw.content[0].text)
            digest = opened["bundle_sha256"]

            for voter in opened["voters"]:
                cast_raw = await session.call_tool(
                    "cast_vote",
                    {
                        "decision_id": opened["decision_id"],
                        "voter": voter,
                        "option": "opt-a",
                        "confidence": 0.85,
                        "kill_criterion": "CI red.",
                        "bundle_sha256": digest,
                    },
                )
                assert cast_raw.is_error is False

            resolved_raw = await session.call_tool(
                "resolve_decision",
                {"decision_id": opened["decision_id"]},
            )
            assert resolved_raw.is_error is False
            resolved = json.loads(resolved_raw.content[0].text)
            assert resolved["verdict"] == "allow"
            assert resolved["chosen_option"] == "opt-a"

    lines = [
        ln
        for ln in (data / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(lines) == 5
    events = [json.loads(ln)["event"] for ln in lines]
    assert events == [
        "decision.opened",
        "vote.cast",
        "vote.cast",
        "vote.cast",
        "decision.resolved",
    ]
