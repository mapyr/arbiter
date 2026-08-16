"""Synchronous hold inbound (Cursor hook / CLI).

Same ``HoldAdjudicator`` as Hangar delivery: ``hold.accepted`` then
``hold.adjudicated``. No Hangar resolve HTTP — the caller applies approve|deny.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from arbiter.application.services.hold_adjudicator import HeldCall, HoldAdjudicator
from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError
from arbiter.domain.services.intercept import parse_intercept_rules


def arguments_hash(arguments: dict[str, Any]) -> str:
    raw = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def intercept_rules_path() -> Path:
    raw = os.environ.get("ARBITER_INTERCEPT_PATH")
    if raw:
        return Path(raw)
    return Path.cwd() / "arbiter.intercept.yaml"


def load_adjudicator() -> HoldAdjudicator:
    path = intercept_rules_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DomainError(f"ARBITER_INTERCEPT_PATH missing or unreadable: {path}") from exc
    intercept = parse_intercept_rules(raw)
    app = create_application()
    principal = os.environ.get("ARBITER_HANGAR_PRINCIPAL_ID") or "service:arbiter"
    return HoldAdjudicator(app, intercept=intercept, resolver_principal=principal)


async def run_hold(
    *,
    mcp_server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: float = 180.0,
    approval_id: str | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Accept + adjudicate. Caller (hook) maps ``approved`` to allow/deny."""
    args = dict(arguments or {})
    now = datetime.now(timezone.utc)
    held = HeldCall(
        approval_id=approval_id or f"hold-{uuid.uuid4().hex}",
        mcp_server_id=str(mcp_server),
        tool_name=str(tool),
        arguments=args,
        arguments_hash=arguments_hash(args),
        expires_at=now + timedelta(seconds=max(1.0, float(timeout_seconds))),
        requested_by=requested_by or os.environ.get("USER"),
        correlation_id="sync-hold",
    )
    adj = load_adjudicator()
    adj.accept(held)
    result = await adj.adjudicate(held)
    return {
        "approved": result.approved,
        "reason": result.reason,
        "path": result.path,
        "decision_id": result.decision_id,
        "call_id": result.call_id,
        "approval_id": held.approval_id,
    }
