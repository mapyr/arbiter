"""Raw model response store under ``responses/<decision_id>/<voter>-r<round>.json``."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from arbiter.domain.services.canonical import canonical_json_bytes


class FsResponseStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def store(
        self,
        *,
        decision_id: str,
        voter: str,
        round_n: int,
        payload: dict[str, Any],
    ) -> str:
        directory = self.directory / decision_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{voter}-r{round_n}.json"
        blob = canonical_json_bytes(payload)
        path.write_bytes(blob)
        return hashlib.sha256(blob).hexdigest()
