"""Behavioral tests for the append-only ledger and rules R1–R8."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arbiter.application.app import Application
from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError
from arbiter.domain.services.canonical import bundle_sha256


ROUTINE_EVIDENCE = {"paths": ["docs/readme.md"], "note": "safe"}


def _open_routine(ledger: Application, **kwargs):
    defaults = dict(
        question="Ship the change?",
        options=["opt-a", "opt-b", "opt-c"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence=ROUTINE_EVIDENCE,
        criticality="routine",
    )
    defaults.update(kwargs)
    return ledger.open_decision(**defaults)


def test_canonical_key_order_stable_hash() -> None:
    a = {"z": 1, "a": {"y": 2, "b": 3}, "paths": ["x"]}
    b = {"paths": ["x"], "a": {"b": 3, "y": 2}, "z": 1}
    assert bundle_sha256(a) == bundle_sha256(b)


def test_full_happy_path_five_ledger_lines(ledger: Application) -> None:
    opened = _open_routine(ledger)
    digest = opened["bundle_sha256"]
    assert opened["criticality"] == "routine"
    for voter in opened["voters"]:
        ledger.cast_vote(
            decision_id=opened["decision_id"],
            voter=voter,
            option="opt-a",
            confidence=0.9,
            kill_criterion="Tests fail on main.",
            bundle_sha256_hex=digest,
        )
    resolved = ledger.resolve_decision(opened["decision_id"])
    assert resolved["verdict"] == "allow"
    assert resolved["chosen_option"] == "opt-a"
    assert resolved["reason"] == "quorum_met"

    lines = [
        ln
        for ln in ledger.ledger_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(lines) == 5
    kinds = [__import__("json").loads(ln)["event"] for ln in lines]
    assert kinds == [
        "decision.opened",
        "vote.cast",
        "vote.cast",
        "vote.cast",
        "decision.resolved",
    ]


def test_R1_closed_option_set(ledger: Application) -> None:
    opened = _open_routine(ledger)
    with pytest.raises(DomainError) as exc:
        ledger.cast_vote(
            decision_id=opened["decision_id"],
            voter="voter-1",
            option="opt-zzz",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex=opened["bundle_sha256"],
        )
    msg = str(exc.value)
    assert "opt-zzz" in msg
    assert "opt-a" in msg and "opt-b" in msg and "opt-c" in msg


def test_R2_vote_immutable(ledger: Application) -> None:
    opened = _open_routine(ledger)
    kwargs = dict(
        decision_id=opened["decision_id"],
        voter="voter-1",
        option="opt-a",
        confidence=0.5,
        kill_criterion="Rollback.",
        bundle_sha256_hex=opened["bundle_sha256"],
    )
    ledger.cast_vote(**kwargs)
    with pytest.raises(DomainError, match="immutable"):
        ledger.cast_vote(**{**kwargs, "option": "opt-b"})


def test_R3_closed_roster(ledger: Application) -> None:
    opened = _open_routine(ledger)
    with pytest.raises(DomainError, match="not in roster"):
        ledger.cast_vote(
            decision_id=opened["decision_id"],
            voter="stranger",
            option="opt-a",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex=opened["bundle_sha256"],
        )


def test_R4_critical_requires_unanimity(ledger: Application) -> None:
    opened = ledger.open_decision(
        question="Touch auth?",
        options=["opt-a", "opt-b"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={"paths": ["svc/auth/login.py"]},
    )
    assert opened["criticality"] == "critical"
    digest = opened["bundle_sha256"]
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-1",
        option="opt-a",
        confidence=0.8,
        kill_criterion="Auth regression.",
        bundle_sha256_hex=digest,
    )
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-2",
        option="opt-a",
        confidence=0.8,
        kill_criterion="Auth regression.",
        bundle_sha256_hex=digest,
    )
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-3",
        option="opt-b",
        confidence=0.8,
        kill_criterion="Auth regression.",
        bundle_sha256_hex=digest,
    )
    result = ledger.resolve_decision(opened["decision_id"])
    assert result["verdict"] == "deny"
    assert result["reason"] == "dissent_on_critical"
    assert {"voter": "voter-3", "option": "opt-b"} in result["dissent"]


def test_R4_routine_majority_and_tie(ledger: Application) -> None:
    opened = _open_routine(ledger)
    digest = opened["bundle_sha256"]
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-1",
        option="opt-a",
        confidence=0.7,
        kill_criterion="Fail CI.",
        bundle_sha256_hex=digest,
    )
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-2",
        option="opt-a",
        confidence=0.7,
        kill_criterion="Fail CI.",
        bundle_sha256_hex=digest,
    )
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-3",
        option="opt-b",
        confidence=0.7,
        kill_criterion="Fail CI.",
        bundle_sha256_hex=digest,
    )
    assert ledger.resolve_decision(opened["decision_id"])["verdict"] == "allow"

    opened2 = _open_routine(ledger, question="Tie case?")
    digest2 = opened2["bundle_sha256"]
    for voter, option in [
        ("voter-1", "opt-a"),
        ("voter-2", "opt-b"),
        ("voter-3", "opt-c"),
    ]:
        ledger.cast_vote(
            decision_id=opened2["decision_id"],
            voter=voter,
            option=option,
            confidence=0.5,
            kill_criterion="Fail CI.",
            bundle_sha256_hex=digest2,
        )
    tied = ledger.resolve_decision(opened2["decision_id"])
    assert tied["verdict"] == "deny"
    assert tied["reason"] == "quorum_not_met"


def test_R5_incompleteness_is_deny(ledger: Application) -> None:
    opened = _open_routine(ledger)
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-1",
        option="opt-a",
        confidence=0.5,
        kill_criterion="Fail CI.",
        bundle_sha256_hex=opened["bundle_sha256"],
    )
    result = ledger.resolve_decision(opened["decision_id"])
    assert result["verdict"] == "deny"
    assert result["reason"] == "quorum_not_met"
    assert "voter-2" in result["quorum"]["missing"]
    assert "voter-3" in result["quorum"]["missing"]

    unknown = ledger.resolve_decision("d-UNKNOWN000000000000000000")
    assert unknown["verdict"] == "deny"
    assert unknown["reason"] == "quorum_not_met"
    assert unknown["quorum"]["missing"]


def test_R6_bundle_mismatch(ledger: Application) -> None:
    opened = _open_routine(ledger)
    with pytest.raises(DomainError, match="bundle_sha256 mismatch"):
        ledger.cast_vote(
            decision_id=opened["decision_id"],
            voter="voter-1",
            option="opt-a",
            confidence=0.5,
            kill_criterion="Fail CI.",
            bundle_sha256_hex="0" * 64,
        )


def test_R7_deadline_evaluated_at_read(ledger: Application) -> None:
    opened = _open_routine(ledger, ttl_seconds=0)
    ledger.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-1",
        option="opt-a",
        confidence=0.5,
        kill_criterion="Fail CI.",
        bundle_sha256_hex=opened["bundle_sha256"],
    )
    # Resolve slightly after open; ttl=0 means deadline == open time.
    later = datetime.now(timezone.utc) + timedelta(milliseconds=5)
    result = ledger.resolve_decision(opened["decision_id"], now=later)
    assert result["verdict"] == "deny"
    assert result["reason"] == "deadline_passed"

    view = ledger.get_decision(opened["decision_id"], now=later)
    assert view["deadline_passed"] is True


def test_R8_denial_names_the_gap(ledger: Application) -> None:
    opened = _open_routine(ledger)
    result = ledger.resolve_decision(opened["decision_id"])
    assert result["verdict"] == "deny"
    assert result["reason"] == "quorum_not_met"
    assert result["quorum"]["missing"] == ["voter-1", "voter-2", "voter-3"]


def test_resolve_idempotent(ledger: Application) -> None:
    opened = _open_routine(ledger)
    digest = opened["bundle_sha256"]
    for voter in opened["voters"]:
        ledger.cast_vote(
            decision_id=opened["decision_id"],
            voter=voter,
            option="opt-a",
            confidence=0.9,
            kill_criterion="Fail CI.",
            bundle_sha256_hex=digest,
        )
    first = ledger.resolve_decision(opened["decision_id"])
    second = ledger.resolve_decision(opened["decision_id"])
    assert second["reason"] == "already_resolved"
    assert second["verdict"] == first["verdict"]
    assert second["chosen_option"] == first["chosen_option"]
    import json

    count = sum(
        1
        for ln in ledger.ledger_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and json.loads(ln)["event"] == "decision.resolved"
    )
    assert count == 1


def test_replay_after_new_process(tmp_cwd: Path) -> None:
    root = tmp_cwd / "decisions"
    rules = tmp_cwd / "arbiter.rules.yaml"
    first = create_application(root=root, rules=rules)
    opened = _open_routine(first)
    digest = opened["bundle_sha256"]
    first.cast_vote(
        decision_id=opened["decision_id"],
        voter="voter-1",
        option="opt-a",
        confidence=0.5,
        kill_criterion="Fail CI.",
        bundle_sha256_hex=digest,
    )
    snapshot = first.get_decision(opened["decision_id"])

    second = create_application(root=root, rules=rules)
    restored = second.get_decision(opened["decision_id"])
    assert restored["decision_id"] == snapshot["decision_id"]
    assert restored["votes"] == snapshot["votes"]
    assert restored["missing_voters"] == snapshot["missing_voters"]
    assert restored["status"] == "open"


def test_caller_cannot_demote_critical(ledger: Application) -> None:
    opened = ledger.open_decision(
        question="Auth change?",
        options=["yes", "no"],
        voters=["v1"],
        evidence={"paths": ["svc/auth/x.py"]},
        criticality="routine",
    )
    assert opened["criticality"] == "critical"
    state = ledger.replay(opened["decision_id"])
    assert state is not None
    assert state.criticality_source == "classifier"


def test_caller_can_escalate_routine(ledger: Application) -> None:
    opened = ledger.open_decision(
        question="Docs only?",
        options=["yes", "no"],
        voters=["v1"],
        evidence=ROUTINE_EVIDENCE,
        criticality="critical",
    )
    assert opened["criticality"] == "critical"
    state = ledger.replay(opened["decision_id"])
    assert state is not None
    assert state.criticality_source == "caller_escalated"


def test_bundle_stored_beside_ledger(ledger: Application) -> None:
    opened = _open_routine(ledger)
    path = ledger.bundles_dir / f"{opened['bundle_sha256']}.json"
    assert path.is_file()
    assert bundle_sha256(__import__("json").loads(path.read_text())) == opened[
        "bundle_sha256"
    ]


def test_concurrent_appends_all_parse(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from arbiter.adapters.outbound.jsonl_event_store import JsonlEventStore
    from arbiter.domain.events import HoldAccepted

    store = JsonlEventStore(tmp_path / "ledger.jsonl")

    def _one(i: int) -> None:
        store.append(
            HoldAccepted(
                at="2026-01-01T00:00:00.000Z",
                approval_id=f"a{i}",
                call_id=f"c{i}",
                mcp_server_id="s",
                tool_name="t",
                arguments_hash=f"{i:064x}",
                expires_at="2026-01-01T00:01:00.000Z",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_one, range(40)))
    rows = store.read_all_wire()
    assert len(rows) == 40
    assert {r["approval_id"] for r in rows} == {f"a{i}" for i in range(40)}


def test_ledger_writable_false_on_directory(tmp_path: Path) -> None:
    from arbiter.adapters.outbound.jsonl_event_store import ledger_writable

    assert ledger_writable(tmp_path / "ledger.jsonl") is True
    assert ledger_writable(tmp_path) is False


_RULE = {
    "kind": "require_contract_test",
    "path_glob": "src/**",
    "detail": "writes under src require contract test",
    "rule_id": "rule-src-contract",
}


def test_open_and_get_decision_surface_deps_and_rule(ledger: Application) -> None:
    parent = _open_routine(ledger)
    opened = ledger.open_decision(
        question="Require contract tests under src/**",
        options=["allow", "deny"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={"rule": True},
        criticality="routine",
        scope=["policy/rule"],
        depends_on=[parent["decision_id"]],
        establishes_rule=_RULE,
    )
    assert opened["depends_on"] == [parent["decision_id"]]
    assert opened["establishes_rule"] == _RULE
    got = ledger.get_decision(opened["decision_id"])
    assert got["depends_on"] == [parent["decision_id"]]
    assert got["establishes_rule"] == _RULE
    wire = next(
        e
        for e in ledger.read_all_wire()
        if e.get("event") == "decision.opened"
        and e["decision_id"] == opened["decision_id"]
    )
    assert wire["depends_on"] == [parent["decision_id"]]
    assert wire["establishes_rule"] == _RULE


def test_self_dependency_refused(ledger: Application) -> None:
    ledger._new_id = lambda: "d-loop"
    with pytest.raises(DomainError, match="cycle"):
        _open_routine(ledger, depends_on=["d-loop"])
