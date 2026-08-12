"""Compatibility re-export for model quorum."""

from __future__ import annotations

import random
from typing import Any

from arbiter.application.app import Application
from arbiter.application.voters_config import VotersConfig
from arbiter.domain.errors import DomainError as LedgerError


async def run_model_quorum(
    ledger: Application,
    decision_id: str,
    *,
    config: VotersConfig | None = None,
    client: Any = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Run model quorum on an Application instance.

    ``client`` is accepted for backward compatibility and ignored when the
    application already has a voter gateway; tests that need a stub server
    pass ``config`` with base_url pointing at the stub.
    """
    if client is not None and getattr(ledger, "_voter_gateway", None) is not None:
        # Allow injecting a custom gateway for tests that still pass client=
        from arbiter.adapters.outbound.openai_voter_gateway import OpenAIVoterGateway

        if isinstance(client, OpenAIVoterGateway):
            ledger._voter_gateway = client
    return await ledger.run_model_quorum(decision_id, config=config, rng=rng)


__all__ = ["LedgerError", "run_model_quorum"]
