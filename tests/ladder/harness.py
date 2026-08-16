"""Offline ladder corpus — shared fixtures for S1–S6 measurements."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from arbiter.domain.services.intercept import parse_intercept_rules
from arbiter.application.services.hold_adjudicator import HeldCall, HoldAdjudicator
from arbiter.bootstrap import create_application
from arbiter.domain.services.narrowing import narrowing_candidates
from arbiter.domain.services.option_kind import ALLOW, DENY, ESCALATE, NARROW_PREFIX
from arbiter.domain.services.preconditions import inexpressible_predicates
from tests.ladder.metrics import disagree_rate, dist
from tests.openai_stub import StubScenario, StubServer, vote_handler

PRINCIPAL = "service:arbiter-ladder"


def args_hash(arguments: dict[str, Any]) -> str:
    raw = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def write_voters(
    path: Path,
    base_url: str,
    *,
    baseline: bool = True,
    round_deadline: float = 30,
) -> None:
    cfg: dict[str, Any] = {
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
    if baseline:
        cfg["baseline_voter"] = "voter-1"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def write_intercept(path: Path) -> None:
    payload = {
        "hold": [
            {"mcp_server": "db", "tool": "migrate.apply"},
            {"mcp_server": "db", "tool": "migrate.dry_run"},
            {"mcp_server": "fs", "tool": "write_file"},
            {"mcp_server": "fs", "tool": "contract_test"},
            {"mcp_server": "shell", "tool": "shell.exec"},
            {"mcp_server": "github", "tool": "create_issue"},
        ]
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def held(
    *,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    approval_id: str,
    expires_s: float = 120.0,
) -> HeldCall:
    return HeldCall(
        approval_id=approval_id,
        mcp_server_id=server,
        tool_name=tool,
        arguments=arguments,
        arguments_hash=args_hash(arguments),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_s),
        requested_by="ladder-agent",
        tenant_id="ladder",
        correlation_id=approval_id,
    )


@dataclass
class LadderEnv:
    cwd: Path
    data: Path
    voters: Path
    intercept: Path
    rules: Path
    app: Any
    adj: HoldAdjudicator
    scenario: StubScenario
    stub: StubServer
    samples: dict[str, list[Any]] = field(default_factory=dict)

    def note(self, key: str, value: Any) -> None:
        self.samples.setdefault(key, []).append(value)


def unanimous(option: str) -> StubScenario:
    s = StubScenario()
    s.on("model-a", vote_handler(option))
    s.on("model-b", vote_handler(option))
    s.on("model-c", vote_handler(option))
    return s


def mixed(a: str, b: str, c: str) -> StubScenario:
    s = StubScenario()
    s.on("model-a", vote_handler(a))
    s.on("model-b", vote_handler(b))
    s.on("model-c", vote_handler(c))
    return s


async def open_ladder_env(
    tmp_path: Path,
    monkeypatch: Any,
    scenario: StubScenario,
    *,
    enable_narrowing: bool = False,
    include_escalate: bool = False,
) -> LadderEnv:
    data = tmp_path / "decisions"
    data.mkdir(parents=True, exist_ok=True)
    voters = tmp_path / "arbiter.voters.yaml"
    intercept = tmp_path / "arbiter.intercept.yaml"
    rules_src = Path(__file__).resolve().parents[2] / "arbiter.rules.yaml.example"
    rules = tmp_path / "arbiter.rules.yaml"
    rules.write_text(rules_src.read_text(encoding="utf-8"), encoding="utf-8")
    write_intercept(intercept)
    monkeypatch.setenv("ARBITER_DATA_DIR", str(data))
    monkeypatch.setenv("ARBITER_RULES_PATH", str(rules))
    monkeypatch.setenv("ARBITER_VOTERS_PATH", str(voters))
    monkeypatch.chdir(tmp_path)

    stub = StubServer(scenario)
    await stub.__aenter__()
    write_voters(voters, stub.base_url, baseline=True)
    app = create_application(root=data, rules=rules, voters=voters)
    adj = HoldAdjudicator(
        app,
        intercept=parse_intercept_rules(yaml.safe_load(intercept.read_text())),
        resolver_principal=PRINCIPAL,
        min_round_seconds=1.0,
        enable_narrowing=enable_narrowing,
        include_escalate=include_escalate,
    )
    return LadderEnv(
        cwd=tmp_path,
        data=data,
        voters=voters,
        intercept=intercept,
        rules=rules,
        app=app,
        adj=adj,
        scenario=scenario,
        stub=stub,
    )


async def close_ladder_env(env: LadderEnv) -> None:
    await env.stub.__aexit__(None, None, None)


def baseline_option(app: Any, decision_id: str) -> str | None:
    for raw in app.read_all_wire():
        if raw.get("event") == "baseline.verdict" and raw.get("decision_id") == decision_id:
            return raw.get("option")
    return None


def collect_cost_time(app: Any) -> dict[str, Any]:
    latencies: list[float] = []
    holds: list[float] = []
    prompt_tokens: list[float] = []
    completion_tokens: list[float] = []
    for raw in app.read_all_wire():
        if raw.get("event") == "vote.cast":
            if raw.get("latency_ms") is not None:
                latencies.append(float(raw["latency_ms"]))
            if raw.get("prompt_tokens") is not None:
                prompt_tokens.append(float(raw["prompt_tokens"]))
            if raw.get("completion_tokens") is not None:
                completion_tokens.append(float(raw["completion_tokens"]))
        if raw.get("event") == "hold.adjudicated":
            holds.append(float(raw.get("duration_ms") or 0.0))
    return {
        "vote_latency_ms": dist(latencies),
        "hold_duration_ms": dist(holds),
        "prompt_tokens": dist(prompt_tokens),
        "completion_tokens": dist(completion_tokens),
    }


async def run_s1(env: LadderEnv) -> dict[str, Any]:
    """Baseline binary allow/deny on current code (narrowing off)."""
    pairs: list[tuple[str | None, str | None]] = []
    paths: list[str] = []

    # Routine allow
    h = held(
        server="github",
        tool="create_issue",
        arguments={"title": "t"},
        approval_id="s1-allow",
    )
    env.adj.accept(h)
    r = await env.adj.adjudicate(h)
    paths.append(r.path)
    if r.decision_id:
        state = env.app.replay(r.decision_id)
        chosen = (
            state.resolution.get("chosen_option")
            if state and state.resolution
            else None
        )
        pairs.append((chosen, baseline_option(env.app, r.decision_id)))

    # Critical dissent → deny (reset scenario mid-flight by new env is heavy;
    # use open_decision directly for dissent case)
    opened = env.app.open_decision(
        question="Critical write?",
        options=["allow", "deny"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={"path": "src/a.py"},
        criticality="critical",
        ttl_seconds=120,
        scope=["fs/write_file"],
    )
    # Reconfigure stub responses for dissent — call counts continue; append handlers
    env.scenario.on("model-a", vote_handler("allow"))
    env.scenario.on("model-b", vote_handler("allow"))
    env.scenario.on("model-c", vote_handler("deny"))
    resolved = await env.app.run_model_quorum(opened["decision_id"], rng=random.Random(0))
    pairs.append((resolved.get("chosen_option"), baseline_option(env.app, opened["decision_id"])))
    paths.append("quorum" if resolved.get("verdict") != "deny" else "quorum_deny")

    # Coverage hit: prior allow then second hold
    env.scenario.on("model-a", vote_handler("allow"))
    env.scenario.on("model-b", vote_handler("allow"))
    env.scenario.on("model-c", vote_handler("allow"))
    h2 = held(
        server="github",
        tool="create_issue",
        arguments={"title": "t2"},
        approval_id="s1-cov-open",
    )
    env.adj.accept(h2)
    r2 = await env.adj.adjudicate(h2)
    paths.append(r2.path)
    h3 = held(
        server="github",
        tool="create_issue",
        arguments={"title": "t3"},
        approval_id="s1-cov-hit",
    )
    env.adj.accept(h3)
    r3 = await env.adj.adjudicate(h3)
    paths.append(r3.path)

    div = disagree_rate(pairs)
    cost = collect_cost_time(env.app)
    return {
        "divergence": div,
        "cost_time": cost,
        "paths": paths,
        "path_counts": {p: paths.count(p) for p in sorted(set(paths))},
        "stop_divergence": (div["disagree_rate"] or 0) > 0.33,
        "stop_latency": (cost["hold_duration_ms"]["p95"] or 0) > 25_000,
    }


async def run_s2(env: LadderEnv) -> dict[str, Any]:
    """Deterministic migrate precondition — trial must be in ledger."""
    args_fail = {"migration": "001_init"}
    args_ok = {"migration": "002_ok"}
    # Apply without trial → precondition deny (no quorum)
    h = held(
        server="db",
        tool="migrate.apply",
        arguments=args_fail,
        approval_id="s2-no-trial",
    )
    env.adj.accept(h)
    r = await env.adj.adjudicate(h)
    denied_pre = r.path == "precondition_denied"

    # Record trial for a *different* migration (call_id is per tool+hash;
    # a prior deny on the same hash would duplicate-short-circuit retries).
    trial = held(
        server="db",
        tool="migrate.dry_run",
        arguments=args_ok,
        approval_id="s2-trial",
    )
    env.adj.accept(trial)
    tr = await env.adj.adjudicate(trial)

    # Apply with matching trial → proceeds to quorum/coverage
    h2 = held(
        server="db",
        tool="migrate.apply",
        arguments=args_ok,
        approval_id="s2-with-trial",
    )
    env.adj.accept(h2)
    r2 = await env.adj.adjudicate(h2)

    # Non-migrate call — precondition not applicable
    h3 = held(
        server="github",
        tool="create_issue",
        arguments={"title": "x"},
        approval_id="s2-other",
    )
    env.adj.accept(h3)
    r3 = await env.adj.adjudicate(h3)

    adjudicated = [
        e
        for e in env.app.read_all_wire()
        if e.get("event") == "hold.adjudicated"
    ]
    pre_paths = [e["path"] for e in adjudicated if e["path"] == "precondition_denied"]
    quorum_paths = [e["path"] for e in adjudicated if e["path"] == "quorum"]
    expressible_share = len(pre_paths) / max(1, len(adjudicated))
    return {
        "denied_without_trial": denied_pre,
        "trial_path": tr.path,
        "apply_after_trial_path": r2.path,
        "other_path": r3.path,
        "precondition_denied_count": len(pre_paths),
        "quorum_count": len(quorum_paths),
        "share_expressed_as_precondition": expressible_share,
        "inexpressible": inexpressible_predicates(),
        "cost_time": collect_cost_time(env.app),
    }


def _reset_unanimous(scenario: StubScenario, option: str) -> None:
    scenario.scripts.clear()
    scenario.call_counts.clear()
    scenario.on("model-a", vote_handler(option))
    scenario.on("model-b", vote_handler(option))
    scenario.on("model-c", vote_handler(option))


async def run_s3(env: LadderEnv) -> dict[str, Any]:
    """Narrowing options — voters pick from generated list."""
    narrow = narrowing_candidates(
        tool_name="write_file",
        arguments={"path": "src/a.py", "content": "x"},
        mcp_server_id="fs",
    )
    assert any(o.startswith(NARROW_PREFIX) for o in narrow)
    assert ALLOW in narrow and DENY in narrow

    # Binary-only class
    binary = narrowing_candidates(tool_name="shell.exec", arguments={})
    assert binary == [ALLOW, DENY] or set(binary) <= {ALLOW, DENY, ESCALATE}

    # Vote path-scoped narrow instead of deny — follow-up outside path needs quorum.
    option = next(
        o
        for o in narrow
        if o.startswith(NARROW_PREFIX) and "paths=src/a.py" in o
    )
    _reset_unanimous(env.scenario, option)
    h = held(
        server="fs",
        tool="write_file",
        arguments={"path": "src/a.py", "content": "x"},
        approval_id="s3-narrow",
    )
    env.adj.accept(h)
    r = await env.adj.adjudicate(h)
    resolved = env.app.replay(r.decision_id) if r.decision_id else None
    verdict = resolved.resolution.get("verdict") if resolved and resolved.resolution else None

    # Follow-up call outside narrow path → new decision
    _reset_unanimous(env.scenario, "allow")
    h2 = held(
        server="fs",
        tool="write_file",
        arguments={"path": "other/b.py", "content": "y"},
        approval_id="s3-followup",
    )
    env.adj.accept(h2)
    r2 = await env.adj.adjudicate(h2)

    return {
        "narrow_options": narrow,
        "binary_only_options": binary,
        "narrow_verdict": verdict,
        "narrow_approved": r.approved,
        "narrow_path": r.path,
        "followup_path": r2.path,
        "followup_needed_new_decision": r2.path == "quorum",
        "narrow_replaced_deny": verdict == "allow_narrow",
        "cost_time": collect_cost_time(env.app),
    }


async def run_s5(env: LadderEnv) -> dict[str, Any]:
    # Parent allow
    parent = env.app.open_decision(
        question="Parent policy",
        options=["allow", "deny"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={"k": 1},
        criticality="critical",
        ttl_seconds=600,
        scope=["fs/write_file"],
    )
    await env.app.run_model_quorum(parent["decision_id"], rng=random.Random(2))

    child = env.app.open_decision(
        question="Child depends on parent",
        options=["allow", "deny"],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={"k": 2},
        criticality="critical",
        ttl_seconds=600,
        scope=["fs/write_file"],
        depends_on=[parent["decision_id"]],
    )
    await env.app.run_model_quorum(child["decision_id"], rng=random.Random(3))

    # Cycle refuse
    cycle_err = None
    try:
        env.app.open_decision(
            question="Cycle",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"k": 3},
            criticality="routine",
            ttl_seconds=60,
            depends_on=[child["decision_id"]],
            # Will try to depend in a way that cycles if we also made child depend
            # on this — open a third that points back via invalidating graph:
        )
        # Force cycle: open d3 depends on child, then we'd need child→d3; instead
        # open with depends_on including itself via crafting edges — use parent→child
        # already; open x depends on child, then reopen impossible. Direct self-cycle:
        env.app.open_decision(
            question="Self",
            options=["allow", "deny"],
            voters=["voter-1", "voter-2", "voter-3"],
            evidence={"k": 4},
            criticality="routine",
            ttl_seconds=60,
            depends_on=["will-be-replaced"],
        )
    except Exception as exc:  # noqa: BLE001
        cycle_err = str(exc)

    # Explicit cycle: a depends on b, b depends on a
    from arbiter.domain.errors import DomainError

    a_id = "dec-cycle-a"
    # Use low-level Decision.open with fixed ids via monkey — easier: open b depending
    # on child, then try open that child already depends on parent — cycle via
    # invalidate graph test separately.
    cycle_refused = False
    try:
        # Manufacture cycle through domain helper
        from arbiter.domain.services.dependencies import assert_no_cycle

        assert_no_cycle("x", ["y"], {"y": ["x"]})
    except DomainError:
        cycle_refused = True

    cascaded = env.app.commands.invalidate_decision(
        parent["decision_id"], reason="operator_revoke"
    )
    child_state = env.app.replay(child["decision_id"])
    # Child still has resolution but invalidated event exists
    inv = {
        e["decision_id"]
        for e in env.app.read_all_wire()
        if e.get("event") == "decision.invalidated"
    }
    surprise = child["decision_id"] in inv and parent["decision_id"] in inv

    opens = [
        e for e in env.app.read_all_wire() if e.get("event") == "decision.opened"
    ]
    with_deps = [e for e in opens if e.get("depends_on")]
    return {
        "opens": len(opens),
        "with_dependencies": len(with_deps),
        "dependency_share": len(with_deps) / max(1, len(opens)),
        "cascade_ids": cascaded,
        "cascade_count": len(cascaded),
        "cascade_surprised_coverage": surprise,
        "cycle_refused": cycle_refused,
        "cycle_err": cycle_err,
        "cost_time": collect_cost_time(env.app),
    }


async def run_s6(env: LadderEnv) -> dict[str, Any]:
    # Establish rule via decision
    rule_open = env.app.open_decision(
        question="Require contract tests under src/**",
        options=["allow", "deny", ESCALATE],
        voters=["voter-1", "voter-2", "voter-3"],
        evidence={"rule": True},
        criticality="critical",
        ttl_seconds=600,
        scope=["policy/rule"],
        establishes_rule={
            "kind": "require_contract_test",
            "path_glob": "src/**",
            "detail": "writes under src require contract test",
            "rule_id": "rule-src-contract",
        },
    )
    await env.app.run_model_quorum(rule_open["decision_id"], rng=random.Random(4))

    # Write without contract test → rule_deny
    h = held(
        server="fs",
        tool="write_file",
        arguments={"path": "src/a.py", "content": "x"},
        approval_id="s6-deny-rule",
    )
    env.adj.accept(h)
    r = await env.adj.adjudicate(h)

    # Contract test recorded
    ct = held(
        server="fs",
        tool="contract_test",
        arguments={"path": "src/a.py"},
        approval_id="s6-contract",
    )
    env.adj.accept(ct)
    await env.adj.adjudicate(ct)

    h2 = held(
        server="fs",
        tool="write_file",
        arguments={"path": "src/a.py", "content": "y"},
        approval_id="s6-allow-rule",
    )
    env.adj.accept(h2)
    r2 = await env.adj.adjudicate(h2)

    # Escalation never passes
    _reset_unanimous(env.scenario, ESCALATE)
    adj_esc = HoldAdjudicator(
        env.app,
        intercept=parse_intercept_rules(yaml.safe_load(env.intercept.read_text())),
        resolver_principal=PRINCIPAL,
        min_round_seconds=1.0,
        enable_narrowing=False,
        include_escalate=True,
    )
    h3 = held(
        server="github",
        tool="create_issue",
        arguments={"title": "escalate-me"},
        approval_id="s6-esc",
    )
    adj_esc.accept(h3)
    r3 = await adj_esc.adjudicate(h3)

    holds = [
        e
        for e in env.app.read_all_wire()
        if e.get("event") == "hold.adjudicated"
    ]
    by_rule = [e for e in holds if e.get("path") in ("rule_allow", "rule_deny")]
    fresh = [e for e in holds if e.get("path") == "quorum"]
    return {
        "rule_deny_without_test": r.path == "rule_deny" and not r.approved,
        "rule_allow_with_test": r2.path == "rule_allow" and r2.approved,
        "escalate_approved": r3.approved,
        "escalate_path": r3.path,
        "escalate_is_pass": r3.approved is True and "escalate" in r3.reason,
        "calls_by_rule": len([e for e in by_rule if e.get("path") == "rule_allow"]),
        "calls_rule_denied": len([e for e in by_rule if e.get("path") == "rule_deny"]),
        "calls_fresh_quorum": len(fresh),
        "rule_vs_quorum_ratio": (
            len([e for e in by_rule if e.get("path") == "rule_allow"])
            / max(1, len(fresh))
        ),
        "cost_time": collect_cost_time(env.app),
    }
