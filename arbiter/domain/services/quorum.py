"""Quorum thresholds and verdict resolution — pure function, single decision point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from arbiter.domain.services.option_kind import (
    ALLOW,
    NARROW_PREFIX,
    is_proceed_kind,
    option_kind,
    parse_narrow_spec,
)

Verdict = Literal["allow", "deny", "allow_narrow", "escalate_to_human"]
Reason = Literal[
    "quorum_met",
    "quorum_not_met",
    "deadline_passed",
    "dissent_on_critical",
    "already_resolved",
]


@dataclass(frozen=True)
class QuorumResult:
    verdict: Verdict
    chosen_option: str | None
    reason: Reason
    tally: dict[str, int]
    dissent: list[dict[str, str]]
    quorum: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "chosen_option": self.chosen_option,
            "reason": self.reason,
            "tally": dict(self.tally),
            "dissent": list(self.dissent),
            "quorum": {
                "required": self.quorum["required"],
                "got": self.quorum["got"],
                "missing": list(self.quorum["missing"]),
            },
        }


def votes_required(voters: Sequence[str], criticality: str) -> int:
    """Number of cast votes required before an allow is possible."""
    return len(voters)


def majority_threshold(roster_size: int) -> int:
    """Strict majority of the roster: ``> len(voters) / 2``."""
    return roster_size // 2 + 1


def _tally(votes: Mapping[str, str], options: Sequence[str]) -> dict[str, int]:
    counts = {opt: 0 for opt in options}
    for option in votes.values():
        counts[option] = counts.get(option, 0) + 1
    return counts


def _leader(tally: Mapping[str, int]) -> str | None:
    if not tally:
        return None
    best = max(tally.values())
    leaders = [opt for opt, n in tally.items() if n == best and n > 0]
    if len(leaders) == 1:
        return leaders[0]
    return None


def _dissent(
    votes: Mapping[str, str], chosen: str | None
) -> list[dict[str, str]]:
    if chosen is None:
        return [{"voter": v, "option": o} for v, o in sorted(votes.items())]
    return [
        {"voter": v, "option": o}
        for v, o in sorted(votes.items())
        if o != chosen
    ]


def _narrow_restrictiveness(option: str) -> tuple[int, int, str]:
    """Sort key: more restrictive first (lower ttl, then stable string)."""
    spec = parse_narrow_spec(option)
    ttl_raw = spec.get("ttl")
    try:
        ttl = int(ttl_raw) if ttl_raw is not None else 10**9
    except ValueError:
        ttl = 10**9
    # Lower ttl = more restrictive; prefer options that declare ttl.
    has_ttl = 0 if ttl_raw is not None else 1
    return (has_ttl, ttl, option)


def _is_standard_proceed_option(option: str) -> bool:
    """True for closed proceed forms ``allow`` / ``allow:*`` / ``allow_narrow:*``.

    Custom ballot labels (``opt-a``) are proceed-kind for legacy tallies but are
    *not* reconciled — critical splits on those stay ``dissent_on_critical``.
    """
    if option.startswith(NARROW_PREFIX):
        return True
    if option == ALLOW or option.startswith("allow:"):
        return True
    return False


def _reconcile_proceed(votes: Mapping[str, str]) -> str:
    """Pick a proceed option when voters agree to proceed but not on exact form.

    Prefer any ``allow_narrow:*`` (most restrictive among those by ttl) over
    plain ``allow``. Critical dissent is about *whether* to proceed, not about
    narrow vs broad allow.
    """
    cast = list(votes.values())
    narrow = [o for o in cast if option_kind(o) == "allow_narrow"]
    if narrow:
        # Modal narrow option; ties broken by restrictiveness.
        counts: dict[str, int] = {}
        for o in narrow:
            counts[o] = counts.get(o, 0) + 1
        best_n = max(counts.values())
        candidates = [o for o, n in counts.items() if n == best_n]
        return sorted(candidates, key=_narrow_restrictiveness)[0]
    # All plain allow-class options — modal string, tie → lexicographic.
    counts: dict[str, int] = {}
    for o in cast:
        counts[o] = counts.get(o, 0) + 1
    best_n = max(counts.values())
    candidates = [o for o, n in counts.items() if n == best_n]
    return sorted(candidates)[0]


def resolve(
    *,
    criticality: str,
    voters: Sequence[str],
    votes: Mapping[str, str],
    options: Sequence[str],
    deadline_passed: bool,
) -> QuorumResult:
    """Compute a verdict from roster, votes, criticality, and deadline."""
    missing = [v for v in voters if v not in votes]
    tally = _tally(votes, options)
    required = votes_required(voters, criticality)
    got = len(votes)
    quorum_info = {"required": required, "got": got, "missing": missing}

    if deadline_passed:
        return QuorumResult(
            verdict="deny",
            chosen_option=None,
            reason="deadline_passed",
            tally=tally,
            dissent=_dissent(votes, _leader(tally)),
            quorum=quorum_info,
        )

    if missing:
        return QuorumResult(
            verdict="deny",
            chosen_option=None,
            reason="quorum_not_met",
            tally=tally,
            dissent=[],
            quorum=quorum_info,
        )

    leader = _leader(tally)

    if criticality == "critical":
        if leader is not None and tally[leader] == len(voters):
            kind = option_kind(leader)
            return QuorumResult(
                verdict=kind,
                chosen_option=leader,
                reason="quorum_met",
                tally=tally,
                dissent=[],
                quorum=quorum_info,
            )
        cast_options = list(votes.values())
        # Split only among standard proceed forms (allow vs allow_narrow) still
        # proceeds — pick the more restrictive form. Deny/escalate, or custom
        # ballot labels (opt-a vs opt-b), stay fail-closed.
        if cast_options and all(
            _is_standard_proceed_option(o) and is_proceed_kind(option_kind(o))
            for o in cast_options
        ):
            chosen = _reconcile_proceed(votes)
            kind = option_kind(chosen)
            return QuorumResult(
                verdict=kind,
                chosen_option=chosen,
                reason="quorum_met",
                tally=tally,
                dissent=_dissent(votes, chosen),
                quorum=quorum_info,
            )
        return QuorumResult(
            verdict="deny",
            chosen_option=None,
            reason="dissent_on_critical",
            tally=tally,
            dissent=_dissent(votes, leader),
            quorum=quorum_info,
        )

    threshold = majority_threshold(len(voters))
    if leader is not None and tally[leader] > len(voters) / 2:
        assert tally[leader] >= threshold
        kind = option_kind(leader)
        return QuorumResult(
            verdict=kind,
            chosen_option=leader,
            reason="quorum_met",
            tally=tally,
            dissent=_dissent(votes, leader),
            quorum=quorum_info,
        )

    return QuorumResult(
        verdict="deny",
        chosen_option=None,
        reason="quorum_not_met",
        tally=tally,
        dissent=_dissent(votes, leader),
        quorum=quorum_info,
    )
