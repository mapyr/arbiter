# Cookbook: Podman demo (Hangar + Arbiter)

Hangar and Arbiter as containers. Arbiter’s MCP is a **static Hangar remote**
(`http://arbiter:8765/` + `X-Arbiter-Secret`). Home-lab contract:
[`lab.md`](./lab.md).

## Architecture

```text
host OpenCode ──► hangar:18782/mcp
                      │
                      ├─ approval delivery (entry point `arbiter`)
                      │     └─ POST 127.0.0.1:8080/api/approvals/{id}/resolve
                      ├─ mockfs / mockhttp (approval_list: *)
                      └─ static remote ──► arbiter:8765/  (X-Arbiter-Secret)
```

| Service | Role |
|---------|------|
| `hangar` | Gateway, holds, delivery (image = Hangar 2.6.0 + this package) |
| `arbiter` | HTTP MCP on the compose network (port not published) |

Stock Hangar images lack the `arbiter` delivery entry point — without it the
channel becomes `noop`. Lab `create_delivery` probes the ledger and **exits**
if that write fails.

## Quick start

Needs OpenCode with GitHub Copilot login (or voter keys in
`deploy/podman/.env`). Model override: `OPENCODE_MODEL` (default
`github-copilot/gpt-5-mini`). After upgrading onto the static-remote stack, run
`--reset` once (old Hangar DB may still hold a discovered arbiter).

```bash
cd /path/to/arbiter
./deploy/podman/up.sh --reset
source /tmp/arbiter-podman/env.sh

curl -sS -H "X-API-Key: $HANGAR_API_KEY" "$HANGAR_URL/api/mcp_servers" | jq .

cd "$OPENCODE_PROJECT"   # /tmp/arbiter-podman/project
opencode
```

### Expected behaviour

- `bash` (echo / git) — allowed (`ARBITER_ALLOW_BASH=1`)
- `edit` / `write` — plugin → Hangar → `get_gate_policy` + `check_coverage`;
  uncovered → `ARBITER_PLAN_REQUIRED` → `ensure_plan` with a **narrow** scope
- local `curl` — denied at L1; HTTP via `mockhttp/curl` → hold → voters
- files — `mockfs/*` → hold → quorum
- Give `hangar_call` ≥90s (hold + voter roster)
- New labs start **shadow**; flip `shadow_mode` when `report-eval` looks sane
  ([`lab.md`](./lab.md))

`source env.sh` sets Hangar URL/key, `ARBITER_DATA_DIR` (host ledger bind), and
`ARBITER_MCP_SERVER`. OpenCode config lives under `/tmp/arbiter-podman/project`.
On macOS Podman, `up.sh` realpath's that dir to `/private/tmp/…` so the ledger
bind is writable (VM `/tmp` is a separate tmpfs).

## Ops

```bash
./deploy/podman/up.sh --logs
./deploy/podman/up.sh --down
arbiter report-eval --horizon-days 14
```

Stale resolve token (401 on resolve) → `up.sh` already `--force-recreate`s.

| Path | Description |
|------|-------------|
| `deploy/podman/compose.yaml` | services, network, host ledger bind |
| `deploy/podman/config/*` | rules / voters / intercept / hangar |
| `deploy/podman/up.sh` | build, bootstrap, up, probe |

## Security (lab)

- `ARBITER_HTTP_SECRET` on the private compose network; `/health` has no secret
- Hangar auth on; tokens in `.env` / `/tmp/arbiter-podman/` (chmod 600)
- Do not publish the arbiter port
- Ledger: flock + fsync; same directory on the host and in both containers

## See also

- [Home-lab slice](./lab.md)
- [Live smoke (host)](./live-smoke.md)
- [Hangar delivery](./hangar-delivery.md)
- [Client layers](./client-layers.md)
