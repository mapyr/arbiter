#!/usr/bin/env python3
"""Fail if domain-layer line coverage is below the required threshold."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _is_domain_file(path: str) -> bool:
    norm = path.replace("\\", "/")
    return (
        "/arbiter/domain/" in norm
        or norm.startswith("arbiter/domain/")
        or "/site-packages/arbiter/domain/" in norm
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--fail-under", type=float, default=85.0)
    args = parser.parse_args()

    data = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    covered = 0
    statements = 0
    rows: list[tuple[str, float, int, int]] = []
    for path, info in data.get("files", {}).items():
        if not _is_domain_file(path):
            continue
        summary = info["summary"]
        c = int(summary["covered_lines"])
        n = int(summary["num_statements"])
        covered += c
        statements += n
        pct = (100.0 * c / n) if n else 100.0
        label = path.replace("\\", "/").split("arbiter/")[-1]
        rows.append((label, pct, c, n))

    if statements == 0:
        print("domain coverage: no statements found", file=sys.stderr)
        print("files seen:", *list(data.get("files", {}))[:20], sep="\n  ", file=sys.stderr)
        return 1

    pct = 100.0 * covered / statements
    print(f"domain coverage: {pct:.1f}% ({covered}/{statements})")
    for name, file_pct, c, n in sorted(rows):
        print(f"  {name}: {file_pct:.1f}% ({c}/{n})")

    if pct + 1e-9 < args.fail_under:
        print(
            f"FAIL: domain coverage {pct:.1f}% < {args.fail_under:.1f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
