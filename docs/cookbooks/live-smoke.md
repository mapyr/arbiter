# Cookbook: live smoke (host processes)

Same deep-enforce demo as Podman, but Arbiter + Hangar run on the host under
`/tmp/arbiter-live`. Prefer **[podman.md](./podman.md)** for the plan-gate /
`ensure_plan` flow; use this when you want hold / L2 checks without containers.

## Quick start

```bash
cd /path/to/arbiter
./scripts/live-smoke-up.sh --reset
source /tmp/arbiter-live/env.sh
```

Override: `LIVE=…` `OPENCODE_MODEL=github-copilot/gpt-4o`.

### What the script hardens

| Layer | Setting |
|-------|---------|
| Rules | `critical.paths: ["**"]` |
| Voters | `shadow_mode: false` |
| Hangar mockfs | `approval_list: ["*"]` |
| Intercept | adjudicate `mockfs` / `*` |
| Plugin | `ARBITER_GATE_ALL=1` |
| Permissions | bash default deny; subagents without edit/write/bash |

Do not hold `hangar_*` or Arbiter MCP tools (control-plane deadlock).

## Prerequisites

- venv with `arbiter[hangar]` + `mcp-hangar==2.6.0`
- OpenCode 1.18.16+
- GitHub Copilot login (`opencode auth list`) — demo voters use that token

## Manual checks

### A — L2 deny without covering allow

```bash
source /tmp/arbiter-live/env.sh
/tmp/arbiter-live/bin/smoke-a-l2.sh
```

### B — Hangar hold → enforce quorum

```bash
/tmp/arbiter-live/bin/smoke-b-hold.sh              # write_note
/tmp/arbiter-live/bin/smoke-b-hold.sh read_note
/tmp/arbiter-live/bin/smoke-b-hold.sh migrate.apply  # no trial → precondition_denied
```

Expect ledger: `hold.accepted` → `decision.opened` (`mode=enforce`) → votes →
`hold.adjudicated` matching quorum.

### C — Unlock L2 with a narrow scope

```bash
/tmp/arbiter-live/bin/smoke-allow-paths.sh 'auth/**' 'src/**'
arbiter check-coverage --json --tool edit --path auth/handler.py
```

### D — Eval report

```bash
arbiter report-eval --horizon-days 14 --format markdown
```

## Checklist

1. `decision.opened.mode` = **enforce**
2. Quorum deny ⇒ tool does not run
3. L2 without allow ⇒ deny on every critical path
4. Restart Hangar after changing voters / tokens

## Diagnostics

```bash
source /tmp/arbiter-live/env.sh
tail -n 30 "$ARBITER_DATA_DIR/ledger.jsonl" | jq -c \
  '{e:.event,id:.decision_id,mode:.mode,approved:.approved,reason:.reason}'
rg 'ArbiterApprovalDelivery|noop|resolve' /tmp/arbiter-live/hangar.log | tail
```

## See also

- [Podman demo](./podman.md)
- [Client layers](./client-layers.md)
