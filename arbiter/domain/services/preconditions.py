"""Deterministic ledger preconditions.

Predicates are pure functions of the event log and call identity — no network,
no wall clock other than timestamps already stored on events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PreconditionResult:
    ok: bool
    predicate: str
    reason: str
    # True when the call class has a ledger predicate; False when skipped.
    applicable: bool = True


# Tools that require a prior trial/dry-run with the same arguments_hash.
_MIGRATE_APPLY_TOOLS = frozenset(
    {
        "migrate.apply",
        "migrate",
        "db.migrate.apply",
    }
)
_TRIAL_TOOLS = frozenset(
    {
        "migrate.dry_run",
        "migrate.trial",
        "db.migrate.dry_run",
    }
)


def check_preconditions(
    wire_events: Sequence[Mapping[str, Any]],
    *,
    tool_name: str,
    arguments_hash: str,
    mcp_server_id: str = "",
) -> PreconditionResult:
    """Run all known ledger predicates for this call.

    Returns the first failing applicable predicate, or ok if none fail.
    Predicates that cannot be expressed from the ledger alone are reported
    via :func:`inexpressible_predicates` (measurement / ceiling signal).
    """
    if tool_name in _MIGRATE_APPLY_TOOLS:
        return _migrate_apply_requires_trial(
            wire_events,
            arguments_hash=arguments_hash,
            mcp_server_id=mcp_server_id,
        )
    return PreconditionResult(
        ok=True,
        predicate="none",
        reason="no_applicable_precondition",
        applicable=False,
    )


def _migrate_apply_requires_trial(
    wire_events: Sequence[Mapping[str, Any]],
    *,
    arguments_hash: str,
    mcp_server_id: str,
) -> PreconditionResult:
    """Allow migrate apply only when a matching trial result is in the ledger.

    ``hold.adjudicated`` does not carry ``arguments_hash``; correlate via
    ``hold.accepted`` (hash + tool) then require a completed adjudication for
    that ``call_id``.
    """
    trial_call_ids: set[str] = set()
    for raw in wire_events:
        if raw.get("event") != "hold.accepted":
            continue
        if raw.get("tool_name") not in _TRIAL_TOOLS:
            continue
        if raw.get("arguments_hash") != arguments_hash:
            continue
        if mcp_server_id and raw.get("mcp_server_id") not in (None, "", mcp_server_id):
            continue
        cid = raw.get("call_id")
        if isinstance(cid, str) and cid:
            trial_call_ids.add(cid)
    for raw in wire_events:
        if raw.get("event") != "hold.adjudicated":
            continue
        if raw.get("call_id") not in trial_call_ids:
            continue
        # Trial must have a recorded outcome (approved True/False).
        if "approved" not in raw:
            continue
        return PreconditionResult(
            ok=True,
            predicate="migrate_apply_requires_trial",
            reason=f"trial_recorded:call_id={raw.get('call_id')}",
            applicable=True,
        )
    return PreconditionResult(
        ok=False,
        predicate="migrate_apply_requires_trial",
        reason="missing_trial_in_ledger",
        applicable=True,
    )


def inexpressible_predicates() -> list[dict[str, str]]:
    """Predicates operators asked for that cannot be ledger-pure.

    Recorded for S2 falsification measurement — not enforced at runtime.
    """
    return [
        {
            "id": "external_ci_green",
            "wanted": "allow deploy only if CI pipeline is green",
            "blocker": "CI status is outside the arbiter ledger (network/clock)",
        },
        {
            "id": "wall_clock_business_hours",
            "wanted": "allow writes only during business hours",
            "blocker": "requires live wall clock, not event timestamps alone",
        },
    ]
