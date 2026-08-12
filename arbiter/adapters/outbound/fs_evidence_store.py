"""Filesystem evidence bundle store under ``bundles/<sha256>.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arbiter.domain.errors import DomainError
from arbiter.domain.services.canonical import bundle_sha256, canonical_json_bytes


class FsEvidenceStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def store(self, evidence: dict[str, Any]) -> str:
        digest = bundle_sha256(evidence)
        path = self.directory / f"{digest}.json"
        if not path.exists():
            path.write_bytes(canonical_json_bytes(evidence))
        return digest

    def load(self, digest: str) -> dict[str, Any]:
        path = self.directory / f"{digest}.json"
        if not path.exists():
            raise DomainError(f"missing evidence bundle: {digest}")
        return json.loads(path.read_text(encoding="utf-8"))
