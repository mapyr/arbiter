"""Plan gate: validate_plan, get_gate_policy, ensure_plan → coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError
from arbiter.domain.services.client_gate import parse_client_gate
from arbiter.domain.services.plan import validate_plan
from tests.openai_stub import StubScenario, StubServer, vote_handler


def test_validate_plan_requires_goal_and_steps() -> None:
    with pytest.raises(DomainError, match="goal"):
        validate_plan({"steps": [{"action": "x"}], "scope": ["src/**"]})
    with pytest.raises(DomainError, match="steps"):
        validate_plan({"goal": "do thing", "scope": ["src/**"]})


def test_validate_plan_derives_scope_from_step_paths() -> None:
    plan = validate_plan(
        {
            "goal": "touch handler",
            "steps": [{"action": "edit", "paths": ["auth/handler.py"]}],
        }
    )
    assert plan["scope"] == ["auth/handler.py"]
    assert "depends_on" not in plan
    assert "establishes_rule" not in plan


def test_validate_plan_passes_deps_and_rule() -> None:
    rule = {
        "kind": "require_contract_test",
        "path_glob": "src/**",
        "detail": "writes under src require contract test",
        "rule_id": "rule-src-contract",
    }
    plan = validate_plan(
        {
            "goal": "touch handler",
            "steps": [{"action": "edit", "paths": ["auth/handler.py"]}],
            "depends_on": ["d-parent"],
            "establishes_rule": rule,
        }
    )
    assert plan["depends_on"] == ["d-parent"]
    assert plan["establishes_rule"] == rule


def test_validate_plan_requires_scope_without_paths() -> None:
    with pytest.raises(DomainError, match="scope required"):
        validate_plan(
            {"goal": "noop", "steps": [{"action": "think"}]}
        )


def test_parse_client_gate_defaults_and_modes() -> None:
    assert parse_client_gate(None)["plan"]["mode"] == "on_uncovered"
    assert parse_client_gate({"client_gate": {"plan": {"mode": "session"}}})[
        "plan"
    ]["mode"] == "session"
    with pytest.raises(DomainError, match="mode"):
        parse_client_gate({"client_gate": {"plan": {"mode": "always"}}})
    with pytest.raises(DomainError, match="mode"):
        parse_client_gate(
            {"client_gate": {"plan": {"mode": "on_uncovered   # or session"}}}
        )


def test_get_gate_policy_from_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "default": "routine",
                "client_gate": {
                    "plan": {
                        "mode": "session",
                        "arbiter_mcp_server": "arbiter-b",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))
    app = create_application()
    policy = app.get_gate_policy()
    assert policy == {
        "plan": {"mode": "session", "arbiter_mcp_server": "arbiter-b"}
    }


def _write_voters(path: Path, base_url: str) -> None:
    cfg = {
        "voters": [
            {
                "id": "voter-1",
                "base_url": base_url,
                "model": "model-a",
                "temperature": 0,
                "max_tokens": 200,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-2",
                "base_url": base_url,
                "model": "model-b",
                "temperature": 0,
                "max_tokens": 200,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-3",
                "base_url": base_url,
                "model": "model-c",
                "temperature": 0,
                "max_tokens": 200,
                "timeout_seconds": 5,
            },
        ],
        "round_deadline_seconds": 30,
        "reveal_round": False,
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


@pytest.mark.asyncio
async def test_ensure_plan_allow_then_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "critical": {"paths": ["auth/**"]},
                "default": "routine",
                "formulation": {
                    "deny_universal_scope": True,
                    "deny_filler_options": True,
                },
                "client_gate": {
                    "plan": {"mode": "on_uncovered", "arbiter_mcp_server": "arbiter"}
                },
            }
        ),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ARBITER_DATA_DIR", str(data))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))

    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(model, vote_handler("allow"))

    async with StubServer(scenario) as stub:
        voters_path = tmp_path / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters_path))
        app = create_application()

        denied = app.check_coverage(paths=["auth/handler.py"], tool="edit")
        assert denied["approved"] is False

        result = await app.ensure_plan(
            {
                "goal": "Update auth handler login path",
                "steps": [
                    {
                        "action": "edit auth handler",
                        "paths": ["auth/handler.py"],
                    }
                ],
                "scope": ["auth/**"],
            }
        )
        assert result["approved"] is True
        assert result["decision_id"]
        assert "auth/**" in result["scope"]
        opened = next(
            e
            for e in app.read_all_wire()
            if e.get("event") == "decision.opened"
            and e["decision_id"] == result["decision_id"]
        )
        assert not opened.get("depends_on")
        assert "establishes_rule" not in opened

        covered = app.check_coverage(paths=["auth/handler.py"], tool="edit")
        assert covered["approved"] is True
        assert covered["decision_id"] == result["decision_id"]


@pytest.mark.asyncio
async def test_ensure_plan_rejects_universal_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "default": "routine",
                "formulation": {"deny_universal_scope": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))
    voters_path = tmp_path / "arbiter.voters.yaml"
    # Voters file required before ensure_plan hits formulation.
    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(model, vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(voters_path, stub.base_url)
        monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters_path))
        app = create_application()
        with pytest.raises(DomainError, match="universal|formulation"):
            await app.ensure_plan(
                {
                    "goal": "everything",
                    "steps": [{"action": "edit all"}],
                    "scope": ["**/*"],
                }
            )


@pytest.mark.asyncio
async def test_ensure_plan_passes_deps_and_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(
        yaml.safe_dump({"default": "routine", "critical": {"paths": ["auth/**"]}}),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ARBITER_DATA_DIR", str(data))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))
    rule = {
        "kind": "require_contract_test",
        "path_glob": "src/**",
        "detail": "writes under src require contract test",
        "rule_id": "rule-src-contract",
    }
    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(model, vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(tmp_path / "arbiter.voters.yaml", stub.base_url)
        monkeypatch.setenv("ARBITER_VOTERS_PATH", str(tmp_path / "arbiter.voters.yaml"))
        app = create_application()
        parent = app.open_decision(
            question="parent",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"k": 1},
            scope=["policy/rule"],
        )
        result = await app.ensure_plan(
            {
                "goal": "Update auth handler login path",
                "steps": [{"action": "edit auth handler", "paths": ["auth/handler.py"]}],
                "scope": ["auth/**"],
                "depends_on": [parent["decision_id"]],
                "establishes_rule": rule,
            }
        )
    got = app.get_decision(result["decision_id"])
    assert got["depends_on"] == [parent["decision_id"]]
    assert got["establishes_rule"] == rule
    assert result["plan"]["depends_on"] == [parent["decision_id"]]
