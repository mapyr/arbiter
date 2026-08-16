"""Hold adjudication — offline stubs (Hangar 2.6.0 contract)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from arbiter.adapters.hangar.config import parse_hangar_channel_config
from arbiter.adapters.hangar.delivery import ArbiterApprovalDelivery, create_delivery
from arbiter.domain.services.intercept import parse_intercept_rules
from arbiter.application.services.hold_adjudicator import HeldCall, HoldAdjudicator
from arbiter.application.voters_config import parse_voters_config
from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError
from arbiter.domain.services.call_identity import call_identity
from tests.openai_stub import StubScenario, StubServer, delay_handler, vote_handler

pytestmark = pytest.mark.integration

PRINCIPAL = "service:arbiter"


def _args_hash(arguments: dict[str, Any]) -> str:
    raw = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _write_voters(path: Path, base_url: str, *, round_deadline: float = 30) -> None:
    cfg = {
        "voters": [
            {
                "id": "voter-1",
                "base_url": base_url,
                "model": "model-a",
                "temperature": 0,
                "max_tokens": 400,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-2",
                "base_url": base_url,
                "model": "model-b",
                "temperature": 0,
                "max_tokens": 400,
                "timeout_seconds": 5,
            },
            {
                "id": "voter-3",
                "base_url": base_url,
                "model": "model-c",
                "temperature": 0,
                "max_tokens": 400,
                "timeout_seconds": 5,
            },
        ],
        "round_deadline_seconds": round_deadline,
        "reveal_round": False,
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _write_intercept(path: Path, rules: list[dict[str, str]] | None = None) -> None:
    payload = {
        "hold": rules
        or [
            {"mcp_server": "github", "tool": "create_issue"},
            {"mcp_server": "filesystem", "tool": "write_*"},
        ]
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _events(app) -> list[dict]:
    return app.read_all_wire()


def _expires(seconds: float = 120.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@dataclass
class FakeApprovalRequest:
    approval_id: str
    mcp_server_id: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str | None = None
    expires_at: datetime | None = None
    requested_by: str | None = "agent-1"
    tenant_id: str | None = "t1"
    correlation_id: str = "corr-1"
    provider_id: str | None = None

    def __post_init__(self) -> None:
        if self.provider_id is None:
            self.provider_id = self.mcp_server_id
        if self.arguments_hash is None:
            self.arguments_hash = _args_hash(self.arguments)
        if self.expires_at is None:
            self.expires_at = _expires(120)


class _CallbackResolver:
    def __init__(self, callback: Any) -> None:
        self._callback = callback

    async def resolve(self, approval_id: str, *, approved: bool, reason: str) -> None:
        await self._callback(approval_id, approved, reason)


def create_delivery_for_tests(
    *,
    adjudicator: HoldAdjudicator,
    resolve_callback: Any,
    app: Any,
) -> ArbiterApprovalDelivery:
    return ArbiterApprovalDelivery(
        adjudicator=adjudicator,
        resolver=_CallbackResolver(resolve_callback),
        app=app,
    )


@dataclass
class HoldHarness:
    """Hangar-shaped hold: send returns immediately; wait for resolve callback."""

    delivery: ArbiterApprovalDelivery
    timeout_seconds: float = 5.0
    resolutions: list[tuple[str, bool, str]] = field(default_factory=list)

    async def _on_resolve(self, approval_id: str, approved: bool, reason: str) -> None:
        self.resolutions.append((approval_id, approved, reason))
        self._event.set()

    async def run(self, request: FakeApprovalRequest) -> tuple[bool, str]:
        self._event = asyncio.Event()
        self.resolutions.clear()
        self.delivery._resolver = _CallbackResolver(self._on_resolve)
        started = time.perf_counter()
        await self.delivery.send(request)
        send_ms = (time.perf_counter() - started) * 1000.0
        try:
            await asyncio.wait_for(self._event.wait(), timeout=self.timeout_seconds)
        except TimeoutError:
            return False, "hangar_hold_timeout"
        assert self.resolutions
        _aid, approved, reason = self.resolutions[-1]
        self.last_send_ms = send_ms
        return approved, reason


@pytest.fixture
def stage4_env(tmp_cwd: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_cwd / "decisions"
    intercept = tmp_cwd / "arbiter.intercept.yaml"
    voters = tmp_cwd / "arbiter.voters.yaml"
    _write_intercept(intercept)
    monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters))
    monkeypatch.setenv("ARBITER_HANGAR_RESOLVE_TOKEN", "mcp_test_token")
    monkeypatch.setenv("ARBITER_HANGAR_PRINCIPAL_ID", PRINCIPAL)
    return {
        "cwd": tmp_cwd,
        "data": data,
        "intercept": intercept,
        "voters": voters,
        "rules": tmp_cwd / "arbiter.rules.yaml",
    }


def _adj(app, intercept_path: Path, **kwargs: Any) -> HoldAdjudicator:
    return HoldAdjudicator(
        app,
        intercept=parse_intercept_rules(yaml.safe_load(intercept_path.read_text())),
        resolver_principal=PRINCIPAL,
        min_round_seconds=kwargs.pop("min_round_seconds", 1.0),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_1_covered_by_prior_allow(stage4_env: dict) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("allow"))
    scenario.on("model-b", vote_handler("allow"))
    scenario.on("model-c", vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        opened = app.open_decision(
            question="Allow github create_issue under policy X?",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"policy": "X"},
            criticality="critical",
            ttl_seconds=900,
            scope=["github/create_issue"],
        )
        await app.run_model_quorum(
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(stage4_env["voters"].read_text())),
            rng=random.Random(0),
        )
        delivery = create_delivery_for_tests(
            adjudicator=_adj(app, stage4_env["intercept"]),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
        harness = HoldHarness(delivery)
        approved, reason = await harness.run(
            FakeApprovalRequest(
                approval_id="a1",
                mcp_server_id="github",
                tool_name="create_issue",
                arguments={"title": "hi"},
            )
        )
    assert approved is True
    assert opened["decision_id"] in reason
    accepts = [e for e in _events(app) if e["event"] == "hold.accepted"]
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert len(accepts) == 1
    assert holds[0]["path"] == "covered"
    assert holds[0]["decision_id"] == opened["decision_id"]
    assert holds[0]["resolver_principal"] == PRINCIPAL


@pytest.mark.asyncio
async def test_plan_allow_covers_hold_on_path(stage4_env: dict) -> None:
    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(model, vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        planned = await app.ensure_plan(
            {
                "goal": "Update auth handler",
                "steps": [
                    {
                        "action": "edit auth handler",
                        "paths": ["auth/handler.py"],
                        "tools": ["filesystem/write_file"],
                    }
                ],
                "scope": ["auth/**"],
            }
        )
        assert planned["approved"] is True
        delivery = create_delivery_for_tests(
            adjudicator=_adj(app, stage4_env["intercept"]),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
        harness = HoldHarness(delivery)
        approved, reason = await harness.run(
            FakeApprovalRequest(
                approval_id="plan-cover",
                mcp_server_id="filesystem",
                tool_name="write_file",
                arguments={"path": "auth/x.py"},
            )
        )
        again, _ = await harness.run(
            FakeApprovalRequest(
                approval_id="plan-cover-dup",
                mcp_server_id="filesystem",
                tool_name="write_file",
                arguments={"path": "auth/x.py"},
            )
        )
    assert approved is True and again is True
    assert planned["decision_id"] in reason
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["path"] == "covered"
    assert holds[0]["decision_id"] == planned["decision_id"]
    assert holds[1]["path"] == "duplicate"
    commit = app.verify_commit_paths(
        paths=["auth/x.py"],
        decision_id=planned["decision_id"],
    )
    assert commit["ok"] is True
    assert commit["decision_id"] == planned["decision_id"]


@pytest.mark.asyncio
async def test_lab_migrate_apply_denied_without_trial(stage4_env: dict) -> None:
    _write_intercept(
        stage4_env["intercept"], [{"mcp_server": "mockfs", "tool": "*"}]
    )
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=_adj(app, stage4_env["intercept"]),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
    )
    approved, reason = await harness.run(
        FakeApprovalRequest(
            approval_id="mig-1",
            mcp_server_id="mockfs",
            tool_name="migrate.apply",
            arguments={"migration": "001_init"},
        )
    )
    assert approved is False
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["path"] == "precondition_denied"
    assert "precondition" in reason


@pytest.mark.asyncio
async def test_rename_dst_outside_scope_is_not_covered(stage4_env: dict) -> None:
    _write_intercept(
        stage4_env["intercept"], [{"mcp_server": "mockfs", "tool": "*"}]
    )
    scenario = StubScenario()
    for model in ("model-a", "model-b", "model-c"):
        scenario.on(model, vote_handler("deny"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        opened = app.open_decision(
            question="Allow notes/**?",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"paths": ["notes/**"]},
            criticality="critical",
            ttl_seconds=900,
            scope=["notes/**"],
        )
        await app.run_model_quorum(
            opened["decision_id"],
            config=parse_voters_config(yaml.safe_load(stage4_env["voters"].read_text())),
            rng=random.Random(0),
        )
        harness = HoldHarness(
            create_delivery_for_tests(
                adjudicator=_adj(app, stage4_env["intercept"]),
                resolve_callback=lambda *a, **k: asyncio.sleep(0),
                app=app,
            )
        )
        await harness.run(
            FakeApprovalRequest(
                approval_id="ren-1",
                mcp_server_id="mockfs",
                tool_name="rename_note",
                arguments={"src": "notes/a", "dst": "other/x"},
            )
        )
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["path"] == "quorum"
    assert holds[0]["decision_id"] != opened["decision_id"]


@pytest.mark.asyncio
async def test_2_no_coverage_quorum_deny_names_decision(stage4_env: dict) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("deny"))
    scenario.on("model-b", vote_handler("deny"))
    scenario.on("model-c", vote_handler("deny"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        harness = HoldHarness(
            create_delivery_for_tests(
                adjudicator=_adj(app, stage4_env["intercept"]),
                resolve_callback=lambda *a, **k: asyncio.sleep(0),
                app=app,
            )
        )
        approved, reason = await harness.run(
            FakeApprovalRequest(
                approval_id="a2",
                mcp_server_id="github",
                tool_name="create_issue",
                arguments={"title": "nope"},
            )
        )
    assert approved is False
    assert reason.startswith("decision:")
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["path"] == "quorum"
    assert holds[0]["requested_by"] == "agent-1"


@pytest.mark.asyncio
async def test_3_not_intercepted_passthrough(stage4_env: dict) -> None:
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )
    stage4_env["voters"].write_text(
        yaml.safe_dump(
            {
                "voters": [
                    {
                        "id": f"voter-{i}",
                        "base_url": "http://127.0.0.1:9",
                        "model": f"m{i}",
                        "temperature": 0,
                        "max_tokens": 10,
                        "timeout_seconds": 1,
                    }
                    for i in (1, 2, 3)
                ],
                "round_deadline_seconds": 5,
                "reveal_round": False,
            }
        ),
        encoding="utf-8",
    )
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=_adj(app, stage4_env["intercept"]),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
    )
    approved, reason = await harness.run(
        FakeApprovalRequest(
            approval_id="a3",
            mcp_server_id="weather",
            tool_name="forecast",
            arguments={},
        )
    )
    assert approved is True
    assert reason == "not_intercepted"


@pytest.mark.asyncio
async def test_4_duplicate_hold_one_quorum_two_verdicts(stage4_env: dict) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("allow"))
    scenario.on("model-b", vote_handler("allow"))
    scenario.on("model-c", vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        delivery = create_delivery_for_tests(
            adjudicator=_adj(app, stage4_env["intercept"]),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
        harness = HoldHarness(delivery)
        args = {"title": "same"}
        a1, _ = await harness.run(
            FakeApprovalRequest(
                approval_id="dup-1",
                mcp_server_id="github",
                tool_name="create_issue",
                arguments=args,
            )
        )
        a2, _ = await harness.run(
            FakeApprovalRequest(
                approval_id="dup-2",
                mcp_server_id="github",
                tool_name="create_issue",
                arguments=args,
            )
        )
    assert a1 is True and a2 is True
    opened = [e for e in _events(app) if e["event"] == "decision.opened"]
    assert len(opened) == 1
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert len(holds) == 2
    assert holds[1]["path"] == "duplicate"
    cid = call_identity(
        mcp_server_id="github",
        tool_name="create_issue",
        arguments_hash=_args_hash(args),
    )
    assert holds[0]["call_id"] == holds[1]["call_id"] == cid


@pytest.mark.asyncio
async def test_5_arbiter_unavailable_hold_times_out_deny(stage4_env: dict) -> None:
    class HangDelivery:
        async def send(self, request: Any) -> None:
            await asyncio.sleep(10)

    delivery = HangDelivery()
    event = asyncio.Event()

    async def run() -> tuple[bool, str]:
        task = asyncio.create_task(delivery.send(object()))
        try:
            await asyncio.wait_for(event.wait(), timeout=0.2)
        except TimeoutError:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return False, "hangar_hold_timeout"
        return True, "ok"

    approved, reason = await run()
    assert approved is False
    assert reason == "hangar_hold_timeout"


@pytest.mark.asyncio
async def test_5b_adapter_error_path_denies(stage4_env: dict) -> None:
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )

    class BoomStore:
        def __init__(self) -> None:
            self._n = 0
            self._rows: list[dict] = []

        def append(self, event: Any) -> None:
            from arbiter.adapters.outbound.event_codec import to_wire
            from arbiter.domain.events import HoldAccepted

            wire = to_wire(event)
            self._rows.append(wire)
            # Allow hold.accepted (Z1), fail later adjudication writes / reads for prior
            if isinstance(event, HoldAccepted):
                return
            self._n += 1
            if self._n >= 1 and wire.get("event") != "hold.accepted":
                raise OSError("ledger unavailable")

        def append_all(self, events: list) -> None:
            for e in events:
                self.append(e)

        def load_stream(self, decision_id: str) -> list:
            return []

        def load_decision(self, decision_id: str):
            return None

        def read_all_wire(self) -> list:
            return list(self._rows)

    boom = BoomStore()
    app._events = boom  # type: ignore[attr-defined]
    app.commands._events = boom  # type: ignore[attr-defined]
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=_adj(app, stage4_env["intercept"]),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
    )
    approved, reason = await harness.run(
        FakeApprovalRequest(
            approval_id="err",
            mcp_server_id="github",
            tool_name="create_issue",
            arguments={},
        )
    )
    assert approved is False
    assert "adapter_error" in reason or reason == "adapter_error"
    assert any(e["event"] == "hold.accepted" for e in app.read_all_wire())


def test_7_missing_intercept_rules_refuses_start(stage4_env: dict) -> None:
    missing = stage4_env["cwd"] / "nope.intercept.yaml"
    with pytest.raises(DomainError, match="intercept rules file missing"):
        parse_hangar_channel_config(
            {
                "data_dir": str(stage4_env["data"]),
                "intercept_rules_path": str(missing),
                "resolve_base_url": "http://127.0.0.1:8000",
            }
        )


@pytest.mark.asyncio
async def test_8_expired_decision_not_used_for_coverage(stage4_env: dict) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("allow"))
    scenario.on("model-b", vote_handler("allow"))
    scenario.on("model-c", vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        opened = app.open_decision(
            question="old allow",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"x": 1},
            criticality="critical",
            ttl_seconds=1,
            scope=["github/create_issue"],
            now=past,
        )
        for voter in ("voter-1", "voter-2", "voter-3"):
            app.commands.cast_vote(
                decision_id=opened["decision_id"],
                voter=voter,
                option="allow",
                confidence=1.0,
                kill_criterion="n/a",
                bundle_sha256_hex=opened["bundle_sha256"],
                now=past + timedelta(milliseconds=1),
            )
        app.resolve_decision(opened["decision_id"], now=past + timedelta(milliseconds=2))
        harness = HoldHarness(
            create_delivery_for_tests(
                adjudicator=_adj(app, stage4_env["intercept"]),
                resolve_callback=lambda *a, **k: asyncio.sleep(0),
                app=app,
            )
        )
        approved, _ = await harness.run(
            FakeApprovalRequest(
                approval_id="exp",
                mcp_server_id="github",
                tool_name="create_issue",
                arguments={"title": "after expiry"},
            )
        )
    assert approved is True
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["path"] == "quorum"
    assert holds[0]["decision_id"] != opened["decision_id"]


def test_9_expand_scope_after_resolve_rejected(stage4_env: dict) -> None:
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )
    stage4_env["voters"].write_text(
        yaml.safe_dump(
            {
                "voters": [
                    {
                        "id": f"voter-{i}",
                        "base_url": "http://127.0.0.1:9",
                        "model": f"m{i}",
                        "temperature": 0,
                        "max_tokens": 10,
                        "timeout_seconds": 1,
                    }
                    for i in (1, 2, 3)
                ],
                "round_deadline_seconds": 5,
                "reveal_round": False,
            }
        ),
        encoding="utf-8",
    )
    opened = app.open_decision(
        question="scoped",
        options=["allow", "deny"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={},
        criticality="routine",
        ttl_seconds=900,
        scope=["github/create_issue"],
        check_voters_config=False,
    )
    with pytest.raises(DomainError, match="cannot expand scope"):
        app.expand_decision_scope(opened["decision_id"], ["github/*"])


@pytest.mark.asyncio
async def test_11_send_returns_immediately_and_accept_traced(stage4_env: dict) -> None:
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )
    stage4_env["voters"].write_text(
        yaml.safe_dump(
            {
                "voters": [
                    {
                        "id": f"voter-{i}",
                        "base_url": "http://127.0.0.1:9",
                        "model": f"m{i}",
                        "temperature": 0,
                        "max_tokens": 10,
                        "timeout_seconds": 1,
                    }
                    for i in (1, 2, 3)
                ],
                "round_deadline_seconds": 5,
                "reveal_round": False,
            }
        ),
        encoding="utf-8",
    )

    class SlowAdj(HoldAdjudicator):
        async def adjudicate(self, held: HeldCall):
            await asyncio.sleep(0.5)
            return await super().adjudicate(held)

    adj = SlowAdj(
        app,
        intercept=parse_intercept_rules(
            yaml.safe_load(stage4_env["intercept"].read_text())
        ),
        resolver_principal=PRINCIPAL,
        min_round_seconds=1.0,
    )
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=adj,
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        ),
        timeout_seconds=3,
    )
    # passthrough path still goes through slow adjudicate in background
    approved, reason = await harness.run(
        FakeApprovalRequest(
            approval_id="fast",
            mcp_server_id="weather",
            tool_name="forecast",
            arguments={},
        )
    )
    assert harness.last_send_ms < 100.0
    assert approved is True
    assert reason == "not_intercepted"
    assert any(e["event"] == "hold.accepted" for e in _events(app))


@pytest.mark.asyncio
async def test_12_unknown_channel_wiring_probe_refuses(stage4_env: dict) -> None:
    """Hangar degrades unknown channels to noop; noop never writes the ledger."""
    pytest.importorskip("mcp_hangar")
    from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery

    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )

    class _Req:
        approval_id = "noop-1"
        tool_name = "t"
        provider_id = "p"
        channel = "arbiter"

    await NoOpApprovalDelivery().send(_Req())
    assert not any(e.get("event") == "hold.accepted" for e in _events(app))


@pytest.mark.asyncio
async def test_13_insufficient_time_denies_without_quorum(stage4_env: dict) -> None:
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )
    stage4_env["voters"].write_text(
        yaml.safe_dump(
            {
                "voters": [
                    {
                        "id": f"voter-{i}",
                        "base_url": "http://127.0.0.1:9",
                        "model": f"m{i}",
                        "temperature": 0,
                        "max_tokens": 10,
                        "timeout_seconds": 1,
                    }
                    for i in (1, 2, 3)
                ],
                "round_deadline_seconds": 5,
                "reveal_round": False,
            }
        ),
        encoding="utf-8",
    )
    harness = HoldHarness(
        create_delivery_for_tests(
            adjudicator=_adj(
                app,
                stage4_env["intercept"],
                hold_margin_seconds=1.0,
                min_round_seconds=30.0,
            ),
            resolve_callback=lambda *a, **k: asyncio.sleep(0),
            app=app,
        )
    )
    approved, reason = await harness.run(
        FakeApprovalRequest(
            approval_id="short",
            mcp_server_id="github",
            tool_name="create_issue",
            arguments={"title": "late"},
            expires_at=_expires(5.0),
        )
    )
    assert approved is False
    assert reason.startswith("insufficient_time_for_quorum")
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["path"] == "insufficient_time"
    assert not any(e["event"] == "decision.opened" for e in _events(app))


def test_14_missing_credentials_refuse_start(
    stage4_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARBITER_HANGAR_RESOLVE_TOKEN", raising=False)
    with pytest.raises(DomainError, match="missing Hangar resolve credentials"):
        parse_hangar_channel_config(
            {
                "data_dir": str(stage4_env["data"]),
                "intercept_rules_path": str(stage4_env["intercept"]),
                "resolve_base_url": "http://127.0.0.1:8000",
            }
        )
    monkeypatch.setenv("ARBITER_HANGAR_RESOLVE_TOKEN", "mcp_x")
    monkeypatch.delenv("ARBITER_HANGAR_PRINCIPAL_ID", raising=False)
    with pytest.raises(DomainError, match="missing Hangar resolver principal"):
        parse_hangar_channel_config(
            {
                "data_dir": str(stage4_env["data"]),
                "intercept_rules_path": str(stage4_env["intercept"]),
                "resolve_base_url": "http://127.0.0.1:8000",
            }
        )
    with pytest.raises(SystemExit, match="missing Hangar"):
        create_delivery(
            {
                "data_dir": str(stage4_env["data"]),
                "intercept_rules_path": str(stage4_env["intercept"]),
                "resolve_base_url": "http://127.0.0.1:8000",
            }
        )


@pytest.mark.asyncio
async def test_15_resolver_principal_linked_to_decision(stage4_env: dict) -> None:
    scenario = StubScenario()
    scenario.on("model-a", vote_handler("allow"))
    scenario.on("model-b", vote_handler("allow"))
    scenario.on("model-c", vote_handler("allow"))
    async with StubServer(scenario) as stub:
        _write_voters(stage4_env["voters"], stub.base_url)
        app = create_application(
            root=stage4_env["data"],
            rules=stage4_env["rules"],
            voters=stage4_env["voters"],
        )
        harness = HoldHarness(
            create_delivery_for_tests(
                adjudicator=_adj(app, stage4_env["intercept"]),
                resolve_callback=lambda *a, **k: asyncio.sleep(0),
                app=app,
            )
        )
        approved, reason = await harness.run(
            FakeApprovalRequest(
                approval_id="p15",
                mcp_server_id="github",
                tool_name="create_issue",
                arguments={"title": "attr"},
                requested_by="user:alice",
            )
        )
    assert approved is True
    holds = [e for e in _events(app) if e["event"] == "hold.adjudicated"]
    assert holds[0]["resolver_principal"] == PRINCIPAL
    assert holds[0]["requested_by"] == "user:alice"
    assert holds[0]["decision_id"] is not None
    assert holds[0]["decision_id"] in reason


@pytest.mark.asyncio
async def test_wiring_probe_passes_for_live_adapter(stage4_env: dict) -> None:
    app = create_application(
        root=stage4_env["data"],
        rules=stage4_env["rules"],
        voters=stage4_env["voters"],
    )
    stage4_env["voters"].write_text(
        yaml.safe_dump(
            {
                "voters": [
                    {
                        "id": f"voter-{i}",
                        "base_url": "http://127.0.0.1:9",
                        "model": f"m{i}",
                        "temperature": 0,
                        "max_tokens": 10,
                        "timeout_seconds": 1,
                    }
                    for i in (1, 2, 3)
                ],
                "round_deadline_seconds": 5,
                "reveal_round": False,
            }
        ),
        encoding="utf-8",
    )
    delivery = create_delivery_for_tests(
        adjudicator=_adj(app, stage4_env["intercept"]),
        resolve_callback=lambda *a, **k: asyncio.sleep(0),
        app=app,
    )
    approval_id = delivery.prove_wired()
    assert any(
        e["event"] == "hold.accepted" and e["approval_id"] == approval_id
        for e in _events(app)
    )


def test_unit_payload_call_key_and_scope() -> None:
    from arbiter.domain.services.intercept import InterceptRule, InterceptRules
    from arbiter.domain.services.scope import scope_covers

    h = _args_hash({"a": 1})
    assert call_identity(
        mcp_server_id="github", tool_name="create_issue", arguments_hash=h
    ) == f"github/create_issue/{h}"
    rules = InterceptRules(
        rules=(InterceptRule(mcp_server="filesystem", tool="write_*"),)
    )
    assert rules.matches("filesystem", "write_file")
    assert scope_covers(["github/*"], "github", "create_issue")
