"""Voters YAML: roster size 1..7, unique ids, repeat providers allowed."""

from __future__ import annotations

import random

import pytest

from arbiter.application.services.model_quorum import ModelQuorumService
from arbiter.application.voters_config import parse_voters_config
from arbiter.domain.errors import DomainError


def _voter(vid: str, *, base_url: str = "https://api.openai.com/v1", model: str) -> dict:
    return {
        "id": vid,
        "base_url": base_url,
        "model": model,
        "temperature": 0,
        "max_tokens": 100,
        "timeout_seconds": 5,
        "api_key_env": "ARBITER_VOTER_1_KEY",
    }


def test_accepts_one_through_seven() -> None:
    for n in (1, 3, 7):
        cfg = parse_voters_config(
            {
                "voters": [
                    _voter(f"v{i}", model=f"m{i}") for i in range(n)
                ]
            }
        )
        assert len(cfg.ids) == n


def test_rejects_empty_and_eight() -> None:
    with pytest.raises(DomainError, match="1..7"):
        parse_voters_config({"voters": []})
    with pytest.raises(DomainError, match="1..7"):
        parse_voters_config(
            {"voters": [_voter(f"v{i}", model=f"m{i}") for i in range(8)]}
        )


def test_same_provider_different_models() -> None:
    cfg = parse_voters_config(
        {
            "voters": [
                _voter("openai-mini", model="gpt-4o-mini"),
                _voter("openai-4o", model="gpt-4o"),
                _voter("claude", base_url="https://openrouter.ai/api/v1", model="anthropic/claude-sonnet-4"),
            ]
        }
    )
    assert cfg.ids == ["openai-mini", "openai-4o", "claude"]
    assert cfg.by_id("openai-mini").base_url == cfg.by_id("openai-4o").base_url
    assert cfg.by_id("openai-mini").model != cfg.by_id("openai-4o").model


def test_duplicate_id_still_rejected() -> None:
    with pytest.raises(DomainError, match="duplicate"):
        parse_voters_config(
            {
                "voters": [
                    _voter("openai", model="gpt-4o-mini"),
                    _voter("openai", model="gpt-4o"),
                ]
            }
        )


def test_round2_labels_cover_four_voters() -> None:
    svc = ModelQuorumService.__new__(ModelQuorumService)
    svc.rng = random.Random(0)
    mapping = svc._assign_labels(["w", "x", "y", "z"])
    assert set(mapping) == {"A", "B", "C", "D"}
    assert set(mapping.values()) == {"w", "x", "y", "z"}
