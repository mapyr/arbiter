"""Evaluation report — computed only from the ledger (+ git for reversals)."""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arbiter.application.services.commit_guard import DECISION_TRAILER_RE
from arbiter.domain.timeutil import parse_iso

_REVERT_HINT = re.compile(
    r"(?i)\b(revert|hotfix|roll\s*back|rollback|undo)\b"
)


def _percentile(samples: list[float], pct: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _dist(samples: list[float]) -> dict[str, Any]:
    return {
        "n": len(samples),
        "p50": _percentile(samples, 50),
        "p90": _percentile(samples, 90),
        "p95": _percentile(samples, 95),
        "max": max(samples) if samples else None,
        "sum": sum(samples) if samples else 0.0,
    }


def build_eval_report(
    app: Any, *, repo: Path | None = None, horizon_days: int = 14
) -> dict[str, Any]:
    wire = app.read_all_wire()
    opens = [e for e in wire if e.get("event") == "decision.opened"]
    resolved = {
        e["decision_id"]: e
        for e in wire
        if e.get("event") == "decision.resolved"
    }
    baselines = {
        e["decision_id"]: e
        for e in wire
        if e.get("event") == "baseline.verdict"
    }
    round2 = {e["decision_id"] for e in wire if e.get("event") == "quorum.round2.opened"}
    votes = [e for e in wire if e.get("event") == "vote.cast"]
    holds = [e for e in wire if e.get("event") == "hold.adjudicated"]
    coverage = [e for e in wire if e.get("event") == "coverage.checked"]
    glass = [e for e in wire if e.get("event") == "break_glass.used"]

    shadow_n = sum(1 for e in opens if e.get("mode") == "shadow")
    enforce_n = sum(1 for e in opens if e.get("mode", "enforce") == "enforce")

    divergence = _divergence(opens, resolved, baselines)
    internal = _internal_dissent(opens, round2, votes)
    reveal = _reveal_efficacy(opens, round2, votes, resolved)
    cover = _coverage_mix(holds, coverage)
    cost = _cost_time(votes, holds)
    glass_dist = _glass(glass)
    reversals = _reversibility(resolved, baselines, repo=repo, horizon_days=horizon_days)

    thesis = _thesis(divergence, reversals, horizon_days=horizon_days)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "horizon_days": horizon_days,
        "sample": {
            "decisions_opened": len(opens),
            "shadow_opens": shadow_n,
            "enforce_opens": enforce_n,
            "resolved": len(resolved),
            "with_baseline": len(baselines),
            "caveat": (
                "Small N, short horizon, and rare critical decisions bias every "
                "rate below. A negative or inconclusive result is a valid outcome."
            ),
        },
        "divergence": divergence,
        "internal_dissent": internal,
        "reveal_round": reveal,
        "coverage": cover,
        "cost_time": cost,
        "break_glass": glass_dist,
        "reversibility": reversals,
        "thesis": thesis,
        "formulation_notes": _formulation_notes(opens, resolved, round2, holds),
    }

def _divergence(
    opens: list[dict],
    resolved: dict[str, dict],
    baselines: dict[str, dict],
) -> dict[str, Any]:
    comparable = 0
    disagree = 0
    pairs: list[dict[str, Any]] = []
    for op in opens:
        did = op["decision_id"]
        res = resolved.get(did)
        base = baselines.get(did)
        if res is None or base is None or not base.get("ok"):
            continue
        comparable += 1
        q_opt = res.get("chosen_option")
        b_opt = base.get("option")
        same = q_opt == b_opt
        if not same:
            disagree += 1
        pairs.append(
            {
                "decision_id": did,
                "mode": op.get("mode", "enforce"),
                "quorum_option": q_opt,
                "baseline_option": b_opt,
                "agree": same,
                "quorum_verdict": res.get("verdict"),
            }
        )
    rate = (disagree / comparable) if comparable else None
    return {
        "comparable": comparable,
        "disagree": disagree,
        "disagree_rate": rate,
        "upper_bound_value": rate,
        "pairs": pairs,
    }

