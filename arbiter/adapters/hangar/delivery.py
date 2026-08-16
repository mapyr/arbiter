"""Hangar ``ApprovalDelivery`` adapter — notify-only, resolve out-of-band (2.6.0)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from arbiter.adapters.hangar.config import parse_hangar_channel_config, refuse_start
from arbiter.adapters.hangar.resolve_client import HttpApprovalResolver
from arbiter.domain.services.intercept import parse_intercept_rules
from arbiter.application.services.hold_adjudicator import HeldCall, HoldAdjudicator
from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError
from arbiter.domain.events import HoldAccepted

logger = logging.getLogger(__name__)


class ArbiterApprovalDelivery:
    """Hangar ApprovalDelivery: accept notification, return immediately (Z1).

    Verdict is delivered later via the public REST resolve channel. ``send`` must
    not raise; errors are logged and swallowed per Hangar 2.6.0 protocol.
    """

    def __init__(
        self,
        *,
        adjudicator: HoldAdjudicator,
        resolver: Any,
        app: Any | None = None,
    ) -> None:
        self._adjudicator = adjudicator
        self._resolver = resolver
        self._app = app
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def app(self) -> Any:
        return self._app

    async def send(self, request: Any) -> None:
        """Accept notification immediately; adjudicate/resolve in the background."""
        approval_id = getattr(request, "approval_id", None)
        try:
            held = self._to_held(request)
            # Z1: receipt trace before any further work — even if processing dies.
            self._adjudicator.accept(held)
            task = asyncio.create_task(
                self._process(held), name=f"arbiter-hold-{held.approval_id}"
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:  # noqa: BLE001 — protocol: never raise from send
            logger.exception(
                "arbiter_approval_delivery_accept_failed",
                extra={"approval_id": approval_id},
            )

    async def _process(self, held: HeldCall) -> None:
        try:
            result = await self._adjudicator.adjudicate(held)
            await self._resolver.resolve(
                held.approval_id,
                approved=result.approved,
                reason=result.reason,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "arbiter_approval_delivery_process_failed",
                extra={"approval_id": held.approval_id},
            )
            try:
                await self._resolver.resolve(
                    held.approval_id,
                    approved=False,
                    reason="adapter_error",
                )
            except Exception:  # noqa: BLE001
                logger.exception("arbiter_resolve_deny_failed")

    def prove_wired(self) -> str:
        """Refuse start unless Z1 ``hold.accepted`` lands in the ledger."""
        if self._app is None:
            raise DomainError("delivery app not configured")
        approval_id = f"wiring-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        self._adjudicator.accept(
            HeldCall(
                approval_id=approval_id,
                mcp_server_id="__arbiter_wiring__",
                tool_name="probe",
                arguments={"probe": True},
                arguments_hash=hashlib.sha256(b'{"probe":true}').hexdigest(),
                expires_at=now + timedelta(seconds=60),
                requested_by="arbiter:wiring-probe",
                correlation_id="wiring-probe",
            )
        )
        if any(
            raw.get("event") == HoldAccepted.TYPE and raw.get("approval_id") == approval_id
            for raw in self._app.read_all_wire()
        ):
            return approval_id
        raise DomainError(
            f"wiring probe failed: hold.accepted for {approval_id!r} not in ledger"
        )

    def _to_held(self, request: Any) -> HeldCall:
        approval_id = getattr(request, "approval_id", None)
        mcp_server_id = (
            getattr(request, "mcp_server_id", None)
            or getattr(request, "provider_id", None)
        )
        tool_name = getattr(request, "tool_name", None)
        arguments = getattr(request, "arguments", None) or {}
        arguments_hash = getattr(request, "arguments_hash", None)
        expires_at = getattr(request, "expires_at", None)
        if not approval_id or not mcp_server_id or not tool_name:
            raise DomainError("ApprovalRequest missing required fields")
        if not isinstance(arguments_hash, str) or not arguments_hash:
            raise DomainError("ApprovalRequest.arguments_hash required (I1 key)")
        if not isinstance(expires_at, datetime):
            raise DomainError("ApprovalRequest.expires_at required (I3 budget)")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        rationale = None
        if isinstance(arguments, dict):
            for key in ("rationale", "reason", "justification"):
                if isinstance(arguments.get(key), str):
                    rationale = arguments[key]
                    break
        return HeldCall(
            approval_id=str(approval_id),
            mcp_server_id=str(mcp_server_id),
            tool_name=str(tool_name),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
            arguments_hash=arguments_hash,
            expires_at=expires_at,
            requested_by=getattr(request, "requested_by", None),
            tenant_id=getattr(request, "tenant_id", None),
            correlation_id=str(getattr(request, "correlation_id", "") or ""),
            agent_rationale=rationale,
        )


def create_delivery(channel_config: dict[str, Any] | None) -> ArbiterApprovalDelivery:
    """Entry-point factory for ``mcp_hangar.approvals.delivery`` / name ``arbiter``.

    Signature (Hangar 2.6.0): ``(channel_config: dict) -> ApprovalDelivery``.
    """
    try:
        cfg = parse_hangar_channel_config(channel_config)
    except DomainError as exc:
        refuse_start(str(exc))
        raise  # pragma: no cover

    try:
        raw_rules = yaml.safe_load(cfg.intercept_rules_path.read_text(encoding="utf-8"))
        intercept = parse_intercept_rules(raw_rules)
    except DomainError as exc:
        refuse_start(str(exc))
        raise  # pragma: no cover
    except OSError as exc:
        refuse_start(f"cannot read intercept rules: {exc}")
        raise  # pragma: no cover

    resolver = HttpApprovalResolver(
        base_url=cfg.resolve_base_url,
        api_key=cfg.resolve_token,
    )
    try:
        app = create_application(
            root=cfg.data_dir,
            rules=cfg.rules_path,
            voters=cfg.voters_path,
        )
        adjudicator = HoldAdjudicator(
            app,
            intercept=intercept,
            resolver_principal=cfg.principal_id,
            hold_margin_seconds=cfg.hold_margin_seconds,
            min_round_seconds=cfg.min_round_seconds,
        )
        delivery = ArbiterApprovalDelivery(
            adjudicator=adjudicator, resolver=resolver, app=app
        )
        delivery.prove_wired()
    except DomainError as exc:
        refuse_start(str(exc))
        raise  # pragma: no cover
    except OSError as exc:
        refuse_start(f"ledger not writable: {exc}")
        raise  # pragma: no cover
    return delivery


__all__ = [
    "ArbiterApprovalDelivery",
    "create_delivery",
]
