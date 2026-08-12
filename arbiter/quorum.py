"""Compatibility re-export."""

from arbiter.domain.services.quorum import (
    QuorumResult,
    majority_threshold,
    resolve,
    votes_required,
)

__all__ = ["QuorumResult", "majority_threshold", "resolve", "votes_required"]
