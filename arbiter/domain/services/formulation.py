"""Deterministic formulation barriers for open_decision.

Enabled only via rules file keys under ``formulation:``. Callers cannot bypass.
"""

from __future__ import annotations

import re
from typing import Any

from arbiter.domain.errors import DomainError

_UNIVERSAL_SCOPE = frozenset({"*", "**", "**/*", "*/*", "/**", "/**/*"})
_FILLER = re.compile(
    r"^(n/?a|na|none|other|tbd|todo|placeholder|asdf|xxx|y|n|yes|no|ok|idk|-)$",
    re.IGNORECASE,
)


def assert_formulation_allowed(
    *,
    options: list[str],
    scope: list[str] | tuple[str, ...] | None,
    rules: dict[str, Any] | None,
) -> None:
    if not isinstance(rules, dict):
        return
    formulation = rules.get("formulation")
    if not isinstance(formulation, dict):
        return

    if formulation.get("deny_universal_scope"):
        patterns = list(scope or ())
        for pattern in patterns:
            normalized = pattern.strip().replace("\\", "/")
            if normalized in _UNIVERSAL_SCOPE:
                raise DomainError(
                    f"formulation barrier: scope pattern {pattern!r} is universal; "
                    "narrow the scope or disable formulation.deny_universal_scope in rules"
                )
            # A lone recursive glob with no path prefix covers the tree.
            if normalized.startswith("**/") and normalized.count("/") == 1:
                raise DomainError(
                    f"formulation barrier: scope pattern {pattern!r} is too broad "
                    "(prefix-free **); narrow it or disable deny_universal_scope"
                )

    if formulation.get("deny_filler_options"):
        stripped = [o.strip() for o in options]
        fillers = [o for o in stripped if _FILLER.match(o)]
        substantive = [o for o in stripped if not _FILLER.match(o)]
        if fillers and substantive and len(substantive) == 1:
            raise DomainError(
                "formulation barrier: options look like one real choice plus filler "
                f"{fillers!r}; rewrite the closed set or disable "
                "formulation.deny_filler_options in rules"
            )
        # Extremely skewed lengths with a tiny token among long options.
        if len(stripped) >= 3:
            lengths = sorted(len(o) for o in stripped)
            if lengths[0] <= 3 and lengths[-1] >= 24 and lengths[-1] >= 8 * max(lengths[0], 1):
                raise DomainError(
                    "formulation barrier: option lengths suggest filler padding; "
                    "balance the closed set or disable deny_filler_options in rules"
                )
