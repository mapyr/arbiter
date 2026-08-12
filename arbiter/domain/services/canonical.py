"""Canonical JSON serialization and SHA-256 hashing for evidence bundles."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize *value* to canonical JSON UTF-8 bytes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def bundle_sha256(value: Any) -> str:
    """Return the hex SHA-256 of the canonical JSON encoding of *value*."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
