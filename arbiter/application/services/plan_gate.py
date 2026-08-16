"""Ensure a structured plan is voted before client mutations (plan gate)."""

from __future__ import annotations

from typing import Any

from arbiter.domain.errors import DomainError
from arbiter.domain.services.client_gate import parse_client_gate
from arbiter.domain.services.narrowing import narrowing_candidates
from arbiter.domain.services.option_kind import is_proceed_kind, option_kind
from arbiter.domain.services.plan import plan_evidence_paths, validate_plan


def get_gate_policy(app: Any) -> dict[str, Any]:
    return parse_client_gate(app.load_rules())


async def ensure_plan(
    app: Any,
    plan: dict[str, Any],
    *,
    ttl_seconds: int = 900,
    criticality: str | None = None,
    voters: list[str] | None = None,
) -> dict[str, Any]:
    normalized = validate_plan(plan)
    config = app.load_voters_config()
    if config is None:
        raise DomainError(
            "arbiter.voters.yaml required for ensure_plan "
            "(set ARBITER_VOTERS_PATH or place the file in cwd)"
        )
    roster = list(voters) if voters else list(config.ids)
    if not roster:
        raise DomainError("ensure_plan requires a non-empty voter roster")

    scope = list(normalized["scope"])
    options = normalized.get("options")
    if not options:
        options = narrowing_candidates(
            tool_name="plan",
            arguments={"paths": plan_evidence_paths(normalized)},
        )

    evidence: dict[str, Any] = {
        "plan": normalized,
        "paths": plan_evidence_paths(normalized),
    }
    if normalized.get("rationale"):
        evidence["rationale"] = normalized["rationale"]

    opened = app.open_decision(
        question=(
            "Does this structured work plan fall within what was previously "
            f"agreed? goal={normalized['goal'][:200]}"
        ),
        options=list(options),
        voters=roster,
        evidence=evidence,
        criticality=criticality,
        ttl_seconds=max(1, int(ttl_seconds)),
        opened_by="ensure_plan",
        scope=scope,
    )
    decision_id = opened["decision_id"]
    resolved = await app.run_model_quorum(decision_id)
    kind = option_kind(str(resolved.get("chosen_option") or "deny"))
    if resolved.get("verdict") in (
        "allow",
        "deny",
        "allow_narrow",
        "escalate_to_human",
    ):
        kind = str(resolved["verdict"])
    approved = is_proceed_kind(kind)  # type: ignore[arg-type]
    if kind == "escalate_to_human":
        approved = False
    if opened.get("mode") == "shadow":
        approved = True

    return {
        "approved": approved,
        "decision_id": decision_id,
        "verdict": resolved.get("verdict"),
        "chosen_option": resolved.get("chosen_option"),
        "reason": resolved.get("reason"),
        "scope": scope,
        "bundle_sha256": opened["bundle_sha256"],
        "tally": resolved.get("tally"),
        "dissent": resolved.get("dissent"),
        "plan": normalized,
    }
