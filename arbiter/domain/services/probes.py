"""Closed evidence-probe catalog and material composition.

Execution is performed by the application layer; this module is pure:
validate requests, truncate results, compose prompt material, hash inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from arbiter.domain.errors import DomainError
from arbiter.domain.services.canonical import bundle_sha256, canonical_json_bytes

# Hard limits — no exceptions.
MAX_PROBES_PER_VOTER_PER_ROUND = 2
MAX_PROBE_RESULT_BYTES = 2048

PROBE_SHOW_FILE = "show_file"
PROBE_SHOW_TEST_SUMMARY = "show_test_summary"
PROBE_SHOW_PATH_HISTORY = "show_path_history"
PROBE_SHOW_PRIOR_DECISIONS = "show_prior_decisions"

CLOSED_PROBES = frozenset(
    {
        PROBE_SHOW_FILE,
        PROBE_SHOW_TEST_SUMMARY,
        PROBE_SHOW_PATH_HISTORY,
        PROBE_SHOW_PRIOR_DECISIONS,
    }
)


@dataclass(frozen=True)
class ProbeRequest:
    probe: str
    params: dict[str, str]
    voter: str
    round: int


def parse_probe_request(
    payload: Mapping[str, Any],
    *,
    voter: str,
    round_n: int,
    changed_paths: Sequence[str] | None = None,
) -> ProbeRequest | str:
    """Parse a non-vote probe request. Returns error string on failure."""
    probe = payload.get("probe")
    if not isinstance(probe, str) or probe not in CLOSED_PROBES:
        return f"probe must be one of {sorted(CLOSED_PROBES)}"
    params_raw = payload.get("params") or {}
    if not isinstance(params_raw, dict):
        return "params must be an object"
    params: dict[str, str] = {}
    for key, value in params_raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return "params keys and values must be strings"
        params[key] = value
    err = validate_probe_params(probe, params, changed_paths=changed_paths)
    if err is not None:
        return err
    return ProbeRequest(probe=probe, params=params, voter=voter, round=round_n)


def validate_probe_params(
    probe: str,
    params: Mapping[str, str],
    *,
    changed_paths: Sequence[str] | None = None,
) -> str | None:
    if probe == PROBE_SHOW_FILE:
        path = params.get("path")
        if not path:
            return "show_file requires params.path"
        allowed = list(changed_paths or ())
        if allowed and path not in allowed:
            return f"show_file path {path!r} not in changed set"
        return None
    if probe == PROBE_SHOW_TEST_SUMMARY:
        # No free params — arbiter picks the stored summary key.
        return None
    if probe == PROBE_SHOW_PATH_HISTORY:
        path = params.get("path")
        if not path:
            return "show_path_history requires params.path"
        return None
    if probe == PROBE_SHOW_PRIOR_DECISIONS:
        # Optional scope substring filter from closed param.
        return None
    return f"unknown probe {probe!r}"


def truncate_probe_result(text: str, *, max_bytes: int = MAX_PROBE_RESULT_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes]
    # Avoid splitting mid-codepoint.
    while clipped and (clipped[-1] & 0xC0) == 0x80:
        clipped = clipped[:-1]
    return clipped.decode("utf-8", errors="ignore") + "\n…[truncated]"


def compose_material(
    base_evidence: Mapping[str, Any],
    probe_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Base bundle ⊕ ordered probe results — input to prompt hashing."""
    return {
        "base": dict(base_evidence),
        "probes": [dict(p) for p in probe_results],
    }


def material_sha256(
    base_evidence: Mapping[str, Any],
    probe_results: Sequence[Mapping[str, Any]],
) -> str:
    return bundle_sha256(compose_material(base_evidence, probe_results))


def assert_probe_budget(already: int, *, max_n: int = MAX_PROBES_PER_VOTER_PER_ROUND) -> None:
    if already >= max_n:
        raise DomainError(
            f"probe budget exhausted: max {max_n} probes per voter per round"
        )


def probe_result_digest(result_text: str) -> str:
    return bundle_sha256({"result": result_text})


def material_canonical_size(material: Mapping[str, Any]) -> int:
    return len(canonical_json_bytes(material))
