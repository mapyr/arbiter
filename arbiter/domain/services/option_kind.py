"""Map closed-set option strings to semantic verdict kinds."""

from __future__ import annotations

from typing import Literal

OptionKind = Literal["allow", "deny", "allow_narrow", "escalate_to_human"]

ALLOW = "allow"
DENY = "deny"
ESCALATE = "escalate_to_human"
NARROW_PREFIX = "allow_narrow:"


def option_kind(option: str) -> OptionKind:
    """Classify a closed-set option without inventing values."""
    if option == DENY or option.startswith("deny:"):
        return "deny"
    if option == ESCALATE or option.startswith("escalate:"):
        return "escalate_to_human"
    if option.startswith(NARROW_PREFIX):
        return "allow_narrow"
    if option == ALLOW or option.startswith("allow:"):
        return "allow"
    # Custom closed options (tests / legacy) remain proceed-class.
    return "allow"


def is_proceed_kind(kind: OptionKind) -> bool:
    return kind in ("allow", "allow_narrow")


def parse_narrow_spec(option: str) -> dict[str, str]:
    """Parse ``allow_narrow:k=v;k2=v2`` into a dict. Empty if not narrow."""
    if not option.startswith(NARROW_PREFIX):
        return {}
    body = option[len(NARROW_PREFIX) :]
    out: dict[str, str] = {}
    for part in body.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    return out
