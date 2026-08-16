# Arbiter

**Deterministic MCP decision gateway** with an append-only JSONL ledger.

Arbiter sits between an AI coding agent and sensitive actions (file edits, held
MCP tools). It opens a closed-option decision, collects immutable votes (human
or model), resolves by quorum, and records everything in a replayable ledger.
Missing votes, missing coverage, or missing rules **deny** — never fail open.

```text
Agent (OpenCode)
  ├─ built-in tools  → L2 plugin → Hangar → arbiter (plan + coverage)
  └─ MCP tools       → Hangar hold → arbiter (coverage or model quorum) → resolve
```

## Why

Agents are good at planning and editing; they are a weak source of truth for
“is this change allowed?”. Arbiter keeps that verdict in one place:

- closed option sets (no free-form inventing of outcomes)
- one vote per `(decision_id, voter, round)` — never overwritten
- critical = unanimous roster; routine = full roster + strict majority
- provider failure → no vote → deny

## Quick start

### Preferred demo (Podman)

Requires Podman, Python 3.11+, and voter API keys (see
[docs/cookbooks/podman.md](docs/cookbooks/podman.md)).

```bash
./deploy/podman/up.sh --reset
source /tmp/arbiter-podman/env.sh
cd "$OPENCODE_PROJECT" && opencode
```

Flow for edits: plugin denies uncovered mutations with `ARBITER_PLAN_REQUIRED` →
agent calls Hangar → `arbiter/ensure_plan` with a structured plan → retry edit.

### Library / MCP server

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"

cp arbiter.rules.yaml.example arbiter.rules.yaml
cp arbiter.voters.yaml.example arbiter.voters.yaml

export ARBITER_DATA_DIR="$(pwd)/decisions"
export ARBITER_RULES_PATH="$(pwd)/arbiter.rules.yaml"

arbiter serve                                          # stdio
# HTTP (Hangar / remote clients):
export ARBITER_HTTP_SECRET='replace-me'
arbiter serve --transport http --host 127.0.0.1 --port 8765
```

Ledger: `$ARBITER_DATA_DIR/ledger.jsonl` · evidence bundles · raw model replies
under `responses/`.

## Documentation

| Doc | Purpose |
|-----|---------|
| **[docs/tutorial.md](docs/tutorial.md)** | Install → decision → Hangar → shadow |
| **[docs/how-it-works.md](docs/how-it-works.md)** | Roles, two gates, ledger, fail-closed |
| **[docs/cookbooks/](docs/cookbooks/README.md)** | Podman, layers, Hangar, formulation |
| [docs/README.md](docs/README.md) | Doc index |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup and PR expectations |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Configuration

Three YAML files (examples in repo root; local copies are gitignored where
secrets might land):

| File | Role |
|------|------|
| `arbiter.rules.yaml` | Criticality paths, formulation barriers, `client_gate.plan` |
| `arbiter.voters.yaml` | 1–7 OpenAI-compatible voters (repeat providers OK) + shadow/enforce |
| `arbiter.intercept.yaml` | Which Hangar MCP tools Arbiter adjudicators |

Missing or invalid rules → **everything is critical** (fail-closed).

## MCP tools

| Tool | Role |
|------|------|
| `open_decision` | Open a decision; optional `scope` for later coverage |
| `get_gate_policy` | Client plan policy (`session` \| `on_uncovered`) |
| `ensure_plan` | Validate plan → open → model quorum (covering allow) |
| `cast_vote` | Immutable vote (`round` defaults to 1) |
| `get_decision` | Replay state from the ledger |
| `resolve_decision` | Apply quorum (idempotent) |
| `run_model_quorum` | Blind (+ optional reveal) model rounds |
| `check_coverage` | Path coverage against prior allow scopes |

CLI highlights: `arbiter check-coverage`, `arbiter ensure-plan --plan-file …`,
`arbiter hangar-call`, `arbiter get-gate-policy`, `arbiter verify-commit`,
`arbiter report-eval`.

## Client layers (OpenCode)

Built-in edits never leave the IDE, so MCP alone is not enough:

1. **L1** — declarative permissions (`client/opencode/opencode.permissions.jsonc`)
2. **L2** — thin plugin `arbiter-gate.js` → Hangar → `get_gate_policy` /
   `check_coverage` (CLI fallback without Hangar)
3. **L3** — `arbiter verify-commit` + CI (`Arbiter-Decision: <id>` trailer)

Details: [docs/cookbooks/client-layers.md](docs/cookbooks/client-layers.md).

> **OpenCode naming:** MCP tools are registered as
> `{serverKey}_{toolName}`. Hangar’s own tools are already named `hangar_*`, so
> with `"mcp": { "hangar": … }` you see `hangar_hangar_call`. That is OpenCode
> behaviour, not a Hangar bug.

## Model quorum

1. **Round 1 (blind)** — identical prompt to all voters; invalid JSON → one
   retry; then `vote.failed` (no vote).
2. **Round 2 (reveal)** — only if everyone cast and quorum is still unmet;
   peers as opaque `A`, `B`, … (one label per roster slot).
3. Votes go only through `cast_vote`. Resolution uses the highest round.

Copy `arbiter.voters.yaml.example`, set `api_key_env` keys in the environment.
Ids must match `voters[]` on `open_decision`. Live check (not CI):
[docs/cookbooks/live-quorum.md](docs/cookbooks/live-quorum.md)
(`./scripts/live-quorum.sh`).

## Hangar approval delivery

Optional: held MCP calls notify Arbiter via the public entry point
`mcp_hangar.approvals.delivery` (`pip install "arbiter[hangar]"`,
**mcp-hangar 2.6.0**). Flow: coverage → else quorum → REST resolve.
Cookbook: [docs/cookbooks/hangar-delivery.md](docs/cookbooks/hangar-delivery.md).

## Shadow evaluation & richer decisions

- **Shadow** — quorum still runs and is ledgered; holds are not gated (measure
  cost vs baseline). `arbiter report-eval --horizon-days 14`
- **Decision structure** — preconditions, narrowing, dependencies, installed
  rules. Offline check:
  `pytest tests/ladder -q`

## HTTP shared secret

HTTP accepts requests only when `X-Arbiter-Secret` equals `ARBITER_HTTP_SECRET`.
Same `401` for missing and wrong secret. This is **not** a full security model
— see [SECURITY.md](SECURITY.md).

## Tests & CI

```bash
pytest
pytest -m "not integration"
python scripts/check_hexagon_boundaries.py
```

CI: hexagon boundaries, domain coverage floors, unit + integration matrices
(Python 3.11–3.13). No live provider keys in CI.

## What this package does not do

- Act as a “smart aggregator” that decides after hearing peers
- Give voters tools, repo access, or network beyond chat completions
- Stream partial votes or early-close a decision
- Replace CI — L3 commit gate is a separate backstop

## License

[MIT](LICENSE)
