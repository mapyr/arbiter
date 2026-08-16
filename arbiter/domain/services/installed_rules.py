"""Rules established by decisions — enforced by runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from arbiter.domain.services.classify import path_matches
from arbiter.domain.services.scope import path_from_arguments


@dataclass(frozen=True)
class InstalledRule:
    rule_id: str
    decision_id: str
    kind: str
    path_glob: str
    detail: str


RULE_REQUIRE_CONTRACT_TEST = "require_contract_test"


def rules_from_wire(wire_events: Sequence[Mapping[str, Any]]) -> list[InstalledRule]:
    """Active rules: established and not revoked by invalidation of source decision."""
    invalidated = {
        e["decision_id"]
        for e in wire_events
        if e.get("event") == "decision.invalidated"
        and isinstance(e.get("decision_id"), str)
    }
    rules: list[InstalledRule] = []
    for raw in wire_events:
        if raw.get("event") != "rule.established":
            continue
        did = raw.get("decision_id")
        if not isinstance(did, str) or did in invalidated:
            continue
        rules.append(
            InstalledRule(
                rule_id=str(raw.get("rule_id") or did),
                decision_id=did,
                kind=str(raw.get("kind") or RULE_REQUIRE_CONTRACT_TEST),
                path_glob=str(raw.get("path_glob") or ""),
                detail=str(raw.get("detail") or ""),
            )
        )
    return rules


@dataclass(frozen=True)
class RuleCheckResult:
    ok: bool
    path: str  # rule_allow | rule_deny | no_rule
    rule_id: str | None
    reason: str


def check_installed_rules(
    rules: Sequence[InstalledRule],
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    wire_events: Sequence[Mapping[str, Any]],
) -> RuleCheckResult:
    """Enforce established rules before quorum.

    ``require_contract_test``: writes under path_glob need a prior
    ``hold.adjudicated`` / evidence marker for a contract test with approved=True.
    """
    path = path_from_arguments(arguments)
    if path is None:
        return RuleCheckResult(
            ok=True, path="no_rule", rule_id=None, reason="no_path_argument"
        )

    applicable = [
        r
        for r in rules
        if r.kind == RULE_REQUIRE_CONTRACT_TEST
        and r.path_glob
        and path_matches(path, r.path_glob)
    ]
    if not applicable:
        return RuleCheckResult(
            ok=True, path="no_rule", rule_id=None, reason="no_matching_rule"
        )

    # Write-like tools only.
    if not _looks_like_write(tool_name):
        return RuleCheckResult(
            ok=True,
            path="no_rule",
            rule_id=applicable[0].rule_id,
            reason="tool_not_write",
        )

    if _contract_test_recorded(wire_events, path=path):
        return RuleCheckResult(
            ok=True,
            path="rule_allow",
            rule_id=applicable[0].rule_id,
            reason=f"contract_test_ok:{applicable[0].rule_id}",
        )
    return RuleCheckResult(
        ok=False,
        path="rule_deny",
        rule_id=applicable[0].rule_id,
        reason=f"missing_contract_test:{applicable[0].rule_id}",
    )


def _looks_like_write(tool_name: str) -> bool:
    lower = tool_name.lower()
    return any(
        token in lower
        for token in (
            "write",
            "edit",
            "apply",
            "patch",
            "create",
            "delete",
            "migrate",
            "append",
            "rename",
        )
    )


def _contract_test_recorded(
    wire_events: Sequence[Mapping[str, Any]], *, path: str
) -> bool:
    for raw in wire_events:
        if raw.get("event") != "hold.adjudicated":
            continue
        tool = str(raw.get("tool_name") or "")
        if "contract" not in tool.lower() and "test" not in tool.lower():
            continue
        if raw.get("approved") is not True:
            continue
        # Optional path echo in reason/meta — accept any approved contract/test.
        _ = path
        return True
    return False
