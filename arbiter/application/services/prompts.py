"""Blind / reveal prompt construction and strict vote parsing (no I/O)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from arbiter.domain.services.canonical import canonical_json_bytes

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ParsedVote:
    option: str
    confidence: float
    kill_criterion: str
    revision_reason: str | None = None


def build_blind_prompt(
    *,
    question: str,
    options: list[str],
    evidence: dict[str, Any],
) -> str:
    payload = {
        "role": "arbiter_voter",
        "round": 1,
        "instruction": (
            "You are a decision voter. Reply with a single JSON object only. "
            "Fields: option (exact string from options), confidence (0.0-1.0), "
            "kill_criterion (non-empty sentence). Do not invent options. "
            "Do not include any other keys."
        ),
        "question": question,
        "options": list(options),
        "evidence": evidence,
    }
    return canonical_json_bytes(payload).decode("utf-8")


def build_reveal_prompt(
    *,
    question: str,
    options: list[str],
    evidence: dict[str, Any],
    labeled_votes: list[dict[str, Any]],
    own_prior: dict[str, Any],
) -> str:
    payload = {
        "role": "arbiter_voter",
        "round": 2,
        "instruction": (
            "You previously voted. Peer votes are shown with opaque labels. "
            "Reply with a single JSON object only. Fields: option (exact string "
            "from options), confidence (0.0-1.0), kill_criterion (non-empty "
            "sentence). If you change option from your prior vote you MUST "
            "include revision_reason (non-empty sentence). Do not invent options."
        ),
        "question": question,
        "options": list(options),
        "evidence": evidence,
        "your_prior_vote": {
            "option": own_prior["option"],
            "confidence": own_prior["confidence"],
            "kill_criterion": own_prior["kill_criterion"],
        },
        "peer_votes": labeled_votes,
    }
    return canonical_json_bytes(payload).decode("utf-8")


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def parse_vote_response(
    text: str, *, options: list[str], prior_option: str | None = None
) -> ParsedVote | str:
    obj = _load_json_object(text)
    if obj is None or not isinstance(obj, dict):
        return "response is not a JSON object"
    option = obj.get("option")
    if not isinstance(option, str):
        return "option must be a string"
    if option not in options:
        return f"option {option!r} not in closed set; allowed: {options!r}"
    confidence = obj.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return "confidence must be a number in [0.0, 1.0]"
    if not 0.0 <= float(confidence) <= 1.0:
        return "confidence must be a number in [0.0, 1.0]"
    kill = obj.get("kill_criterion")
    if not isinstance(kill, str) or not kill.strip():
        return "kill_criterion must be a non-empty sentence"
    revision = obj.get("revision_reason")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        return "revision_reason must be a non-empty sentence when present"
    if prior_option is not None and option != prior_option:
        if not isinstance(revision, str) or not revision.strip():
            return "revision_reason required when option changes"
    return ParsedVote(
        option=option,
        confidence=float(confidence),
        kill_criterion=kill.strip(),
        revision_reason=revision.strip() if isinstance(revision, str) else None,
    )


def _load_json_object(text: str) -> Any | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