def _internal_dissent(
    opens: list[dict],
    round2: set[str],
    votes: list[dict],
) -> dict[str, Any]:
    opened_ids = {e["decision_id"] for e in opens}
    r1_needed_reveal = len(round2 & opened_ids)
    return {
        "decisions": len(opened_ids),
        "round1_no_quorum": r1_needed_reveal,
        "rate": (r1_needed_reveal / len(opened_ids)) if opened_ids else None,
        "note": (
            "Round-1 miss is a useful signal of underspecified questions, "
            "not merely a failure mode."
        ),
    }

def _reveal_efficacy(
    opens: list[dict],
    round2: set[str],
    votes: list[dict],
    resolved: dict[str, dict],
) -> dict[str, Any]:
    changed = 0
    with_reason = 0
    settled_allow = 0
    for did in round2:
        r1 = {v["voter"]: v for v in votes if v.get("decision_id") == did and int(v.get("round", 1)) == 1}
        r2 = {v["voter"]: v for v in votes if v.get("decision_id") == did and int(v.get("round", 1)) == 2}
        for voter, v2 in r2.items():
            v1 = r1.get(voter)
            if v1 and v1.get("option") != v2.get("option"):
                changed += 1
                if v2.get("revision_reason"):
                    with_reason += 1
        res = resolved.get(did)
        if res and res.get("verdict") == "allow":
            settled_allow += 1
    n = len(round2)
    return {
        "reveal_triggered": n,
        "option_changes": changed,
        "changes_with_revision_reason": with_reason,
        "settled_allow_after_reveal": settled_allow,
        "settled_allow_rate": (settled_allow / n) if n else None,
        "concession_risk_flag": (
            n > 0
            and settled_allow / n >= 0.9
            and changed > 0
            and (with_reason / changed if changed else 0) < 0.5
        ),
    }

def _coverage_mix(holds: list[dict], coverage: list[dict]) -> dict[str, Any]:
    paths = Counter(e.get("path") for e in holds + coverage)
    total = sum(paths.values())
    covered = paths.get("covered", 0) + paths.get("duplicate", 0)
    quorum = paths.get("quorum", 0)
    return {
        "counts": dict(paths),
        "total": total,
        "covered_share": (covered / total) if total else None,
        "forced_quorum_share": (quorum / total) if total else None,
    }

def _cost_time(votes: list[dict], holds: list[dict]) -> dict[str, Any]:
    lat = [float(v["latency_ms"]) for v in votes if isinstance(v.get("latency_ms"), (int, float))]
    hold_ms = [
        float(h["duration_ms"])
        for h in holds
        if isinstance(h.get("duration_ms"), (int, float))
    ]
    tokens_in = [
        int(v["prompt_tokens"])
        for v in votes
        if isinstance(v.get("prompt_tokens"), int)
    ]
    tokens_out = [
        int(v["completion_tokens"])
        for v in votes
        if isinstance(v.get("completion_tokens"), int)
    ]
    return {
        "vote_latency_ms": _dist(lat),
        "hold_duration_ms": _dist(hold_ms),
        "prompt_tokens": _dist([float(x) for x in tokens_in]),
        "completion_tokens": _dist([float(x) for x in tokens_out]),
    }

def _glass(glass: list[dict]) -> dict[str, Any]:
    by_actor = Counter(e.get("actor") or "unknown" for e in glass)
    return {"n": len(glass), "by_actor": dict(by_actor)}

