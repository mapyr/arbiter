"""Decision dependency graph helpers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from arbiter.domain.errors import DomainError

DEFAULT_MAX_DEPENDENCY_DEPTH = 3


def assert_no_cycle(
    decision_id: str,
    depends_on: Sequence[str],
    edges: Mapping[str, Sequence[str]],
) -> None:
    """Refuse open when adding depends_on would create a cycle."""
    graph = {k: list(v) for k, v in edges.items()}
    graph[decision_id] = list(depends_on)
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            raise DomainError(
                f"dependency cycle detected involving {node!r}; refuse open"
            )
        if node in visited:
            return
        visiting.add(node)
        for nxt in graph.get(node, ()):
            dfs(nxt)
        visiting.remove(node)
        visited.add(node)

    dfs(decision_id)


def assert_depth_ok(
    depends_on: Sequence[str],
    edges: Mapping[str, Sequence[str]],
    *,
    max_depth: int = DEFAULT_MAX_DEPENDENCY_DEPTH,
) -> None:
    if max_depth < 1:
        raise DomainError("max dependency depth must be >= 1")

    def depth_of(node: str, seen: set[str]) -> int:
        if node in seen:
            return 0
        kids = list(edges.get(node, ()))
        if not kids:
            return 1
        return 1 + max(depth_of(k, seen | {node}) for k in kids)

    for dep in depends_on:
        d = depth_of(dep, set())
        # New node adds one level on top of dep's depth.
        if d + 1 > max_depth:
            raise DomainError(
                f"dependency depth {d + 1} exceeds max {max_depth} "
                f"(via {dep!r})"
            )


def cascade_invalidations(
    root_id: str,
    edges: Mapping[str, Sequence[str]],
    *,
    already_invalid: set[str] | None = None,
) -> list[str]:
    """Return decision ids that depend (transitively) on root_id, root first.

    ``edges`` maps child → parents (depends_on).
    """
    invalid = set(already_invalid or ())
    invalid.add(root_id)
    changed = True
    while changed:
        changed = False
        for child, parents in edges.items():
            if child in invalid:
                continue
            if any(p in invalid for p in parents):
                invalid.add(child)
                changed = True
    # Stable order: root, then others sorted.
    rest = sorted(i for i in invalid if i != root_id)
    return [root_id, *rest]


def dependency_edges_from_wire(
    wire_events: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Build child→parents map from decision.opened rows (ignores invalidated)."""
    invalidated = {
        e["decision_id"]
        for e in wire_events
        if e.get("event") == "decision.invalidated"
        and isinstance(e.get("decision_id"), str)
    }
    edges: dict[str, list[str]] = {}
    for raw in wire_events:
        if raw.get("event") != "decision.opened":
            continue
        did = raw.get("decision_id")
        if not isinstance(did, str) or did in invalidated:
            continue
        deps = raw.get("depends_on") or []
        edges[did] = [d for d in deps if isinstance(d, str)]
    return edges


def dependencies_still_hold(
    decision_id: str,
    edges: Mapping[str, Sequence[str]],
    *,
    is_valid_allow: Any,
) -> tuple[bool, str | None]:
    """Walk parents; ``is_valid_allow(dep_id) -> bool`` supplied by caller."""
    for dep in edges.get(decision_id, ()):
        if not is_valid_allow(dep):
            return False, dep
        ok, bad = dependencies_still_hold(dep, edges, is_valid_allow=is_valid_allow)
        if not ok:
            return False, bad
    return True, None
