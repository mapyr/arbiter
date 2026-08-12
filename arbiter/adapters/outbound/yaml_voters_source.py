"""Load arbiter.voters.yaml via PyYAML."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from arbiter.application.voters_config import VotersConfig, parse_voters_config
from arbiter.domain.errors import DomainError


class YamlVotersSource:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_voters_path()

    def load(self) -> VotersConfig | None:
        explicit = os.environ.get("ARBITER_VOTERS_PATH")
        if not self.path.exists():
            if explicit:
                raise DomainError(f"ARBITER_VOTERS_PATH does not exist: {self.path}")
            return None
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        return parse_voters_config(raw)


def default_voters_path() -> Path:
    raw = os.environ.get("ARBITER_VOTERS_PATH")
    if raw:
        return Path(raw)
    return Path.cwd() / "arbiter.voters.yaml"
