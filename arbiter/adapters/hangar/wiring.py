"""Behavioural wiring probe — Hangar may degrade to noop without failing boot."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from arbiter.domain.errors import DomainError
from arbiter.domain.events import HoldAccepted


class _ProbeRequest:
    def __init__(self, approval_id: str) -> None:
        now = datetime.now(timezone.utc)
        self.approval_id = approval_id
        self.mcp_server_id = "__arbiter_wiring__"
        self.provider_id = self.mcp_server_id
        self.tool_name = "probe"
        self.arguments: dict[str, Any] = {"probe": True}
        self.arguments_hash = hashlib.sha256(b'{"probe":true}').hexdigest()
        self.requested_at = now
        self.expires_at = now + timedelta(seconds=60)
        self.channel = "arbiter"
        self.correlation_id = "wiring-probe"
        self.requested_by = "arbiter:wiring-probe"
        self.tenant_id = None


def probe_request(approval_id: str | None = None) -> _ProbeRequest:
    return _ProbeRequest(approval_id or f"wiring-{uuid.uuid4().hex}")


async def assert_delivery_wired(
    delivery: Any,
    app: Any,
    *,
    timeout_seconds: float = 2.0,
) -> str:
    """Send a probe notification and require ``hold.accepted`` in the ledger.

    Hangar unknown-channel / factory failures degrade to noop and still boot.
    This assertion is what refuses stage-4 start when notifications never arrive.
    """
    request = probe_request()
    approval_id = request.approval_id
    await delivery.send(request)

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        for raw in app.read_all_wire():
            if raw.get("event") != HoldAccepted.TYPE:
                continue
            if raw.get("approval_id") == approval_id:
                return approval_id
        await asyncio.sleep(0.02)

    raise DomainError(
        f"wiring probe failed: hold.accepted for {approval_id!r} not observed "
        f"within {timeout_seconds}s (channel may have degraded to Hangar noop)"
    )
