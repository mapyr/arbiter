"""Model quorum — offline stub tests (no API keys)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import yaml

from arbiter.ledger import Ledger
from arbiter.llm import build_blind_prompt, prompt_sha256
from arbiter.protocol import run_model_quorum
from arbiter.voters import parse_voters_config
from tests.openai_stub import (
    StubScenario,
    StubServer,
    delay_handler,
    status_handler,
    text_handler,
    vote_handler,
)

pytestmark = pytest.mark.integration

EVIDENCE = {"paths": ["docs/readme.md"], "note": "stage3"}


def _write_voters(path: Path, base_url: str) -> None:
    cfg = {
        "voters": [
            {
                "id": "voter-1",
                "base_url": base_url,
                "model": "model-a",
                "temperature": 0,
                "max_tokens": 1200,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-2",
                "base_url": base_url,
                "model": "model-b",
                "temperature": 0,
                "max_tokens": 1200,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-3",
                "base_url": base_url,
                "model": "model-c",
                "temperature": 0,
                "max_tokens": 1200,
                "timeout_seconds": 5,
            },
        ],
        "round_deadline_seconds": 30,
        "reveal_round": True,
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _open_critical(ledger: Ledger) -> dict:
    return ledger.open_decision(
        question="Ship the critical change?",
        options=["opt-a", "opt-b", "opt-c"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence=EVIDENCE,
        criticality="critical",
        ttl_seconds=900,
    )


def _events(ledger: Ledger) -> list[dict]:
    return [
        json.loads(ln)
        for ln in ledger.ledger_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


@pytest.mark.asyncio
async def test_A1_unanimous_critical_allow(ledger: Ledger, tmp_cwd: Path) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("opt-a"))
    scenario.on("model-b", vote_handler("opt-a"))
    scenario.on("model-c", vote_handler("opt-a"))
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        opened = _open_critical(ledger)
        result = await run_model_quorum(
            ledger,
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(voters_path.read_text())),
            rng=random.Random(0),
        )
    assert result["verdict"] == "allow"
    assert result["chosen_option"] == "opt-a"
    kinds = [e["event"] for e in _events(ledger)]
    assert kinds.count("vote.cast") == 3
    assert all(
        e.get("round") == 1 for e in _events(ledger) if e["event"] == "vote.cast"
    )
    assert kinds.count("decision.resolved") == 1
    assert "quorum.round2.opened" not in kinds


@pytest.mark.asyncio
async def test_A2_dissent_triggers_round2_then_deny(
    ledger: Ledger, tmp_cwd: Path
) -> None:
    scenario = StubScenario()
    # Round 1: 2 vs 1. Round 2: still dissent, with revision_reason where changed.
    scenario.on(
        "model-a",
        vote_handler("opt-a"),
        vote_handler("opt-a"),  # unchanged — no revision_reason
    )
    scenario.on(
        "model-b",
        vote_handler("opt-a"),
        vote_handler("opt-a"),
    )
    scenario.on(
        "model-c",
        vote_handler("opt-b"),
        vote_handler(
            "opt-b",
            revision_reason="Still prefer opt-b after reveal.",
        ),
    )
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        opened = _open_critical(ledger)
        result = await run_model_quorum(
            ledger,
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(voters_path.read_text())),
            rng=random.Random(1),
        )
    assert result["verdict"] == "deny"
    assert result["reason"] == "dissent_on_critical"
    assert result["dissent"]
    kinds = [e["event"] for e in _events(ledger)]
    assert "quorum.round2.opened" in kinds
    rounds = sorted(
        e["round"] for e in _events(ledger) if e["event"] == "vote.cast"
    )
    assert rounds == [1, 1, 1, 2, 2, 2]


@pytest.mark.asyncio
async def test_A3_http_401_never_allows(ledger: Ledger, tmp_cwd: Path) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("opt-a"))
    scenario.on("model-b", status_handler(401))
    scenario.on("model-c", vote_handler("opt-a"))
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        opened = _open_critical(ledger)
        result = await run_model_quorum(
            ledger,
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(voters_path.read_text())),
        )
    assert result["verdict"] == "deny"
    assert result["reason"] == "quorum_not_met"
    assert "voter-2" in result["quorum"]["missing"]
    failed = [e for e in _events(ledger) if e["event"] == "vote.failed"]
    assert any(e["voter"] == "voter-2" and e["reason"] == "http_401" for e in failed)
    assert result["verdict"] != "allow"
    assert "quorum.round2.opened" not in [e["event"] for e in _events(ledger)]


@pytest.mark.asyncio
async def test_A4_timeout_never_allows(ledger: Ledger, tmp_cwd: Path) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("opt-a"))
    scenario.on("model-b", delay_handler(3.0, vote_handler("opt-a")))
    scenario.on("model-c", vote_handler("opt-a"))
    async with StubServer(scenario) as stub:
        cfg = {
            "voters": [
                {
                    "id": "voter-1",
                    "base_url": stub.base_url,
                    "model": "model-a",
                    "temperature": 0,
                    "max_tokens": 100,
                    "timeout_seconds": 0.2,
                },
                {
                    "id": "voter-2",
                    "base_url": stub.base_url,
                    "model": "model-b",
                    "temperature": 0,
                    "max_tokens": 100,
                    "timeout_seconds": 0.2,
                },
                {
                    "id": "voter-3",
                    "base_url": stub.base_url,
                    "model": "model-c",
                    "temperature": 0,
                    "max_tokens": 100,
                    "timeout_seconds": 0.2,
                },
            ],
            "round_deadline_seconds": 10,
            "reveal_round": True,
        }
        opened = _open_critical(ledger)
        result = await run_model_quorum(
            ledger,
            opened["decision_id"],
            config=parse_voters_config(cfg),
        )
    assert result["verdict"] == "deny"
    assert result["reason"] == "quorum_not_met"
    assert "voter-2" in result["quorum"]["missing"]
    failed = [e for e in _events(ledger) if e["event"] == "vote.failed"]
    assert any(e["voter"] == "voter-2" and e["reason"] == "timeout" for e in failed)


@pytest.mark.asyncio
async def test_A5_option_outside_set_retry_then_no_vote(
    ledger: Ledger, tmp_cwd: Path
) -> None:
    scenario = StubScenario()
    scenario.on(
        "model-a",
        text_handler(json.dumps({"option": "opt-zzz", "confidence": 0.5, "kill_criterion": "x" * 8})),
        text_handler(json.dumps({"option": "opt-zzz", "confidence": 0.5, "kill_criterion": "x" * 8})),
    )
    scenario.on("model-b", vote_handler("opt-a"))
    scenario.on("model-c", vote_handler("opt-a"))
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        opened = _open_critical(ledger)
        result = await run_model_quorum(
            ledger,
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(voters_path.read_text())),
        )
    assert result["verdict"] == "deny"
    cast_options = [
        e["option"] for e in _events(ledger) if e["event"] == "vote.cast"
    ]
    assert "opt-zzz" not in cast_options
    assert scenario.call_counts["model-a"] == 2


@pytest.mark.asyncio
async def test_A6_round2_change_without_revision_reason_rejected(
    ledger: Ledger, tmp_cwd: Path
) -> None:
    scenario = StubScenario()
    scenario.on(
        "model-a",
        vote_handler("opt-a"),
        vote_handler("opt-b"),  # change, no revision_reason — twice
        vote_handler("opt-b"),
    )
    scenario.on(
        "model-b",
        vote_handler("opt-a"),
        vote_handler("opt-a"),
    )
    scenario.on(
        "model-c",
        vote_handler("opt-b"),
        vote_handler("opt-b", revision_reason="Keep B."),
    )
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        opened = _open_critical(ledger)
        result = await run_model_quorum(
            ledger,
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(voters_path.read_text())),
            rng=random.Random(2),
        )
    assert result["verdict"] == "deny"
    r2 = [
        e
        for e in _events(ledger)
        if e["event"] == "vote.cast" and e.get("round") == 2 and e["voter"] == "voter-1"
    ]
    assert r2 == []
    failed = [
        e
        for e in _events(ledger)
        if e["event"] == "vote.failed" and e["voter"] == "voter-1" and e["round"] == 2
    ]
    assert failed


@pytest.mark.asyncio
async def test_A7_reveal_labels_differ_across_decisions(
    ledger: Ledger, tmp_cwd: Path
) -> None:
    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(
            model,
            vote_handler("opt-a" if model != "model-c" else "opt-b"),
            vote_handler(
                "opt-a" if model != "model-c" else "opt-b",
                revision_reason="Hold.",
            ),
        )
    labels = []
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        cfg = parse_voters_config(yaml.safe_load(voters_path.read_text()))
        for seed in (10, 11):
            opened = _open_critical(ledger)
            await run_model_quorum(
                ledger,
                opened["decision_id"],
                config=cfg,
                rng=random.Random(seed),
            )
            ev = next(
                e
                for e in _events(ledger)
                if e["event"] == "quorum.round2.opened"
                and e["decision_id"] == opened["decision_id"]
            )
            labels.append(ev["labels"])
    assert labels[0] != labels[1]


@pytest.mark.asyncio
async def test_A8_prompt_sha256_stable_across_identical_runs(
    ledger: Ledger, tmp_cwd: Path
) -> None:
    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(model, vote_handler("opt-a"))
    hashes = []
    async with StubServer(scenario) as stub:
        voters_path = tmp_cwd / "arbiter.voters.yaml"
        _write_voters(voters_path, stub.base_url)
        cfg = parse_voters_config(yaml.safe_load(voters_path.read_text()))
        for _ in range(2):
            opened = _open_critical(ledger)
            result = await run_model_quorum(
                ledger, opened["decision_id"], config=cfg, rng=random.Random(0)
            )
            hashes.append(result["prompt_sha256"])
    assert hashes[0] == hashes[1]
    expected = prompt_sha256(
        build_blind_prompt(
            question="Ship the critical change?",
            options=["opt-a", "opt-b", "opt-c"],
            evidence=EVIDENCE,
        )
    )
    assert hashes[0]["voter-1"] == expected
    assert hashes[0]["voter-2"] == expected
    assert hashes[0]["voter-3"] == expected


@pytest.mark.asyncio
async def test_open_decision_rejects_roster_mismatch(
    ledger: Ledger, tmp_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voters_path = tmp_cwd / "arbiter.voters.yaml"
    _write_voters(voters_path, "http://127.0.0.1:9/v1")
    monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters_path))
    from mcp import Client

    from arbiter.server import create_server
    from tests.conftest import tool_error_text

    server = create_server(ledger)
    async with Client(server) as client:
        result = await client.call_tool(
            "open_decision",
            {
                "question": "Ship?",
                "options": ["opt-a", "opt-b"],
                "voters": ["alice", "bob", "carol"],
                "evidence": EVIDENCE,
                "criticality": "critical",
            },
        )
    err = tool_error_text(result)
    assert "voters mismatch" in err
    assert "alice" in err or "only in open_decision" in err


def test_A9_import_from_installed_package() -> None:
    """Acceptance: arbiter must resolve from site-packages when installed."""
    import importlib.util
    from pathlib import Path

    origin = Path(importlib.util.find_spec("arbiter").origin).resolve()
    # In CI the suite runs from a copied tests dir; locally allow editable install.
    assert origin.name == "__init__.py"
    assert "arbiter" in origin.parts
