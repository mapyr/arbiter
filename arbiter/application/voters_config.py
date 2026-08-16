"""Voter roster config (pure) — loaded by adapters from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arbiter.domain.errors import DomainError


@dataclass(frozen=True)
class VoterSpec:
    id: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    api_key_env: str | None = None


@dataclass(frozen=True)
class VotersConfig:
    voters: tuple[VoterSpec, ...]
    round_deadline_seconds: float
    reveal_round: bool
    shadow_mode: bool = False
    baseline_voter: str | None = None

    @property
    def ids(self) -> list[str]:
        return [v.id for v in self.voters]

    def by_id(self, voter_id: str) -> VoterSpec:
        for v in self.voters:
            if v.id == voter_id:
                return v
        raise KeyError(voter_id)


def parse_voters_config(raw: Any) -> VotersConfig:
    if not isinstance(raw, dict):
        raise DomainError("voters config must be a mapping")
    voters_raw = raw.get("voters")
    if not isinstance(voters_raw, list) or not (1 <= len(voters_raw) <= 7):
        raise DomainError("voters config must declare 1..7 voters")
    seen: set[str] = set()
    voters: list[VoterSpec] = []
    for item in voters_raw:
        if not isinstance(item, dict):
            raise DomainError("each voter must be a mapping")
        vid = item.get("id")
        if not isinstance(vid, str) or not vid:
            raise DomainError("voter.id must be a non-empty string")
        if vid in seen:
            raise DomainError(f"duplicate voter id: {vid!r}")
        seen.add(vid)
        base_url = item.get("base_url")
        model = item.get("model")
        if not isinstance(base_url, str) or not base_url:
            raise DomainError(f"voter {vid!r}: base_url required")
        if not isinstance(model, str) or not model:
            raise DomainError(f"voter {vid!r}: model required")
        temperature = float(item.get("temperature", 0))
        max_tokens = int(item.get("max_tokens", 1200))
        timeout_seconds = float(item.get("timeout_seconds", 45))
        api_key_env = item.get("api_key_env")
        if api_key_env is not None and (
            not isinstance(api_key_env, str) or not api_key_env
        ):
            raise DomainError(f"voter {vid!r}: api_key_env must be a non-empty string")
        voters.append(
            VoterSpec(
                id=vid,
                base_url=base_url.rstrip("/"),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                api_key_env=api_key_env,
            )
        )
    deadline = raw.get("round_deadline_seconds", 60)
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or deadline <= 0:
        raise DomainError("round_deadline_seconds must be a positive number")
    reveal = raw.get("reveal_round", True)
    if not isinstance(reveal, bool):
        raise DomainError("reveal_round must be a boolean")
    shadow = raw.get("shadow_mode", False)
    if not isinstance(shadow, bool):
        raise DomainError("shadow_mode must be a boolean")
    baseline = raw.get("baseline_voter")
    if baseline is not None:
        if not isinstance(baseline, str) or not baseline:
            raise DomainError("baseline_voter must be a non-empty string")
        if baseline not in {v.id for v in voters}:
            raise DomainError(
                f"baseline_voter {baseline!r} must be one of the roster ids"
            )
    return VotersConfig(
        voters=tuple(voters),
        round_deadline_seconds=float(deadline),
        reveal_round=reveal,
        shadow_mode=shadow,
        baseline_voter=baseline,
    )


def assert_roster_matches_config(voters: list[str], config: VotersConfig) -> None:
    configured = config.ids
    open_set = set(voters)
    conf_set = set(configured)
    if open_set == conf_set and len(voters) == len(configured):
        return
    only_open = sorted(open_set - conf_set)
    only_config = sorted(conf_set - open_set)
    parts = [
        f"voters mismatch: open_decision={list(voters)!r}, "
        f"arbiter.voters.yaml={configured!r}"
    ]
    if only_open:
        parts.append(f"only in open_decision: {only_open!r}")
    if only_config:
        parts.append(f"only in arbiter.voters.yaml: {only_config!r}")
    raise DomainError("; ".join(parts))
