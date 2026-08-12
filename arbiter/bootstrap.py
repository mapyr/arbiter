"""Composition root — wire adapters into the application."""

from __future__ import annotations

import os
from pathlib import Path

from arbiter.adapters.outbound.fs_evidence_store import FsEvidenceStore
from arbiter.adapters.outbound.fs_response_store import FsResponseStore
from arbiter.adapters.outbound.jsonl_event_store import JsonlEventStore
from arbiter.adapters.outbound.openai_voter_gateway import OpenAIVoterGateway
from arbiter.adapters.outbound.system_clock import SystemClock
from arbiter.adapters.outbound.ulid_ids import UlidDecisionIdGenerator
from arbiter.adapters.outbound.yaml_rules_source import YamlRulesSource
from arbiter.adapters.outbound.yaml_voters_source import (
    YamlVotersSource,
    default_voters_path,
)
from arbiter.application.app import Application


def data_root() -> Path:
    raw = os.environ.get("ARBITER_DATA_DIR")
    if raw:
        return Path(raw)
    return Path.cwd() / "decisions"


def rules_path() -> Path:
    raw = os.environ.get("ARBITER_RULES_PATH")
    if raw:
        return Path(raw)
    return Path.cwd() / "arbiter.rules.yaml"


def create_application(
    *,
    root: Path | None = None,
    rules: Path | None = None,
    voters: Path | None = None,
    voter_gateway: OpenAIVoterGateway | None = None,
) -> Application:
    """Assemble the hexagonal application from env / explicit paths."""
    data = Path(root) if root is not None else data_root()
    data.mkdir(parents=True, exist_ok=True)
    rules_file = Path(rules) if rules is not None else rules_path()
    voters_file = Path(voters) if voters is not None else default_voters_path()

    events = JsonlEventStore(data / "ledger.jsonl")
    evidence = FsEvidenceStore(data / "bundles")
    responses = FsResponseStore(data / "responses")
    return Application(
        events=events,
        evidence=evidence,
        responses=responses,
        rules=YamlRulesSource(rules_file),
        voters_config=YamlVotersSource(voters_file),
        clock=SystemClock(),
        ids=UlidDecisionIdGenerator(),
        voter_gateway=voter_gateway if voter_gateway is not None else OpenAIVoterGateway(),
        root=data,
    )
