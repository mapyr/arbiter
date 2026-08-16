"""Load arbiter.rules.yaml via PyYAML (fail-closed on missing/invalid)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_rules_yaml(text: str) -> dict[str, Any]:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("rules must be a mapping")
    return raw


class YamlRulesSource:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            return load_rules_yaml(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            return None
