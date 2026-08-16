# Tutorial: using Arbiter

Hands-on guide from zero to a working decision gateway.
Theory → [`how-it-works.md`](./how-it-works.md).
Task guides → [`cookbooks/`](./cookbooks/README.md).

---

## Contents

1. [What you get](#1-what-you-get)
2. [Install (5 minutes)](#2-install-5-minutes)
3. [Three config files](#3-three-config-files)
4. [Path A — manual decision (MCP / stdio)](#4-path-a--manual-decision-mcp--stdio)
5. [Path B — model quorum](#5-path-b--model-quorum)
6. [Path C — coverage CLI and plan gate (L2)](#6-path-c--coverage-cli-and-plan-gate-l2)
7. [Path D — Hangar + hold (preferred stack)](#7-path-d--hangar--hold-preferred-stack)
8. [Shadow vs enforce](#8-shadow-vs-enforce)
9. [Commit gate (L3)](#9-commit-gate-l3)
10. [Richer decisions](#10-richer-decisions)
11. [Where data lives](#11-where-data-lives)
12. [Environment variables](#12-environment-variables)
13. [Common errors](#13-common-errors)
14. [Ready checklist](#14-ready-checklist)

---

## 1. What you get

Arbiter is a **decision gateway**, not an agent and not a file editor.

| Entry | What happens |
|-------|----------------|
| MCP tools (`open_decision`, …) | Open a decision, collect votes, resolve |
| `ensure_plan` / `check_coverage` | Structured plan → covering allow; path coverage for clients |
| Hangar approval delivery | Held MCP tool → coverage or quorum → approve/deny |
| CLI `verify-commit` | Critical-path commits need `Arbiter-Decision:` trailer |

**Fail-closed:** missing vote, missing cover, missing rules → **deny**, never a silent “OK”.

---

## 2. Install (5 minutes)

```bash
git clone https://github.com/mapyr/arbiter.git && cd arbiter
python3 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"          # package + pytest; Hangar extras included in dev

# working config (do not commit secrets)
cp arbiter.rules.yaml.example arbiter.rules.yaml
cp arbiter.voters.yaml.example arbiter.voters.yaml
cp arbiter.intercept.yaml.example arbiter.intercept.yaml

export ARBITER_DATA_DIR="$(pwd)/decisions"
export ARBITER_RULES_PATH="$(pwd)/arbiter.rules.yaml"
export ARBITER_VOTERS_PATH="$(pwd)/arbiter.voters.yaml"

# quick sanity check
pytest -q -m "not integration"
arbiter --help
```

Start the MCP server:

```bash
# stdio — local tests / direct MCP client
arbiter serve

# HTTP — Hangar / remote client
export ARBITER_HTTP_SECRET='replace-me'
arbiter serve --transport http --host 127.0.0.1 --port 8765
# client must send header: X-Arbiter-Secret: replace-me
```

---

## 3. Three config files

### `arbiter.rules.yaml` — what is critical

```yaml
critical:
  paths:
    - "**/auth/**"
    - "**/migrations/**"
    - "infra/**"
default: routine

formulation:
  deny_universal_scope: true   # refuse decisions with universal scope
  deny_filler_options: true

client_gate:
  plan:
    mode: on_uncovered         # or session
    arbiter_mcp_server: arbiter
```

- Missing file / parse error → **everything critical** (fail-closed).
- Clients **do not** copy these rules — they live only here.

### `arbiter.voters.yaml` — voter roster

```yaml
voters:
  - id: voter-1
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: ARBITER_VOTER_1_KEY
    temperature: 0
    max_tokens: 1200
    timeout_seconds: 45
  # voter-2, voter-3, … — same shape (1..7; same provider + different model OK)

round_deadline_seconds: 60
reveal_round: true
shadow_mode: false
baseline_voter: voter-1        # single-model reference line (not mixed into quorum)
```

API keys go in the env named by `api_key_env`, **not** in YAML.

YAML `id`s must be **identical** to the `voters` list on `open_decision`.

### `arbiter.intercept.yaml` — which Hangar tools Arbiter adjudicates

```yaml
hold:
  - mcp_server: "github"
    tool: "create_issue"
  - mcp_server: "filesystem"
    tool: "write_*"
```

Hangar must also list the tool on its own `approval_list`.
Missing intercept file at delivery start → adapter refuses to start.

---

## 4. Path A — manual decision (MCP / stdio)

Simplest flow without Hangar and without models — you (or a script) vote.

### 4.1 Start

```bash
export ARBITER_DATA_DIR="$(pwd)/decisions"
export ARBITER_RULES_PATH="$(pwd)/arbiter.rules.yaml"
arbiter serve   # stdio — attach an MCP client or use tests
```

### 4.2 MCP tools

| Tool | Purpose |
|------|---------|
| `open_decision` | Question, closed options, roster, evidence, optional `scope` |
| `ensure_plan` | Structured plan → open + model quorum |
| `cast_vote` | One vote; key `(decision_id, voter, round)` — no overwrite |
| `get_decision` | State from ledger replay |
| `resolve_decision` | Compute quorum (idempotent) |
| `run_model_quorum` | Roster from YAML instead of manual votes |
| `check_coverage` | Whether paths are covered by an earlier allow |
| `get_gate_policy` | Client plan mode + MCP server name |

### 4.3 Example (call semantics)

**1. Open a decision**

```json
{
  "question": "Allow writes under migrations/ for policy X?",
  "options": ["allow", "deny"],
  "voters": ["voter-1", "voter-2", "voter-3"],
  "evidence": { "policy": "X", "paths": ["migrations/001.sql"] },
  "criticality": "critical",
  "ttl_seconds": 900,
  "scope": ["migrations/**"]
}
```

Response includes `decision_id` and `bundle_sha256` (evidence digest).

**2. Three votes** (each voter once in round 1):

```json
{
  "decision_id": "<id>",
  "voter": "voter-1",
  "option": "allow",
  "confidence": 0.9,
  "kill_criterion": "Roll back the migration on red smoke.",
  "bundle_sha256": "<same as at open>"
}
```

**3. Resolve**

```json
{ "decision_id": "<id>" }
```

- `critical` → everyone must vote **and** be unanimous.
- `routine` → everyone + strict majority of the roster.
- Missing vote / tie / deadline → `deny`.

**4. Cover later paths**

```bash
arbiter check-coverage \
  --path migrations/001.sql \
  --tool edit \
  --decision-id <id> \
  --json
```

`approved: true` only when the decision is `allow`/`allow_narrow`, scope covers
the paths, and it has not expired.

---

## 5. Path B — model quorum

Roster is **1–7** voters from `arbiter.voters.yaml` (unique `id`; same provider
with different `model` is fine). Examples below use three.

### 5.1 Keys and config

```bash
export ARBITER_VOTER_1_KEY='…'
export ARBITER_VOTER_2_KEY='…'
export ARBITER_VOTER_3_KEY='…'
export ARBITER_VOTERS_PATH="$(pwd)/arbiter.voters.yaml"
```

Endpoints must be **OpenAI-compatible** (`/v1/chat/completions`).

### 5.2 Flow

1. `open_decision` (as above; `voters` = ids from YAML).
2. `run_model_quorum` with `{ "decision_id": "…" }`.
3. Arbiter:
   - **Round 1 (blind)** — same prompt to three; no peer votes.
   - If quorum unmet and `reveal_round: true` → **round 2** with labels A, B, …
   - Votes only via `cast_vote` (same rules).
4. Result + latencies `p50_ms` / `p95_ms` in the response.

### 5.3 What models may / may not do

May: pick `option` from the closed list + `confidence` + `kill_criterion`.

Must not: invent options, rewrite tool arguments, call tools, or reach the
network beyond the chat API.

### 5.4 Offline smoke (no keys)

```bash
pytest tests/test_hold_adjudication.py tests/ladder -q
```

OpenAI stub in `tests/openai_stub.py` — zero real providers in CI.

### 5.5 Live smoke (three independent providers)

With OpenAI + OpenRouter + Gemini keys:

```bash
export ARBITER_VOTER_1_KEY='…'
export ARBITER_VOTER_2_KEY='…'
export ARBITER_VOTER_3_KEY='…'
./scripts/live-quorum.sh
```

Cookbook: [`cookbooks/live-quorum.md`](./cookbooks/live-quorum.md). Missing any
key exits before HTTP. Hangar/OpenCode demo is a separate path
([`cookbooks/live-smoke.md`](./cookbooks/live-smoke.md)).

---

## 6. Path C — coverage CLI and plan gate (L2)

The client plugin (OpenCode `arbiter-gate`) does **not** run quorum on every
`edit`. With Hangar configured it asks Arbiter through Hangar; otherwise:

```bash
arbiter check-coverage --path arbiter/domain/x.py --tool edit --json
# exit 0 + approved → allow
# else → deny (fail-closed)
```

When `client_gate.plan.mode` is `on_uncovered` / `session`, an uncovered
mutation surfaces as `ARBITER_PLAN_REQUIRED`. Obtain a covering allow, then retry:

```bash
# Local CLI (needs voters + keys; writes to ARBITER_DATA_DIR):
cat > /tmp/plan.json <<'EOF'
{
  "goal": "Add module docstring",
  "steps": [{"action": "edit docstring", "paths": ["auth/handler.py"]}],
  "scope": ["auth/**"]
}
EOF
arbiter ensure-plan --plan-file /tmp/plan.json

# Preferred in the Podman/OpenCode demo — same tool via Hangar
# (OpenCode may show the tool as hangar_hangar_call):
arbiter hangar-call --tool ensure_plan --arguments-json '{
  "plan": {
    "goal": "Add module docstring",
    "steps": [{"action": "edit docstring", "paths": ["auth/handler.py"]}],
    "scope": ["auth/**"]
  }
}'
```

CLI flag is `--plan-file` (JSON file), not inline `--plan-json`.
Scope must not be universal (`**/*` is refused by formulation). After
`approved: true`, retry the original tool. Absolute editor paths still match
relative scope (e.g. `/tmp/project/auth/x.py` vs `auth/**`).

Break-glass (emergency, always visible in the ledger):

```bash
ARBITER_BREAK_GLASS=1 arbiter check-coverage \
  --path arbiter/domain/x.py \
  --tool edit \
  --break-glass \
  --break-glass-reason "prod hotfix ack by oncall"
```

---

## 7. Path D — Hangar + hold (preferred stack)

Target layout: agent → Hangar → (hold) → Arbiter → resolve → tool.

### 7.1 Podman (recommended)

```bash
./deploy/podman/up.sh --reset
source /tmp/arbiter-podman/env.sh

# discovery registered arbiter?
curl -sS -H "X-API-Key: $HANGAR_API_KEY" \
  "$HANGAR_URL/api/mcp_servers" | jq .

./deploy/podman/up.sh --logs
./deploy/podman/up.sh --down
```

Details (labels, `/health`, socket): [`cookbooks/podman.md`](./cookbooks/podman.md).

### 7.2 What happens on a hold

```text
Agent calls a tool on approval_list
        ↓
Hangar holds + delivery → Arbiter
        ↓
1) installed rules?
2) ledger precondition?
3) coverage from earlier allow / allow_narrow?
4) else: open_decision + narrowing options + run_model_quorum
        ↓
POST /approvals/{id}/resolve  (approve | deny)
        ↓
Hangar runs upstream or rejects
```

### 7.3 Alternative: host processes

```bash
./scripts/live-smoke-up.sh
# cookbook: docs/cookbooks/live-smoke.md
```

### 7.4 OpenCode

1. Hangar as MCP server (URL from `env.sh`).
2. L2 plugin from `client/opencode/plugins/` (Podman `up.sh` copies it into the
   demo project) + env from `env.sh`.
3. Optional `ARBITER_GATE_ALL=1` — gate nearly all tools (deep demo).

---

## 8. Shadow vs enforce

| Mode | Quorum / baseline | Tool gate |
|------|-------------------|-----------|
| **enforce** (`shadow_mode: false`) | yes | **yes** — deny blocks |
| **shadow** (`shadow_mode: true` or `ARBITER_SHADOW_MODE=1`) | yes + `baseline.verdict` | **no** — call proceeds |

Evaluation after a work window:

```bash
arbiter report-eval --horizon-days 14 --format markdown
arbiter report-eval --horizon-days 14 --format json
```

Reports quorum↔baseline disagreement, cost/time (p50/p90/p95), coverage mix,
and git reversibility from trailers.

---

## 9. Commit gate (L3)

Critical-path commits must carry:

```text
Arbiter-Decision: <decision_id>
```

Check (CI / hook):

```bash
arbiter verify-commit --paths-from staged --message-file .git/COMMIT_EDITMSG
# or:
arbiter verify-commit --paths-from range --base origin/main --message-file …
```

Break-glass on commit needs an explicit ack: `--allow-break-glass` /
`ARBITER_ALLOW_BREAK_GLASS=1`.

---

## 10. Richer decisions

Complexity should live in the **decision object**, not harder voting.

| Feature | Behaviour | Default |
|---------|-----------|---------|
| Preconditions | e.g. `migrate.apply` only if ledger has matching trial/dry-run hash | on in hold path |
| Narrowing | options include `allow_narrow:ttl=…;paths=…` — model **picks from list** | on |
| Dependencies | `depends_on` at open; cascade invalidation; cycle → refuse | on |
| Installed rules | `establishes_rule`; runtime enforces before quorum; `escalate_to_human` does not pass | on |

Offline check:

```bash
pytest tests/ladder -q
```

Opening with dependency / rule uses the **Application API** (hold path and
`tests/ladder` exercise this). The public MCP `open_decision` tool currently
exposes `question` / `options` / `voters` / `evidence` / `scope` / `mode` only —
not `depends_on` / `establishes_rule`:

```python
from arbiter.bootstrap import create_application

app = create_application()  # respects ARBITER_* env
app.open_decision(
    question="Require contract tests under src/**",
    options=["allow", "deny", "escalate_to_human"],
    voters=["voter-1", "voter-2", "voter-3"],
    evidence={"rule": True},
    scope=["policy/rule"],
    depends_on=["d-parent-id"],
    establishes_rule={
        "kind": "require_contract_test",
        "path_glob": "src/**",
        "detail": "writes under src require contract test",
        "rule_id": "rule-src-contract",
    },
)
```

---

## 11. Where data lives

With `ARBITER_DATA_DIR=…/decisions`:

```text
decisions/
  ledger.jsonl                          # append-only events
  bundles/<sha256>.json                 # evidence (content-addressed)
  responses/<decision_id>/<voter>-rN.json
```

Example ledger events: `decision.opened`, `vote.cast`, `vote.failed`,
`decision.resolved`, `hold.accepted`, `hold.adjudicated`, `coverage.checked`,
`baseline.verdict`, `break_glass.used`, `rule.established`,
`decision.invalidated`.

**Never** hand-edit `ledger.jsonl` — replayability depends on append-only + hashes.

---

## 12. Environment variables

| Variable | Role |
|----------|------|
| `ARBITER_DATA_DIR` | Ledger / bundles / responses directory |
| `ARBITER_RULES_PATH` | `arbiter.rules.yaml` |
| `ARBITER_VOTERS_PATH` | `arbiter.voters.yaml` |
| `ARBITER_HTTP_SECRET` | HTTP shared secret (`X-Arbiter-Secret`) |
| `ARBITER_ALLOW_INSECURE_HTTP` | `1` = HTTP without secret (private net / Podman only) |
| `ARBITER_HTTP_MCP_PATH` | MCP path (`/mcp` or `/` for discovery) |
| `ARBITER_SHADOW_MODE` | `1` = shadow (observe, do not gate) |
| `ARBITER_BREAK_GLASS` | `1` = allow break-glass in check-coverage |
| `ARBITER_ALLOW_BREAK_GLASS` | `1` = ack break-glass in verify-commit |
| `ARBITER_HANGAR_RESOLVE_TOKEN` | Token for Hangar resolve |
| `ARBITER_HANGAR_PRINCIPAL_ID` | Delivery principal |
| `ARBITER_VOTER_*_KEY` | Keys per `api_key_env` in voters YAML |
| `HANGAR_URL` / `HANGAR_API_KEY` | L2 plugin Hangar transport |
| `ARBITER_MCP_SERVER` | Injected Arbiter server id inside Hangar (default `arbiter`) |
| `ARBITER_GATE_ALL` / `ARBITER_ALLOW_BASH` | OpenCode plugin behaviour |

---

## 13. Common errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `voters mismatch` | open ids ≠ YAML | Align `voters[]` with YAML `id`s (1–7) |
| Everything `critical` | missing / bad rules | Set `ARBITER_RULES_PATH`, fix YAML |
| HTTP `401` | missing / wrong secret | `X-Arbiter-Secret` = `ARBITER_HTTP_SECRET` |
| Hangar “noop delivery” | stock image without entry point | Use `./deploy/podman/up.sh` (custom hangar image) |
| Discovery misses arbiter | wrong socket / published port | Rootful sock + no arbiter host publish; MCP on `/` |
| `check-coverage` always deny | no earlier allow with scope | Run `ensure_plan` / Hangar hold / `open_decision` + resolve first |
| `ARBITER_PLAN_REQUIRED` then still deny | absolute path vs relative scope (fixed in ≥0.7.1) or universal scope refused | Use e.g. `auth/**`; restart stack after upgrade |
| `precondition_denied` on migrate | no dry-run in ledger | Run trial with the same `arguments_hash` |
| Model “invents” an option | reply outside closed set | One retry; second fail → `vote.failed` → deny |
| Commit rejected | missing trailer on critical path | Add `Arbiter-Decision: <id>` |
| OpenCode `hangar_hangar_call` | server key `hangar` + tool `hangar_call` | Expected — call the name your session lists |

---

## 14. Ready checklist

- [ ] `pip install ".[dev]"` and `pytest -q` pass locally
- [ ] Copied `rules` / `voters` / `intercept` (no secrets in git)
- [ ] `arbiter serve` starts; ledger appears under `ARBITER_DATA_DIR`
- [ ] Can open a decision + resolve (manually or `run_model_quorum`)
- [ ] `check-coverage` on a scoped path returns `approved` after allow
- [ ] (demo) `./deploy/podman/up.sh --reset` → discovery registers `arbiter`
- [ ] Know whether you run **shadow** (measure) or **enforce** (gate)
- [ ] Critical commits carry trailer; CI runs `verify-commit`

---

## Further reading

| Document | When |
|----------|------|
| [how-it-works.md](./how-it-works.md) | Roles and two gates |
| [cookbooks/podman.md](./cookbooks/podman.md) | Container demo |
| [cookbooks/live-quorum.md](./cookbooks/live-quorum.md) | Three live providers, one `ensure-plan` |
| [cookbooks/live-smoke.md](./cookbooks/live-smoke.md) | Host-process smoke |
| [cookbooks/client-layers.md](./cookbooks/client-layers.md) | L1/L2/L3 in the client |
| [cookbooks/formulation.md](./cookbooks/formulation.md) | Scope / options barriers |
| [cookbooks/hangar-delivery.md](./cookbooks/hangar-delivery.md) | Hangar hold wiring |
| [../README.md](../README.md) | Tool contract, quorum thresholds, tests |
