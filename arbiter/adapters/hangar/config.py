"""Validate Hangar channel config for the arbiter delivery adapter (Hangar 2.6.0)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arbiter.domain.errors import DomainError

DEFAULT_RESOLVE_TOKEN_ENV = "ARBITER_HANGAR_RESOLVE_TOKEN"
DEFAULT_PRINCIPAL_ID_ENV = "ARBITER_HANGAR_PRINCIPAL_ID"


@dataclass(frozen=True)
class HangarChannelConfig:
    data_dir: Path
    intercept_rules_path: Path
    voters_path: Path | None
    rules_path: Path | None
    resolve_base_url: str
    resolve_token_env: str
    principal_id_env: str
    hold_margin_seconds: float
    min_round_seconds: float | None
    # Resolved at factory time from the environment (never from rules YAML).
    resolve_token: str
    principal_id: str


def parse_hangar_channel_config(raw: dict[str, Any] | None) -> HangarChannelConfig:
    if not isinstance(raw, dict):
        raise DomainError(
            "approvals.arbiter channel config required "
            "(missing hangar channel mapping)"
        )

    if raw.get("resolve_token") is not None:
        raise DomainError(
            "approvals.arbiter.resolve_token is forbidden; "
            "put credentials in the environment named by resolve_token_env"
        )

    intercept = raw.get("intercept_rules_path")
    if not isinstance(intercept, str) or not intercept.strip():
        raise DomainError(
            "approvals.arbiter.intercept_rules_path required "
            "(missing interception rules decision)"
        )
    intercept_path = Path(intercept).expanduser()
    if not intercept_path.is_file():
        raise DomainError(
            f"intercept rules file missing: {intercept_path} "
            "(refuse start — no default allow/deny surface)"
        )

    data_dir_raw = raw.get("data_dir")
    if not isinstance(data_dir_raw, str) or not data_dir_raw.strip():
        raise DomainError("approvals.arbiter.data_dir required")
    data_dir = Path(data_dir_raw).expanduser()

    resolve_base_url = raw.get("resolve_base_url")
    if not isinstance(resolve_base_url, str) or not resolve_base_url.strip():
        raise DomainError(
            "approvals.arbiter.resolve_base_url required "
            "(public Hangar POST /approvals/{id}/resolve)"
        )

    token_env = raw.get("resolve_token_env", DEFAULT_RESOLVE_TOKEN_ENV)
    principal_env = raw.get("principal_id_env", DEFAULT_PRINCIPAL_ID_ENV)
    if not isinstance(token_env, str) or not token_env.strip():
        raise DomainError("resolve_token_env must be a non-empty string")
    if not isinstance(principal_env, str) or not principal_env.strip():
        raise DomainError("principal_id_env must be a non-empty string")

    token = os.environ.get(token_env.strip())
    principal = os.environ.get(principal_env.strip())
    if not token:
        raise DomainError(
            f"missing Hangar resolve credentials: env {token_env!r} is unset "
            "(refuse start)"
        )
    if not principal:
        raise DomainError(
            f"missing Hangar resolver principal id: env {principal_env!r} is unset "
            "(refuse start)"
        )

    voters_raw = raw.get("voters_path")
    voters_path = Path(voters_raw).expanduser() if isinstance(voters_raw, str) else None
    rules_raw = raw.get("rules_path")
    rules_path = Path(rules_raw).expanduser() if isinstance(rules_raw, str) else None

    margin = raw.get("hold_margin_seconds", 5.0)
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or float(margin) < 0:
        raise DomainError("hold_margin_seconds must be a non-negative number")

    min_round = raw.get("min_round_seconds")
    if min_round is not None and (
        not isinstance(min_round, (int, float))
        or isinstance(min_round, bool)
        or float(min_round) <= 0
    ):
        raise DomainError("min_round_seconds must be a positive number when set")

    return HangarChannelConfig(
        data_dir=data_dir,
        intercept_rules_path=intercept_path,
        voters_path=voters_path,
        rules_path=rules_path,
        resolve_base_url=resolve_base_url.strip().rstrip("/"),
        resolve_token_env=token_env.strip(),
        principal_id_env=principal_env.strip(),
        hold_margin_seconds=float(margin),
        min_round_seconds=float(min_round) if min_round is not None else None,
        resolve_token=token,
        principal_id=principal,
    )


def refuse_start(message: str) -> None:
    """Refuse process start.

    Hangar's ``_build_delivery`` catches ``Exception`` and degrades to ``noop``.
    ``SystemExit`` is a ``BaseException``, so it propagates (Hangar 2.6.0).
    """
    raise SystemExit(f"arbiter hangar delivery: {message}")
