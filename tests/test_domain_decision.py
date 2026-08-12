"""Unit tests focused on the Decision aggregate (domain layer)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from arbiter.domain.errors import DomainError
from arbiter.domain.events import (
    DecisionOpened,
    DecisionResolved,
    QuorumRound2Opened,
    VoteCast,
    VoteFailed,
)
from arbiter.domain.model import Decision
from arbiter.domain.services.classify import apply_criticality, classify, path_matches
from arbiter.domain.services.classify import Classification
from arbiter.domain.timeutil import format_iso, parse_iso


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _opened(**kwargs) -> Decision:
    base = dict(
        decision_id="d-TEST000000000000000000001",
        question="Ship?",
        options=["opt-a", "opt-b"],
        criticality="routine",
        criticality_source="classifier",
        voters=["voter-1", "voter-2"],
        bundle_sha256="abc",
        deadline=format_iso(_now() + timedelta(hours=1)),
        opened_by="test",
        opened_at=format_iso(_now()),
    )
    base.update(kwargs)
    return Decision(**base)


def test_from_events_rebuilds_rounds_and_failures() -> None:
    at = format_iso(_now())
    events = [
        DecisionOpened(
            at=at,
            decision_id="d-1",
            question="Q?",
            options=("a", "b"),
            criticality="critical",
            criticality_source="classifier",
            voters=("v1", "v2"),
            bundle_sha256="deadbeef",
            deadline=at,
            opened_by="t",
        ),
        VoteCast(
            at=at,
            decision_id="d-1",
            voter="v1",
            option="a",
            confidence=0.5,
            kill_criterion="Fail.",
            bundle_sha256="deadbeef",
            round=1,
            revision_reason="keep",
            meta={"model": "m"},
        ),
        VoteFailed(
            at=at,
            decision_id="d-1",
            voter="v2",
            round=1,
            reason="timeout",
            detail="slow",
        ),
        QuorumRound2Opened(at=at, decision_id="d-1", labels={"A": "v1"}),
        DecisionResolved(
            at=at,
            decision_id="d-1",
            verdict="deny",
            chosen_option=None,
            reason="quorum_not_met",
            tally={"a": 1, "b": 0},
            dissent=(),
        ),
    ]
    state = Decision.from_events(events)
    assert state is not None
    assert state.effective_votes()["v1"]["revision_reason"] == "keep"
    assert state.failures[0]["reason"] == "timeout"
    assert state.reveal_labels == {"A": "v1"}
    assert state.resolution["verdict"] == "deny"


def test_from_events_ignores_orphan_vote() -> None:
    at = format_iso(_now())
    assert (
        Decision.from_events(
            [
                VoteCast(
                    at=at,
                    decision_id="d-1",
                    voter="v1",
                    option="a",
                    confidence=0.5,
                    kill_criterion="Fail.",
                    bundle_sha256="x",
                )
            ]
        )
        is None
    )


def test_cast_vote_validation_matrix() -> None:
    state = _opened()
    now = _now()
    # resolved blocks
    state.resolution = {"verdict": "allow"}
    with pytest.raises(DomainError, match="already resolved"):
        state.cast_vote(
            voter="voter-1",
            option="opt-a",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            at=now,
        )
    state.resolution = None

    with pytest.raises(DomainError, match="positive integer"):
        state.cast_vote(
            voter="voter-1",
            option="opt-a",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            round=0,
            at=now,
        )
    with pytest.raises(DomainError, match="confidence"):
        state.cast_vote(
            voter="voter-1",
            option="opt-a",
            confidence=1.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            at=now,
        )
    with pytest.raises(DomainError, match="confidence"):
        state.cast_vote(
            voter="voter-1",
            option="opt-a",
            confidence=True,  # type: ignore[arg-type]
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            at=now,
        )
    with pytest.raises(DomainError, match="kill_criterion"):
        state.cast_vote(
            voter="voter-1",
            option="opt-a",
            confidence=0.5,
            kill_criterion="  ",
            bundle_sha256_hex="abc",
            at=now,
        )
    with pytest.raises(DomainError, match="revision_reason must be"):
        state.cast_vote(
            voter="voter-1",
            option="opt-a",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            revision_reason="  ",
            at=now,
        )


def test_round2_requires_prior_and_revision() -> None:
    state = _opened()
    now = _now()
    first = state.cast_vote(
        voter="voter-1",
        option="opt-a",
        confidence=0.5,
        kill_criterion="Rollback.",
        bundle_sha256_hex="abc",
        round=1,
        at=now,
    )
    state.rounds.setdefault(1, {})["voter-1"] = {
        "option": first.option,
        "confidence": first.confidence,
        "kill_criterion": first.kill_criterion,
        "round": 1,
    }
    with pytest.raises(DomainError, match="no round 1 vote"):
        state.cast_vote(
            voter="voter-2",
            option="opt-a",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            round=2,
            at=now,
        )
    with pytest.raises(DomainError, match="revision_reason"):
        state.cast_vote(
            voter="voter-1",
            option="opt-b",
            confidence=0.5,
            kill_criterion="Rollback.",
            bundle_sha256_hex="abc",
            round=2,
            at=now,
        )
    ok = state.cast_vote(
        voter="voter-1",
        option="opt-b",
        confidence=0.5,
        kill_criterion="Rollback.",
        bundle_sha256_hex="abc",
        round=2,
        revision_reason="Changed mind.",
        at=now,
    )
    assert ok.revision_reason == "Changed mind."


def test_fail_vote_and_open_round2() -> None:
    state = _opened()
    now = _now()
    failed = state.fail_vote(
        voter="voter-1", round=1, reason="timeout", at=now, detail="x"
    )
    assert failed.reason == "timeout"
    labels = state.open_round2(labels={"A": "voter-1", "B": "voter-2"}, at=now)
    assert labels.labels["A"] == "voter-1"
    state.resolution = {"verdict": "deny"}
    with pytest.raises(DomainError, match="already resolved"):
        state.fail_vote(voter="voter-1", round=1, reason="timeout", at=now)
    state.resolution = None
    with pytest.raises(DomainError, match="not in roster"):
        state.fail_vote(voter="ghost", round=1, reason="timeout", at=now)


def test_resolve_at_emits_once() -> None:
    state = _opened(voters=["voter-1"], criticality="routine")
    now = _now()
    cast = state.cast_vote(
        voter="voter-1",
        option="opt-a",
        confidence=1.0,
        kill_criterion="Fail.",
        bundle_sha256_hex="abc",
        at=now,
    )
    state.rounds[1] = {
        "voter-1": {
            "option": cast.option,
            "confidence": cast.confidence,
            "kill_criterion": cast.kill_criterion,
            "round": 1,
        }
    }
    result, event = state.resolve_at(at=now)
    assert event is not None
    assert result.verdict == "allow"
    state.resolution = {
        "verdict": event.verdict,
        "chosen_option": event.chosen_option,
        "reason": event.reason,
        "tally": dict(event.tally),
        "dissent": list(event.dissent),
        "at": event.at,
    }
    again, no_event = state.resolve_at(at=now)
    assert no_event is None
    assert again.verdict == "allow"


def test_open_validation_errors() -> None:
    now = _now()
    rules = {"default": "routine", "critical": {"paths": [], "any_of": []}}
    kwargs = dict(
        decision_id="d-x",
        voters=["v1", "v2"],
        evidence={"paths": ["docs/x.md"]},
        rules=rules,
        criticality=None,
        ttl_seconds=60,
        opened_by="t",
        at=now,
        bundle_sha256="abc",
    )
    with pytest.raises(DomainError, match="question"):
        Decision.open(question="  ", options=["a", "b"], **kwargs)
    with pytest.raises(DomainError, match="2..8"):
        Decision.open(question="Q?", options=["a"], **{**kwargs, "voters": ["v1"]})
    with pytest.raises(DomainError, match="non-empty strings"):
        Decision.open(question="Q?", options=["a", ""], **kwargs)
    with pytest.raises(DomainError, match="unique"):
        Decision.open(question="Q?", options=["a", "a"], **kwargs)
    with pytest.raises(DomainError, match="voters must contain"):
        Decision.open(
            question="Q?",
            options=["a", "b"],
            **{**kwargs, "voters": []},
        )
    with pytest.raises(DomainError, match="voters must be unique"):
        Decision.open(
            question="Q?",
            options=["a", "b"],
            **{**kwargs, "voters": ["v1", "v1"]},
        )
    with pytest.raises(DomainError, match="ttl_seconds"):
        Decision.open(
            question="Q?",
            options=["a", "b"],
            **{**kwargs, "ttl_seconds": -1},
        )
    with pytest.raises(DomainError, match="bundle_sha256"):
        Decision.open(
            question="Q?",
            options=["a", "b"],
            **{**kwargs, "bundle_sha256": ""},
        )
    with pytest.raises(DomainError, match="evidence"):
        Decision.open(
            question="Q?",
            options=["a", "b"],
            **{**kwargs, "evidence": ["not", "object"]},  # type: ignore[arg-type]
        )


def test_classify_and_path_helpers() -> None:
    assert path_matches("./svc/auth/x.py", "svc/auth/**")
    assert classify({"paths": []}, {"default": "routine"}).criticality == "critical"
    assert classify({"paths": ["a"]}, None).reason == "no rules file"
    assert (
        classify({"paths": ["a"]}, {"critical": "bad"}).criticality == "critical"
    )
    with pytest.raises(DomainError, match="criticality must"):
        apply_criticality(Classification("routine", "default"), "maybe")
    assert apply_criticality(Classification("routine", "default"), "critical") == (
        "critical",
        "caller_escalated",
    )


def test_parse_iso_and_naive_format() -> None:
    dt = parse_iso("2026-01-01T00:00:00.000Z")
    assert format_iso(dt).endswith("Z")
    naive = datetime(2026, 1, 1, 0, 0, 0)
    assert format_iso(naive).endswith("Z")