def _reversibility(
    resolved: dict[str, dict],
    baselines: dict[str, dict],
    *,
    repo: Path | None,
    horizon_days: int,
) -> dict[str, Any]:
    """Link decision trailers in git to later revert/hotfix commits."""
    commits = _git_decision_commits(repo)
    horizon = timedelta(days=horizon_days)
    # Cases: quorum deny + baseline allow vs quorum allow + baseline deny
    q_deny_b_allow_rev = 0
    q_deny_b_allow_n = 0
    q_allow_b_deny_rev = 0
    q_allow_b_deny_n = 0

    for did, res in resolved.items():
        base = baselines.get(did)
        if not base or not base.get("ok"):
            continue
        b_opt = base.get("option")
        # Hold-shaped closed sets use allow/deny; that is the thesis pair.
        if res.get("verdict") == "deny" and b_opt == "allow":
            q_deny_b_allow_n += 1
            if _reversed_after(did, commits, horizon, repo=repo):
                q_deny_b_allow_rev += 1
        if res.get("verdict") == "allow" and b_opt == "deny":
            q_allow_b_deny_n += 1
            if _reversed_after(did, commits, horizon, repo=repo):
                q_allow_b_deny_rev += 1

    return {
        "commits_with_decision_trailer": len(commits),
        "quorum_deny_baseline_allow": {
            "n": q_deny_b_allow_n,
            "reversed": q_deny_b_allow_rev,
            "reversal_rate": (
                q_deny_b_allow_rev / q_deny_b_allow_n if q_deny_b_allow_n else None
            ),
        },
        "quorum_allow_baseline_deny": {
            "n": q_allow_b_deny_n,
            "reversed": q_allow_b_deny_rev,
            "reversal_rate": (
                q_allow_b_deny_rev / q_allow_b_deny_n if q_allow_b_deny_n else None
            ),
        },
        "method": (
            f"git log trailers matched to later commits within {horizon_days}d "
            "whose subject/body match revert|hotfix|rollback"
        ),
    }

def _git_decision_commits(repo: Path | None) -> list[dict[str, Any]]:
    repo = repo or Path.cwd()
    if not (repo / ".git").exists():
        return []
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "--all",
            "--format=%H%n%cI%n%B%n--END--",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    chunks = proc.stdout.split("--END--\n")
    for chunk in chunks:
        lines = chunk.strip().splitlines()
        if len(lines) < 2:
            continue
        sha, when, *body_lines = lines
        body = "\n".join(body_lines)
        match = DECISION_TRAILER_RE.search(body)
        if not match:
            continue
        try:
            at = parse_iso(when)
        except Exception:  # noqa: BLE001
            continue
        out.append(
            {
                "sha": sha,
                "at": at,
                "decision_id": match.group(1),
                "body": body,
            }
        )
    return out

def _reversed_after(
    decision_id: str,
    commits: list[dict[str, Any]],
    horizon: timedelta,
    *,
    repo: Path | None,
) -> bool:
    relevant = [c for c in commits if c["decision_id"] == decision_id]
    if not relevant:
        return False
    # Any later commit in repo within horizon that looks like a revert —
    # linked by mentioning the decision id or following the decision commit.
    repo = repo or Path.cwd()
    for base in relevant:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                f"{base['sha']}..HEAD",
                "--format=%s%n%b%n--END--",
                f"--since={base['at'].isoformat()}",
                f"--until={(base['at'] + horizon).isoformat()}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            continue
        for chunk in proc.stdout.split("--END--"):
            if _REVERT_HINT.search(chunk) or decision_id in chunk:
                if _REVERT_HINT.search(chunk):
                    return True
    return False

