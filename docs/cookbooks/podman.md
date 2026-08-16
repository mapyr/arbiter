# Cookbook: Podman demo (Hangar + Arbiter)

Hangar and Arbiter as containers. Arbiter’s MCP is **discovered** via
Docker-compatible labels (works with Podman’s API) — not hard-coded in
`mcp_servers`.

## Architecture

```text
host OpenCode ──► hangar:18782/mcp
                      │
                      ├─ approval delivery (entry point `arbiter`)
                      │     └─ POST 127.0.0.1:8080/api/approvals/{id}/resolve
                      ├─ mockfs / mockhttp (approval_list: *)
                      └─ discovery (socket) ──► arbiter (labels) → :8765/
```

| Service | Role |
|---------|------|
| `hangar` | Gateway, holds, delivery, discovery (image = Hangar 2.6.0 + this package) |
| `arbiter` | HTTP MCP, labeled for discovery |

Stock Hangar images lack the `arbiter` delivery entry point — without it the
channel becomes `noop`.

## Quick start

Needs OpenCode with GitHub Copilot login (or voter keys in
`deploy/podman/.env`). Model override: `OPENCODE_MODEL` (default
`github-copilot/gpt-4o`).

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

`source env.sh` sets Hangar URL/key and `ARBITER_MCP_SERVER`. OpenCode config
lives under `/tmp/arbiter-podman/project` — restart the session after plugin
changes.

## Discovery labels (arbiter)

```yaml
mcp.hangar.enabled: "true"
mcp.hangar.name: "arbiter"
mcp.hangar.mode: "http"
mcp.hangar.port: "8765"
```

Arbiter must serve MCP at **`/`**, expose **`GET /health`**, publish **no** host
port, and may use `ARBITER_ALLOW_INSECURE_HTTP=1` on the private compose network.

## Ops

```bash
./deploy/podman/up.sh --logs
./deploy/podman/up.sh --down
```

Stale resolve token (401 on resolve) → recreate Hangar with the compose
`--force-recreate` path documented in `up.sh`. Discovery socket must be the
same socket Compose uses (usually `/run/podman/podman.sock`).

| Path | Description |
|------|-------------|
| `deploy/podman/compose.yaml` | services, networks, volumes, labels |
| `deploy/podman/config/*` | rules / voters / intercept / hangar |
| `deploy/podman/up.sh` | build, bootstrap, up, probe |

## Security (demo)

- Insecure HTTP only on the compose network
- Hangar auth on; tokens in `.env` / `/tmp/arbiter-podman/` (chmod 600)
- Do not publish the arbiter port publicly

## See also

- [Live smoke (host)](./live-smoke.md)
- [Hangar delivery](./hangar-delivery.md)
- [Client layers](./client-layers.md)
