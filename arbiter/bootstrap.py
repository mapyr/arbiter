"""Composition root — wire adapters into the application."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from arbiter.adapters.outbound.fs_evidence_store import FsEvidenceStore
from arbiter.adapters.outbound.fs_response_store import FsResponseStore
from arbiter.adapters.outbound.jsonl_event_store import JsonlEventStore
from arbiter.adapters.outbound.openai_voter_gateway import OpenAIVoterGateway
from arbiter.adapters.outbound.yaml_rules_source import load_rules_file
from arbiter.adapters.outbound.yaml_voters_source import (
    default_voters_path,
    load_voters_file,
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
        load_rules=lambda: load_rules_file(rules_file),
        load_voters=lambda: load_voters_file(voters_file),
        now=lambda: datetime.now(timezone.utc),
        new_id=lambda: f"d-{uuid.uuid4().hex}",
        voter_gateway=voter_gateway
        if voter_gateway is not None
        else OpenAIVoterGateway(),
        root=data,
    )
