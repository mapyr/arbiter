"""Load arbiter.voters.yaml via PyYAML."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from arbiter.application.voters_config import VotersConfig, parse_voters_config
from arbiter.domain.errors import DomainError


def load_voters_file(path: Path | None = None) -> VotersConfig | None:
    path = Path(path) if path is not None else default_voters_path()
    explicit = os.environ.get("ARBITER_VOTERS_PATH")
    if not path.exists():
        if explicit:
            raise DomainError(f"ARBITER_VOTERS_PATH does not exist: {path}")
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_voters_config(raw)


def default_voters_path() -> Path:
    raw = os.environ.get("ARBITER_VOTERS_PATH")
    if raw:
        return Path(raw)
    return Path.cwd() / "arbiter.voters.yaml"