def _thesis(divergence: dict, reversals: dict, *, horizon_days: int) -> dict[str, Any]:
    a = reversals["quorum_deny_baseline_allow"]
    b = reversals["quorum_allow_baseline_deny"]
    # "Better" = lower reversal rate when quorum denied vs when it allowed against baseline
    conclusion = "inconclusive"
    detail = "Insufficient paired reversibility samples."
    if a["n"] and b["n"] and a["reversal_rate"] is not None and b["reversal_rate"] is not None:
        if a["reversal_rate"] < b["reversal_rate"]:
            conclusion = "quorum_adds_value"
            detail = (
                "Cases where quorum denied and baseline allowed reversed less often "
                "than the opposite disagreement — tentative support for quorum."
            )
        elif a["reversal_rate"] > b["reversal_rate"]:
            conclusion = "baseline_better"
            detail = (
                "Opposite pattern: baseline looks safer on reversibility. "
                "Quorum may not justify 3× cost."
            )
        else:
            conclusion = "no_difference"
            detail = "Reversal rates match on thin sample."
    elif divergence["comparable"] and (divergence["disagree_rate"] or 0) < 0.05:
        conclusion = "likely_not_worth_3x"
        detail = (
            "Quorum and baseline agree on ≥95% of comparable decisions; "
            "upper bound on quorum's unique value is tiny before reversibility."
        )
    return {
        "conclusion": conclusion,
        "detail": detail,
        "disagree_rate": divergence.get("disagree_rate"),
        "caveats": [
            "Sample size may be too small for significance.",
            f"Reversibility horizon is {horizon_days} days — hotfixes later are invisible.",
            "Critical decisions are rare; rates are noisy.",
            "Reversibility is a cheap proxy, not ground truth about correctness.",
        ],
    }

def _formulation_notes(
    opens: list[dict],
    resolved: dict[str, dict],
    round2: set[str],
    holds: list[dict],
) -> dict[str, Any]:
    unanimous = []
    dissent = []
    for op in opens:
        did = op["decision_id"]
        res = resolved.get(did)
        if not res:
            continue
        row = {
            "decision_id": did,
            "options_n": len(op.get("options") or []),
            "scope_n": len(op.get("scope") or []),
            "scope": list(op.get("scope") or []),
        }
        if did in round2 or res.get("verdict") != "allow":
            dissent.append(row)
        else:
            unanimous.append(row)
    return {
        "unanimous_allow_n": len(unanimous),
        "dissent_or_deny_n": len(dissent),
        "median_options_unanimous": _percentile(
            [float(r["options_n"]) for r in unanimous], 50
        ),
        "median_options_dissent": _percentile(
            [float(r["options_n"]) for r in dissent], 50
        ),
        "median_scope_unanimous": _percentile(
            [float(r["scope_n"]) for r in unanimous], 50
        ),
        "median_scope_dissent": _percentile(
            [float(r["scope_n"]) for r in dissent], 50
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report["sample"]
    d = report["divergence"]
    t = report["thesis"]
    lines = [
        "# Arbiter evaluation report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Horizon: **{report['horizon_days']} days**",
        "",
        "## Sample",
        "",
        f"- Decisions opened: **{s['decisions_opened']}** "
        f"(shadow={s['shadow_opens']}, enforce={s['enforce_opens']})",
        f"- Resolved: **{s['resolved']}**; with baseline: **{s['with_baseline']}**",
        f"- Caveat: {s['caveat']}",
        "",
        "## Divergence (quorum vs baseline)",
        "",
        f"- Comparable pairs: **{d['comparable']}**",
        f"- Disagreements: **{d['disagree']}** (rate={d['disagree_rate']})",
        "",
        "## Internal dissent / reveal",
        "",
        f"- Round-1 no quorum: {report['internal_dissent']}",
        f"- Reveal: {report['reveal_round']}",
        "",
        "## Coverage mix",
        "",
        f"{report['coverage']}",
        "",
        "## Cost & time (distributions)",
        "",
        f"{report['cost_time']}",
        "",
        "## Break-glass",
        "",
        f"{report['break_glass']}",
        "",
        "## Reversibility thesis",
        "",
        f"- Conclusion: **{t['conclusion']}**",
        f"- {t['detail']}",
        "",
        "Caveats:",
    ]
    for c in t["caveats"]:
        lines.append(f"- {c}")
    lines.extend(["", "## Formulation signals", "", f"{report['formulation_notes']}", ""])
    return "\n".join(lines)
