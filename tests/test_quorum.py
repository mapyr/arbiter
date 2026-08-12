"""Behavioral tests for quorum thresholds (pure data structures)."""

from __future__ import annotations

from arbiter.quorum import majority_threshold, resolve


def test_majority_threshold_values() -> None:
    assert majority_threshold(1) == 1
    assert majority_threshold(2) == 2
    assert majority_threshold(3) == 2
    assert majority_threshold(4) == 3


def test_critical_unanimous_allow() -> None:
    result = resolve(
        criticality="critical",
        voters=["a", "b", "c"],
        votes={"a": "opt-a", "b": "opt-a", "c": "opt-a"},
        options=["opt-a", "opt-b"],
        deadline_passed=False,
    )
    assert result.verdict == "allow"
    assert result.chosen_option == "opt-a"
    assert result.reason == "quorum_met"


def test_critical_one_dissent_deny() -> None:
    result = resolve(
        criticality="critical",
        voters=["a", "b", "c"],
        votes={"a": "opt-a", "b": "opt-a", "c": "opt-b"},
        options=["opt-a", "opt-b"],
        deadline_passed=False,
    )
    assert result.verdict == "deny"
    assert result.reason == "dissent_on_critical"
    assert {"voter": "c", "option": "opt-b"} in result.dissent


def test_critical_allow_vs_allow_narrow_proceeds_narrow() -> None:
    """Voters agree to proceed; narrow vs broad is reconciled, not denied."""
    result = resolve(
        criticality="critical",
        voters=["a", "b", "c"],
        votes={
            "a": "allow_narrow:ttl=60",
            "b": "allow",
            "c": "allow_narrow:ttl=60",
        },
        options=["allow", "deny", "allow_narrow:ttl=60"],
        deadline_passed=False,
    )
    assert result.verdict == "allow_narrow"
    assert result.chosen_option == "allow_narrow:ttl=60"
    assert result.reason == "quorum_met"
    assert result.dissent == [{"voter": "b", "option": "allow"}]


def test_critical_allow_vs_deny_still_dissent() -> None:
    result = resolve(
        criticality="critical",
        voters=["a", "b", "c"],
        votes={"a": "allow", "b": "allow", "c": "deny"},
        options=["allow", "deny"],
        deadline_passed=False,
    )
    assert result.verdict == "deny"
    assert result.reason == "dissent_on_critical"


def test_routine_two_of_three_allow() -> None:
    result = resolve(
        criticality="routine",
        voters=["a", "b", "c"],
        votes={"a": "opt-a", "b": "opt-a", "c": "opt-b"},
        options=["opt-a", "opt-b", "opt-c"],
        deadline_passed=False,
    )
    assert result.verdict == "allow"
    assert result.chosen_option == "opt-a"
    assert result.reason == "quorum_met"
    assert result.dissent == [{"voter": "c", "option": "opt-b"}]


def test_routine_tie_one_one_one_deny() -> None:
    result = resolve(
        criticality="routine",
        voters=["a", "b", "c"],
        votes={"a": "opt-a", "b": "opt-b", "c": "opt-c"},
        options=["opt-a", "opt-b", "opt-c"],
        deadline_passed=False,
    )
    assert result.verdict == "deny"
    assert result.reason == "quorum_not_met"
    assert result.chosen_option is None


def test_missing_voter_is_deny() -> None:
    result = resolve(
        criticality="routine",
        voters=["a", "b", "c"],
        votes={"a": "opt-a", "b": "opt-a"},
        options=["opt-a", "opt-b"],
        deadline_passed=False,
    )
    assert result.verdict == "deny"
    assert result.reason == "quorum_not_met"
    assert result.quorum["missing"] == ["c"]


def test_deadline_passed_is_deny() -> None:
    result = resolve(
        criticality="routine",
        voters=["a"],
        votes={"a": "opt-a"},
        options=["opt-a", "opt-b"],
        deadline_passed=True,
    )
    assert result.verdict == "deny"
    assert result.reason == "deadline_passed"
