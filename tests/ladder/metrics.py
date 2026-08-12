"""Metric units for the ladder corpus."""

from __future__ import annotations

from typing import Any, Sequence


def dist(samples: Sequence[float]) -> dict[str, Any]:
    if not samples:
        return {"n": 0, "p50": None, "p90": None, "p95": None, "max": None, "sum": 0.0}
    ordered = sorted(float(x) for x in samples)
    return {
        "n": len(ordered),
        "p50": _percentile(ordered, 50),
        "p90": _percentile(ordered, 90),
        "p95": _percentile(ordered, 95),
        "max": ordered[-1],
        "sum": sum(ordered),
    }


def _percentile(ordered: list[float], pct: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def disagree_rate(pairs: Sequence[tuple[str | None, str | None]]) -> dict[str, Any]:
    comparable = [(a, b) for a, b in pairs if a is not None and b is not None]
    disagree = sum(1 for a, b in comparable if a != b)
    n = len(comparable)
    rate = (disagree / n) if n else None
    return {
        "comparable": n,
        "disagree": disagree,
        "disagree_rate": rate,
    }
