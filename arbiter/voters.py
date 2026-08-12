"""Compatibility re-export."""

from arbiter.adapters.outbound.yaml_voters_source import (
    YamlVotersSource,
    default_voters_path,
)
from arbiter.application.voters_config import (
    VotersConfig,
    VoterSpec,
    assert_roster_matches_config,
    parse_voters_config,
)
from pathlib import Path


def load_voters_file(path: Path | None = None):
    return YamlVotersSource(path).load()


__all__ = [
    "VotersConfig",
    "VoterSpec",
    "assert_roster_matches_config",
    "default_voters_path",
    "load_voters_file",
    "parse_voters_config",
]
