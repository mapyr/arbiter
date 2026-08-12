"""Compatibility re-export."""

from arbiter.adapters.outbound.yaml_rules_source import load_rules_yaml
from arbiter.domain.services.classify import Classification, classify, path_matches
from pathlib import Path
from typing import Any


def load_rules_file(path: Path) -> dict[str, Any] | None:
    from arbiter.adapters.outbound.yaml_rules_source import YamlRulesSource

    return YamlRulesSource(path).load()


__all__ = [
    "Classification",
    "classify",
    "load_rules_file",
    "load_rules_yaml",
    "path_matches",
]
