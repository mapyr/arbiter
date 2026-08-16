"""Criterion 10 — installed entry point is discoverable (Hangar 2.6.0)."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

import pytest
import yaml

mcp_hangar = pytest.importorskip("mcp_hangar")

from mcp_hangar.approvals.bootstrap import DELIVERY_ENTRY_POINT_GROUP  # noqa: E402

import arbiter.adapters.hangar.delivery as delivery_mod  # noqa: E402


def test_entry_point_group_matches_probed_hangar() -> None:
    assert DELIVERY_ENTRY_POINT_GROUP == "mcp_hangar.approvals.delivery"
    assert mcp_hangar


def test_hangar_entry_point_loads_and_builds_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARBITER_HANGAR_RESOLVE_TOKEN", "mcp_test_key")
    monkeypatch.setenv("ARBITER_HANGAR_PRINCIPAL_ID", "service:arbiter")

    matches = [
        ep
        for ep in entry_points(group=DELIVERY_ENTRY_POINT_GROUP)
        if ep.name == "arbiter"
    ]
    assert matches, (
        f"no entry point name=arbiter in group {DELIVERY_ENTRY_POINT_GROUP!r}"
    )
    factory = matches[0].load()

    intercept = tmp_path / "arbiter.intercept.yaml"
    intercept.write_text(
        yaml.safe_dump(
            {"hold": [{"mcp_server": "github", "tool": "create_issue"}]}
        ),
        encoding="utf-8",
    )
    data = tmp_path / "decisions"
    data.mkdir()

    built = factory(
        {
            "data_dir": str(data),
            "intercept_rules_path": str(intercept),
            "resolve_base_url": "http://127.0.0.1:9",
        }
    )
    assert isinstance(built, delivery_mod.ArbiterApprovalDelivery)
    assert callable(getattr(built, "send", None))
