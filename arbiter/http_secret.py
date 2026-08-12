"""Compatibility re-export."""

from arbiter.adapters.inbound.http_secret import (
    REJECT_BODY,
    REJECT_STATUS,
    SharedSecretASGI,
)

__all__ = ["REJECT_BODY", "REJECT_STATUS", "SharedSecretASGI"]
