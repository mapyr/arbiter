"""Compatibility facade — prefer ``arbiter.application.app.Application``."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError as LedgerError
from arbiter.domain.model import Decision as DecisionState
from arbiter.domain.timeutil import format_iso, parse_iso

__all__ = [
    "DecisionState",
    "Ledger",
    "LedgerError",
    "format_iso",
    "parse_iso",
    "utc_now",
    "new_decision_id",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_decision_id() -> str:
    from arbiter.adapters.outbound.ulid_ids import UlidDecisionIdGenerator

    return UlidDecisionIdGenerator().new_decision_id()


def Ledger(*, root: Path, rules_path: Path):  # noqa: N802 — historic constructor name
    """Build the application rooted at *root* (ledger.jsonl beside bundles/)."""
    return create_application(root=Path(root), rules=Path(rules_path))
