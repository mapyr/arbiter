"""Shadow mode, baseline isolation, formulation barriers, ledger report."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from arbiter.application.services.eval_report import build_eval_report, render_markdown
from arbiter.application.services.hold_adjudicator import HeldCall, HoldAdjudicator
from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError
from arbiter.domain.services.formulation import assert_formulation_allowed
from tests.openai_stub import StubScenario, StubServer, vote_handler


@dataclass
class _FakeApp:
    wire: list[dict[str, Any]]

    def read_all_wire(self) -> list[dict[str, Any]]:
        return list(self.wire)


def test_formulation_deny_universal_scope() -> None:
    with pytest.raises(DomainError, match="universal"):
        assert_formulation_allowed(
            options=["allow", "deny"],
            scope=["**/*"],
            rules={"formulation": {"deny_universal_scope": True}},
        )


def test_formulation_deny_filler_options() -> None:
    with pytest.raises(DomainError, match="filler"):
        assert_formulation_allowed(
            options=["Ship the migration as written", "n/a", "other"],
            scope=["arbiter/domain/**"],
            rules={"formulation": {"deny_filler_options": True}},
        )


def test_formulation_off_by_default() -> None:
    assert_formulation_allowed(
        options=["Ship the migration as written", "n/a", "other"],
        scope=["**/*"],
        rules={},
    )


def test_open_decision_records_mode_and_env_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARBITER_SHADOW_MODE", "1")
    monkeypatch.delenv("ARBITER_VOTERS_PATH", raising=False)
    app = create_application(root=tmp_path / "decisions")
    opened = app.open_decision(
        question="Q?",
        options=["allow", "deny"],
        voters=["a", "b"],
        evidence={"paths": ["x.py"]},
        criticality="routine",
        check_voters_config=False,
    )
    assert opened["mode"] == "shadow"
    wire = app.read_all_wire()
    assert wire[0]["event"] == "decision.opened"
    assert wire[0]["mode"] == "shadow"
    got = app.get_decision(opened["decision_id"])
    assert got["mode"] == "shadow"


def test_eval_report_from_ledger_distributions() -> None:
    wire = [
        {
            "event": "decision.opened",
            "decision_id": "d1",
            "options": ["allow", "deny"],
            "scope": ["github/create_issue"],
            "mode": "shadow",
        },
        {
            "event": "decision.opened",
            "decision_id": "d2",
            "options": ["allow", "deny"],
            "scope": ["fs/write"],
            "mode": "shadow",
        },
        {
            "event": "baseline.verdict",
            "decision_id": "d1",
            "option": "allow",
            "ok": True,
        },
        {
            "event": "baseline.verdict",
            "decision_id": "d2",
            "option": "deny",
            "ok": True,
        },
        {
            "event": "decision.resolved",
            "decision_id": "d1",
            "verdict": "deny",
            "chosen_option": "deny",
            "reason": "no_quorum",
        },
        {
            "event": "decision.resolved",
            "decision_id": "d2",
            "verdict": "allow",
            "chosen_option": "allow",
            "reason": "unanimous",
        },
        {
            "event": "quorum.round2.opened",
            "decision_id": "d1",
            "labels": {"A": "voter-1"},
        },
        {
            "event": "vote.cast",
            "decision_id": "d1",
            "voter": "voter-1",
            "option": "deny",
            "round": 1,
            "latency_ms": 100.0,
            "prompt_tokens": 10,
            "completion_tokens": 5,
        },
        {
            "event": "vote.cast",
            "decision_id": "d1",
            "voter": "voter-1",
            "option": "allow",
            "round": 2,
            "revision_reason": "new evidence in reveal",
            "latency_ms": 120.0,
        },
        {
            "event": "hold.adjudicated",
            "path": "quorum",
            "duration_ms": 400.0,
        },
        {
            "event": "hold.adjudicated",
            "path": "covered",
            "duration_ms": 5.0,
        },
        {
            "event": "break_glass.used",
            "actor": "dev",
            "tool": "edit",
            "paths": ["x.py"],
            "reason": "urgent",
        },
        {
            "event": "coverage.checked",
            "path": "covered",
            "approved": True,
        },
    ]
    report = build_eval_report(
        _FakeApp(wire), repo=Path("/nonexistent"), horizon_days=14
    )
    assert report["sample"]["shadow_opens"] == 2
    assert report["divergence"]["comparable"] == 2
    assert report["divergence"]["disagree"] == 2
    assert report["internal_dissent"]["round1_no_quorum"] == 1
    assert report["reveal_round"]["option_changes"] == 1
    assert report["coverage"]["covered_share"] is not None
    assert report["compounding"]["ready_to_enforce"] is False
    assert report["reuse"]["opened"] == 2
    assert report["cost_time"]["vote_latency_ms"]["n"] == 2
    assert report["break_glass"]["n"] == 1
    assert report["thesis"]["caveats"]
    md = render_markdown(report)
    assert "caveat" in md.lower()
    assert "Divergence" in md
    assert "ready_to_enforce" in md


def test_eval_report_hold_path_mix_and_reuse() -> None:
    wire = [
        {"event": "decision.opened", "decision_id": "d1"},
        {"event": "decision.opened", "decision_id": "d2"},
        {"event": "hold.adjudicated", "path": "covered", "decision_id": "d1"},
        {"event": "hold.adjudicated", "path": "duplicate", "decision_id": "d1"},
        {"event": "hold.adjudicated", "path": "quorum", "decision_id": "d2"},
        {"event": "hold.adjudicated", "path": "rule_deny"},
        {"event": "hold.adjudicated", "path": "precondition_denied"},
        {"event": "coverage.checked", "path": "covered", "decision_id": "d1"},
    ]
    report = build_eval_report(
        _FakeApp(wire), repo=Path("/nonexistent"), horizon_days=14
    )
    hold = report["coverage"]
    assert hold["hold_total"] == 5
    assert hold["hold_covered_share"] == 0.4
    assert hold["hold_quorum_share"] == 0.2
    assert hold["hold_rule_share"] == 0.2
    assert hold["hold_precondition_share"] == 0.2
    assert report["reuse"] == {
        "opened": 2,
        "reused": 1,
        "one_shot": 1,
        "one_shot_share": 0.5,
    }
    assert report["compounding"]["ready_to_enforce"] is False
    assert report["compounding"]["gates"]["hold_total_min"] == 10
    assert report["divergence"]["comparable"] == 0


def _write_voters(
    path: Path, base_url: str, *, shadow: bool = False, baseline: str | None = "voter-1"
) -> None:
    cfg: dict[str, Any] = {
        "voters": [
            {
                "id": "voter-1",
                "base_url": base_url,
                "model": "model-a",
                "temperature": 0,
                "max_tokens": 400,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-2",
                "base_url": base_url,
                "model": "model-b",
                "temperature": 0,
                "max_tokens": 400,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-3",
                "base_url": base_url,
                "model": "model-c",
                "temperature": 0,
                "max_tokens": 400,
                "timeout_seconds": 5,
            },
        ],
        "round_deadline_seconds": 30,
        "reveal_round": False,
        "shadow_mode": shadow,
    }
    if baseline:
        cfg["baseline_voter"] = baseline
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_shadow_hold_does_not_gate_and_records_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = StubScenario()
    # Unanimous deny of the tool call; shadow must still approve.
    scenario.on("model-a", vote_handler("deny"))
    scenario.on("model-b", vote_handler("deny"))
    scenario.on("model-c", vote_handler("deny"))
    async with StubServer(scenario) as stub:
        voters = tmp_path / "voters.yaml"
        _write_voters(voters, stub.base_url, shadow=True, baseline="voter-1")
        monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters))
        app = create_application(root=tmp_path / "decisions", voters=voters)
        from arbiter.domain.services.intercept import parse_intercept_rules

        adj = HoldAdjudicator(
            app,
            intercept=parse_intercept_rules(
                {"hold": [{"mcp_server": "github", "tool": "create_issue"}]}
            ),
            resolver_principal="service:arbiter",
            min_round_seconds=1.0,
        )
        held = HeldCall(
            approval_id="a1",
            mcp_server_id="github",
            tool_name="create_issue",
            arguments={"title": "x"},
            arguments_hash="deadbeef",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
            requested_by="agent",
            tenant_id="t1",
            correlation_id="corr",
        )
        result = await adj.adjudicate(held)
        assert result.approved is True
        assert result.path == "quorum"
        assert result.reason.startswith("shadow:")
        events = app.read_all_wire()
        opened = [e for e in events if e["event"] == "decision.opened"]
        assert opened and opened[0]["mode"] == "shadow"
        baselines = [e for e in events if e["event"] == "baseline.verdict"]
        assert len(baselines) == 1
        assert baselines[0]["ok"] is True
        assert baselines[0]["option"] == "deny"
        votes = [e for e in events if e["event"] == "vote.cast"]
        assert all(not str(v["voter"]).startswith("baseline-") for v in votes)
        resolved = [e for e in events if e["event"] == "decision.resolved"]
        assert resolved and resolved[0]["chosen_option"] == "deny"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_baseline_isolated_from_quorum_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("allow"))
    scenario.on("model-b", vote_handler("allow"))
    scenario.on("model-c", vote_handler("allow"))
    async with StubServer(scenario) as stub:
        voters = tmp_path / "voters.yaml"
        _write_voters(voters, stub.base_url, shadow=False, baseline="voter-1")
        monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters))
        app = create_application(root=tmp_path / "decisions", voters=voters)
        opened = app.open_decision(
            question="Approve?",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"paths": ["README.md"]},
            criticality="critical",
            mode="enforce",
        )
        await app.run_model_quorum(opened["decision_id"])
        assert scenario.call_counts["model-a"] == 2
        assert scenario.call_counts["model-b"] == 1
        assert scenario.call_counts["model-c"] == 1
        responses = list((tmp_path / "decisions" / "responses").rglob("*.json"))
        names = {p.name for p in responses}
        assert any(n.startswith("baseline-voter-1") for n in names)
        baseline_blob = next(p for p in responses if p.name.startswith("baseline-"))
        payload = json.loads(baseline_blob.read_text(encoding="utf-8"))
        assert payload["line"] == "baseline"
        assert payload["round"] == 0


def test_open_rejects_filler_when_rules_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "default": "routine",
                "formulation": {
                    "deny_universal_scope": True,
                    "deny_filler_options": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))
    monkeypatch.delenv("ARBITER_VOTERS_PATH", raising=False)
    app = create_application(root=tmp_path / "decisions", rules=rules)
    with pytest.raises(DomainError, match="formulation barrier"):
        app.open_decision(
            question="Q?",
            options=["Real substantive option text here", "n/a", "other"],
            voters=["a", "b"],
            evidence={"paths": ["x.py"]},
            scope=["**/*"],
            check_voters_config=False,
        )
