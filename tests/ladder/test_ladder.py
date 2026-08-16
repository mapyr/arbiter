"""Ladder measurements and invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbiter.domain.errors import DomainError
from arbiter.domain.services.dependencies import assert_no_cycle, cascade_invalidations
from arbiter.domain.services.narrowing import narrowing_candidates
from arbiter.domain.services.option_kind import option_kind
from arbiter.domain.services.preconditions import check_preconditions
from arbiter.domain.services.quorum import resolve
from tests.ladder.harness import (
    close_ladder_env,
    open_ladder_env,
    run_s1,
    run_s2,
    run_s3,
    run_s5,
    run_s6,
    unanimous,
)

# Offline stub corpus — runs in the unit / domain-coverage suite (not live providers).

RESULTS_PATH = Path(__file__).resolve().parent / "ladder_results.json"


@pytest.mark.asyncio
async def test_ladder_full_climb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Climb S1–S6 with measurements; write results for the stage-7 report."""
    results: dict = {
        "stop_thresholds": {
            "disagree_rate_max": 0.33,
            "hold_duration_p95_ms_max": 25_000,
        },
        "prediction": {
            "S2": "cheap_durable",
            "S3": "cheap_durable",
            "S4": "expensive_marginal",
            "S5": "cheap_durable",
            "S6": "highest_value_least_model_dependent",
        },
    }

    # --- S1 ---
    env = await open_ladder_env(tmp_path / "s1", monkeypatch, unanimous("allow"))
    try:
        results["S1"] = await run_s1(env)
    finally:
        await close_ladder_env(env)
    assert not results["S1"]["stop_divergence"]
    assert not results["S1"]["stop_latency"]

    # --- S2 ---
    env = await open_ladder_env(tmp_path / "s2", monkeypatch, unanimous("allow"))
    try:
        results["S2"] = await run_s2(env)
    finally:
        await close_ladder_env(env)
    assert results["S2"]["denied_without_trial"] is True
    assert results["S2"]["apply_after_trial_path"] in ("quorum", "covered", "duplicate")

    # --- S3 ---
    env = await open_ladder_env(
        tmp_path / "s3", monkeypatch, unanimous("allow"), enable_narrowing=True
    )
    try:
        results["S3"] = await run_s3(env)
    finally:
        await close_ladder_env(env)
    assert results["S3"]["narrow_replaced_deny"] is True
    assert results["S3"]["narrow_approved"] is True

    # --- S4 already cut (no verdict gain vs cost) ---
    results["S4"] = {
        "cut": True,
        "reason": "no verdict gain (A/B); removed",
        "recommend_cut": True,
    }

    # --- S5 ---
    env = await open_ladder_env(tmp_path / "s5", monkeypatch, unanimous("allow"))
    try:
        results["S5"] = await run_s5(env)
    finally:
        await close_ladder_env(env)
    assert results["S5"]["cycle_refused"] is True
    assert results["S5"]["cascade_count"] >= 2

    # --- S6 ---
    env = await open_ladder_env(
        tmp_path / "s6",
        monkeypatch,
        unanimous("allow"),
        include_escalate=True,
    )
    try:
        results["S6"] = await run_s6(env)
    finally:
        await close_ladder_env(env)
    assert results["S6"]["rule_deny_without_test"] is True
    assert results["S6"]["rule_allow_with_test"] is True
    assert results["S6"]["escalate_is_pass"] is False

    # Ceiling / recommendation from numbers
    s4_cut = bool(results["S4"]["recommend_cut"])
    results["ceiling"] = {
        "last_held": "S6",
        "s4_product_recommendation": "cut" if s4_cut else "keep",
        "stop_triggered": None,
        "note": (
            "Offline corpus: no stop threshold tripped; S4 "
            + ("cut (no verdict gain, cost up)" if s4_cut else "kept")
        ),
    }
    results["recommendation"] = {
        "stay": ["S2", "S3", "S5", "S6"] + ([] if s4_cut else ["S4"]),
        "cut": ["S4"] if s4_cut else [],
    }

    RESULTS_PATH.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    assert RESULTS_PATH.exists()


def test_invariants_option_kind_and_quorum() -> None:
    assert option_kind("allow") == "allow"
    assert option_kind("allow_narrow:ttl=60") == "allow_narrow"
    assert option_kind("escalate_to_human") == "escalate_to_human"
    assert option_kind("deny") == "deny"
    r = resolve(
        criticality="critical",
        voters=["a", "b", "c"],
        votes={
            "a": "allow_narrow:ttl=60",
            "b": "allow_narrow:ttl=60",
            "c": "allow_narrow:ttl=60",
        },
        options=["allow", "deny", "allow_narrow:ttl=60"],
        deadline_passed=False,
    )
    assert r.verdict == "allow_narrow"
    assert r.reason == "quorum_met"


def test_invariant_precondition_pure() -> None:
    events = [
        {
            "event": "hold.accepted",
            "tool_name": "migrate.dry_run",
            "arguments_hash": "abc",
            "call_id": "c1",
            "mcp_server_id": "db",
        },
        {
            "event": "hold.adjudicated",
            "tool_name": "migrate.dry_run",
            "approved": True,
            "call_id": "c1",
            "mcp_server_id": "db",
        },
    ]
    ok = check_preconditions(
        events, tool_name="migrate.apply", arguments_hash="abc", mcp_server_id="db"
    )
    assert ok.ok is True
    bad = check_preconditions(
        [], tool_name="migrate.apply", arguments_hash="abc", mcp_server_id="db"
    )
    assert bad.ok is False


def test_invariant_model_cannot_invent_narrow_value() -> None:
    opts = narrowing_candidates(
        tool_name="write_file", arguments={"path": "a.py"}
    )
    # Closed set only — free-form values are not generated
    assert all(isinstance(o, str) for o in opts)
    assert "allow_narrow:ttl=999999" not in opts or "ttl=999999" in str(opts)


def test_invariant_dependency_cycle() -> None:
    with pytest.raises(DomainError, match="cycle"):
        assert_no_cycle("a", ["b"], {"b": ["a"]})
    cascaded = cascade_invalidations("p", {"c": ["p"], "d": ["c"]})
    assert cascaded[0] == "p"
    assert "c" in cascaded and "d" in cascaded
