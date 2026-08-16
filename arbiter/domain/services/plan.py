"""Structured work-plan validation for ``ensure_plan`` (client plan gate).

Arbiter owns the schema: agents submit JSON; formulation barriers still apply
to the derived ``open_decision`` scope/options.
"""

from __future__ import annotations

from typing import Any

from arbiter.domain.errors import DomainError


def validate_plan(plan: Any) -> dict[str, Any]:
    """Return a normalized plan dict or raise ``DomainError``."""
    if not isinstance(plan, dict):
        raise DomainError("plan must be an object")
    goal = plan.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise DomainError("plan.goal must be a non-empty string")
    steps_raw = plan.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise DomainError("plan.steps must be a non-empty array")
    steps: list[dict[str, Any]] = []
    for i, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            raise DomainError(f"plan.steps[{i}] must be an object")
        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            raise DomainError(f"plan.steps[{i}].action must be a non-empty string")
        normalized: dict[str, Any] = {"action": action.strip()}
        paths = step.get("paths")
        if paths is not None:
            if not isinstance(paths, list) or not all(
                isinstance(p, str) and p.strip() for p in paths
            ):
                raise DomainError(f"plan.steps[{i}].paths must be a string array")
            normalized["paths"] = [p.strip().replace("\\", "/") for p in paths]
        tools = step.get("tools")
        if tools is not None:
            if not isinstance(tools, list) or not all(
                isinstance(t, str) and t.strip() for t in tools
            ):
                raise DomainError(f"plan.steps[{i}].tools must be a string array")
            normalized["tools"] = [t.strip() for t in tools]
        steps.append(normalized)

    scope = plan.get("scope")
    if scope is None:
        derived: list[str] = []
        for step in steps:
            for path in step.get("paths") or []:
                if path not in derived:
                    derived.append(path)
        scope_list = derived
    else:
        if not isinstance(scope, list) or not scope:
            raise DomainError("plan.scope must be a non-empty string array")
        if not all(isinstance(p, str) and p.strip() for p in scope):
            raise DomainError("plan.scope entries must be non-empty strings")
        scope_list = [p.strip().replace("\\", "/") for p in scope]
    for step in steps:
        for tool in step.get("tools") or []:
            pat = tool if "/" in tool else f"*/{tool}"
            if pat not in scope_list:
                scope_list.append(pat)
    if not scope_list:
        raise DomainError(
            "plan.scope required when steps have no paths or tools "
            "(formulation needs a non-universal scope)"
        )

    options = plan.get("options")
    options_list: list[str] | None = None
    if options is not None:
        if not isinstance(options, list) or not options:
            raise DomainError("plan.options must be a non-empty string array")
        if not all(isinstance(o, str) and o.strip() for o in options):
            raise DomainError("plan.options entries must be non-empty strings")
        options_list = [o.strip() for o in options]

    out: dict[str, Any] = {
        "goal": goal.strip(),
        "steps": steps,
        "scope": scope_list,
    }
    if options_list is not None:
        out["options"] = options_list
    rationale = plan.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        out["rationale"] = rationale.strip()
    depends_on = plan.get("depends_on")
    if depends_on is not None:
        if not isinstance(depends_on, list) or not all(
            isinstance(x, str) and x.strip() for x in depends_on
        ):
            raise DomainError("plan.depends_on must be a string array")
        out["depends_on"] = depends_on
    establishes_rule = plan.get("establishes_rule")
    if establishes_rule is not None:
        if not isinstance(establishes_rule, dict):
            raise DomainError("plan.establishes_rule must be an object")
        out["establishes_rule"] = establishes_rule
    return out


def plan_evidence_paths(plan: dict[str, Any]) -> list[str]:
    """Paths for evidence.paths / coverage correlation."""
    paths: list[str] = []
    for pattern in plan.get("scope") or []:
        if isinstance(pattern, str) and pattern.strip():
            # Strip trailing globs for evidence path list (classifier input).
            p = pattern.strip().replace("\\", "/")
            if p.endswith("/**"):
                p = p[: -len("/**")] or p
            elif p.endswith("/**/*"):
                p = p[: -len("/**/*")] or p
            if p and p not in paths:
                paths.append(p)
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for path in step.get("paths") or []:
            if isinstance(path, str) and path.strip() and path not in paths:
                paths.append(path.strip().replace("\\", "/"))
    return paths
