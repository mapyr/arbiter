"""Load arbiter.rules.yaml (subset parser — fail-closed on missing/invalid)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "\"'":
        quote = text[0]
        return text[1:-1] if text.endswith(quote) and len(text) >= 2 else text[1:]
    # Unquoted scalars: drop inline comments (``mode: on_uncovered  # note``).
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip()
    elif text.startswith("#"):
        text = ""
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "null" or lower == "~":
        return None
    return text


def load_rules_yaml(text: str) -> dict[str, Any]:
    """Load the arbiter rules YAML subset (mappings, lists, scalars, comments)."""
    lines = text.splitlines()
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    def current_container() -> Any:
        return stack[-1][1]

    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        container = current_container()

        if content.startswith("- "):
            item_raw = content[2:].strip()
            if not isinstance(container, list):
                raise ValueError("list item outside a list")
            if ":" in item_raw and not (
                item_raw.startswith('"') or item_raw.startswith("'")
            ):
                key, _, rest = item_raw.partition(":")
                key = key.strip()
                rest = rest.strip()
                if rest == "":
                    nested: dict[str, Any] = {}
                    container.append(nested)
                    stack.append((indent, nested))
                else:
                    container.append({key: _parse_scalar(rest)})
            else:
                container.append(_parse_scalar(item_raw))
            continue

        if ":" not in content:
            raise ValueError(f"unsupported YAML line: {raw!r}")

        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not isinstance(container, dict):
            raise ValueError(f"mapping entry {key!r} inside a list without '- '")

        if rest == "":
            j = i
            nested_kind: list[Any] | dict[str, Any]
            while j < len(lines):
                peek = lines[j]
                if peek.strip() and not peek.lstrip().startswith("#"):
                    break
                j += 1
            if j < len(lines):
                peek = lines[j]
                peek_indent = len(peek) - len(peek.lstrip(" "))
                if peek_indent > indent and peek.lstrip().startswith("- "):
                    nested_kind = []
                else:
                    nested_kind = {}
            else:
                nested_kind = {}
            container[key] = nested_kind
            stack.append((indent, nested_kind))
        else:
            container[key] = _parse_scalar(rest)

    return root


class YamlRulesSource:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            rules = load_rules_yaml(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(rules, dict):
            return None
        return rules
