"""Compatibility re-export."""

from arbiter.adapters.outbound.openai_voter_gateway import (
    CompletionResult,
    OpenAICompatibleClient,
    OpenAIVoterGateway,
)
from arbiter.application.services.prompts import (
    ParsedVote,
    build_blind_prompt,
    build_reveal_prompt,
    parse_vote_response,
    prompt_sha256,
)

__all__ = [
    "CompletionResult",
    "OpenAICompatibleClient",
    "OpenAIVoterGateway",
    "ParsedVote",
    "build_blind_prompt",
    "build_reveal_prompt",
    "parse_vote_response",
    "prompt_sha256",
]
