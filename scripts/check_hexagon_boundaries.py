#!/usr/bin/env python3
"""Fail if hexagonal dependency rules are violated.

Rules:
- ``arbiter.domain`` may import only the stdlib and itself.
- ``arbiter.application`` may import stdlib, itself, and ``arbiter.domain``.
- ``arbiter.adapters`` may import stdlib, third-party, domain, application, adapters.
- Compatibility shims at package root are unconstrained (thin re-exports).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

def _package_root() -> Path:
    """Repo tree if present beside the script; else the installed package."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, *here.parents):
        pkg = candidate / "arbiter"
        if (pkg / "domain").is_dir() and (pkg / "application").is_dir():
            return pkg
    try:
        import arbiter

        return Path(arbiter.__file__).resolve().parent
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "cannot locate arbiter package (install the package or run from the repo)"
        ) from exc

# Top-level packages that must never appear inside domain.
DOMAIN_FORBIDDEN_PREFIXES = (
    "arbiter.application",
    "arbiter.adapters",
    "arbiter.bootstrap",
    "mcp",
    "httpx",
    "httpx2",
    "yaml",
    "uvicorn",
    "starlette",
    "pytest",
)

APPLICATION_FORBIDDEN_PREFIXES = (
    "arbiter.adapters",
    "arbiter.bootstrap",
    "mcp",
    "httpx",
    "httpx2",
    "yaml",
    "uvicorn",
    "starlette",
)


def _module_prefix(name: str) -> str:
    return name.split(".")[0] if name else ""


def _is_forbidden(module: str | None, prefixes: tuple[str, ...]) -> bool:
    if not module:
        return False
    for prefix in prefixes:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def _imports_in(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module is None:
                # relative import inside package — resolve roughly
                found.append((node.lineno, "." * node.level))
            elif node.module:
                found.append((node.lineno, node.module))
    return found


def _check_tree(base: Path, *, layer: str, forbidden: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    if not base.is_dir():
        return [f"missing layer directory: {base}"]
    for path in sorted(base.rglob("*.py")):
        if path.name == "__pycache__":
            continue
        for lineno, mod in _imports_in(path):
            if mod.startswith("."):
                # relative imports stay inside the package tree; OK for domain/application
                continue
            if _is_forbidden(mod, forbidden):
                try:
                    rel = path.relative_to(ROOT)
                except ValueError:
                    rel = path
                errors.append(f"{rel}:{lineno}: {layer} must not import {mod!r}")
    return errors


def main() -> int:
    root = _package_root()
    print(f"checking package root: {root}")
    errors: list[str] = []
    errors.extend(
        _check_tree(
            root / "domain",
            layer="domain",
            forbidden=DOMAIN_FORBIDDEN_PREFIXES,
        )
    )
    errors.extend(
        _check_tree(
            root / "application",
            layer="application",
            forbidden=APPLICATION_FORBIDDEN_PREFIXES,
        )
    )
    if errors:
        print("Hexagon boundary violations:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    print("hexagon boundaries: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
