#!/usr/bin/env bash
# Build and start Hangar + Arbiter under Podman (discovery-based arbiter MCP).
#
# Usage:
#   ./deploy/podman/up.sh              # build + up
#   ./deploy/podman/up.sh --reset      # wipe volumes, re-bootstrap, up
#   ./deploy/podman/up.sh --down       # stop stack
#   ./deploy/podman/up.sh --logs       # follow logs
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(podman compose -f "$DEPLOY/compose.yaml" --project-directory "$DEPLOY")
OPENCODE_AUTH="${OPENCODE_AUTH:-$HOME/.local/share/opencode/auth.json}"
OPENCODE_MODEL="${OPENCODE_MODEL:-github-copilot/gpt-4o}"
ENV_FILE="$DEPLOY/.env"
STATE_DIR="${ARBITER_PODMAN_STATE:-/tmp/arbiter-podman}"
HANGAR_HOST_PORT="${HANGAR_HOST_PORT:-18782}"
VOLUME_HANGAR_DATA="${VOLUME_HANGAR_DATA:-arbiter_hangar-data}"

DO_RESET=0
DO_DOWN=0
DO_LOGS=0

for arg in "$@"; do
  case "$arg" in
    --reset) DO_RESET=1 ;;
    --down) DO_DOWN=1 ;;
    --logs) DO_LOGS=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# docker-compose-via-podman prints a banner on stdout; never trust `compose ps -q`.
hangar_container_id() {
  podman ps -q --filter "name=^arbiter-hangar-1$" 2>/dev/null | head -1
}

# podman logs → stderr; rg -q closes the pipe early → SIGPIPE under pipefail.
hangar_logs_match() {
  local cid="$1" pat="$2"
  set +o pipefail
  podman logs "$cid" 2>&1 | rg -q "$pat"
  local st=$?
  set -o pipefail
  return "$st"
}

# Discovery persists endpoint IPs in hangar-data. After compose recreate the
# arbiter container gets a new IP → hangar_call fails with "No route to host"
# until we rewrite mcp_server_configs (and bounce Hangar to drop in-memory cache).
sync_arbiter_endpoint() {
  local arb_cid arb_ip hangar_cid
  arb_cid="$(podman ps -q --filter "name=^arbiter-arbiter-1$" 2>/dev/null | head -1)"
  hangar_cid="$(hangar_container_id)"
  [[ -n "$arb_cid" && -n "$hangar_cid" ]] || return 0
  arb_ip="$(podman inspect "$arb_cid" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
  [[ -n "$arb_ip" ]] || return 0
  podman exec "$hangar_cid" python3 -c "
import json, sqlite3, sys
ip = sys.argv[1]
con = sqlite3.connect('/app/data/mcp_hangar.db')
row = con.execute(
    \"SELECT config_json FROM mcp_server_configs WHERE mcp_server_id='arbiter'\"
).fetchone()
if not row:
    sys.exit(0)
cfg = json.loads(row[0])
cfg['endpoint'] = f'http://{ip}:8765'
cfg['runtime_addresses'] = [ip]
con.execute(
    '''UPDATE mcp_server_configs
       SET config_json=?, enabled=1, consecutive_failures=0
       WHERE mcp_server_id=\"arbiter\"''',
    (json.dumps(cfg),),
)
con.commit()
print(cfg['endpoint'])
" "$arb_ip" >/dev/null
  podman restart "$hangar_cid" >/dev/null
  sleep 3
  log "synced Hangar arbiter endpoint → http://${arb_ip}:8765"
}

command -v podman >/dev/null || die "podman not on PATH"
podman info >/dev/null 2>&1 || die "podman not usable (is the machine running?)"

if [[ "$DO_DOWN" -eq 1 ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    "${COMPOSE[@]}" --env-file "$ENV_FILE" down || true
  else
    "${COMPOSE[@]}" down || true
  fi
  log "down"
  exit 0
fi

if [[ "$DO_LOGS" -eq 1 ]]; then
  [[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE — run up first"
  "${COMPOSE[@]}" --env-file "$ENV_FILE" logs -f
  exit 0
fi

[[ -f "$OPENCODE_AUTH" ]] || die "OpenCode auth missing: $OPENCODE_AUTH"

export OPENCODE_AUTH
COPILOT_ACCESS="$(
  python3 - <<'PY'
import json, os, sys
from pathlib import Path
data = json.loads(Path(os.environ["OPENCODE_AUTH"]).read_text())
access = (data.get("github-copilot") or {}).get("access")
if not access:
    sys.stderr.write("github-copilot.access missing — run: opencode auth login\n")
    sys.exit(1)
print(access)
PY
)"

mkdir -p "$STATE_DIR"
KEY_FILE="$STATE_DIR/hangar-api-key"

if [[ "$DO_RESET" -eq 1 ]]; then
  log "reset: compose down -v + wipe Hangar auth volume"
  # compose.yaml mounts ${PODMAN_SOCKET}; a wiped/partial .env makes down fail
  # before volumes are removed. Provide a harmless default for teardown only.
  export PODMAN_SOCKET="${PODMAN_SOCKET:-/run/podman/podman.sock}"
  if [[ -f "$ENV_FILE" ]]; then
    "${COMPOSE[@]}" --env-file "$ENV_FILE" down -v || true
  else
    PODMAN_SOCKET="$PODMAN_SOCKET" "${COMPOSE[@]}" down -v || true
  fi
  # hangar-data is external:true — compose down -v never deletes it. Bootstrap
  # also creates it via `podman volume create` before first up. Wipe explicitly
  # or the next bootstrap hits "already bootstrapped" with no key on disk.
  podman volume rm -f "$VOLUME_HANGAR_DATA" >/dev/null 2>&1 || true
  podman volume rm -f arbiter_decisions >/dev/null 2>&1 || true
  rm -f "$KEY_FILE" "$ENV_FILE" "$STATE_DIR/env.sh"
fi

log "building images (arbiter + hangar-with-arbiter-delivery)"
podman build -t localhost/arbiter:0.6.0 -f "$DEPLOY/Containerfile.arbiter" "$ROOT"
podman build -t localhost/arbiter-hangar:2.6.0 -f "$DEPLOY/Containerfile.hangar" "$ROOT"

parse_bootstrap_key() {
  local text_file="$1"
  python3 - <<'PY' "$text_file"
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
m = re.search(r"api key\s*:\s*(mcp_\S+)", text)
if not m:
    sys.stderr.write(text + "\n")
    sys.exit(1)
print(m.group(1))
PY
}

if [[ -f "$KEY_FILE" ]]; then
  HANGAR_API_KEY="$(cat "$KEY_FILE")"
  log "reusing Hangar API key from $KEY_FILE"
else
  log "bootstrapping Hangar admin into volume ${VOLUME_HANGAR_DATA}"
  podman volume create "$VOLUME_HANGAR_DATA" >/dev/null 2>&1 || true
  BOOT_OUT="$(mktemp)"
  if ! podman run --rm \
    -v "${VOLUME_HANGAR_DATA}:/app/data" \
    -v "$DEPLOY/config/hangar.config.yaml:/app/config.yaml:ro" \
    -v "$DEPLOY/config:/config:ro" \
    -e MCP_CONFIG=/app/config.yaml \
    localhost/arbiter-hangar:2.6.0 \
    auth bootstrap-admin \
      --config /app/config.yaml \
      --principal service:arbiter \
      --key-name "arbiter-podman" \
      --show-key >"$BOOT_OUT" 2>&1; then
    if rg -q "already been bootstrapped" "$BOOT_OUT"; then
      cat "$BOOT_OUT" >&2
      rm -f "$BOOT_OUT"
      die "Hangar already bootstrapped but no key on disk — re-run with --reset"
    fi
    cat "$BOOT_OUT" >&2
    rm -f "$BOOT_OUT"
    die "bootstrap-admin failed"
  fi
  HANGAR_API_KEY="$(parse_bootstrap_key "$BOOT_OUT")" || {
    cat "$BOOT_OUT" >&2
    rm -f "$BOOT_OUT"
    die "could not parse bootstrap API key"
  }
  rm -f "$BOOT_OUT"
  printf '%s\n' "$HANGAR_API_KEY" >"$KEY_FILE"
  chmod 600 "$KEY_FILE"
  log "saved Hangar API key → $KEY_FILE"
fi

if [[ -z "${PODMAN_SOCKET:-}" ]]; then
  # podman compose (docker-compose provider) creates containers on the *rootful*
  # engine socket inside the machine. Discovery must use the same socket.
  # Hangar runs privileged so the mount is readable from the container.
  if podman machine ssh -- 'test -S /run/podman/podman.sock' 2>/dev/null; then
    PODMAN_SOCKET="/run/podman/podman.sock"
  else
    PODMAN_SOCKET="/var/run/docker.sock"
  fi
fi
log "PODMAN_SOCKET=$PODMAN_SOCKET (must match compose engine socket)"

cat >"$ENV_FILE" <<EOF
HANGAR_HOST_PORT=${HANGAR_HOST_PORT}
PODMAN_SOCKET=${PODMAN_SOCKET}
ARBITER_HANGAR_RESOLVE_TOKEN=${HANGAR_API_KEY}
ARBITER_VOTER_1_KEY=${COPILOT_ACCESS}
ARBITER_VOTER_2_KEY=${COPILOT_ACCESS}
ARBITER_VOTER_3_KEY=${COPILOT_ACCESS}
EOF
chmod 600 "$ENV_FILE"
log "wrote $ENV_FILE"

# Host live-smoke binds 127.0.0.1:HANGAR_HOST_PORT and steals curls from Podman
# (wrong API key → rate_limit). Refuse to start while that process is alive.
if lsof -nP -iTCP:"${HANGAR_HOST_PORT}" -sTCP:LISTEN 2>/dev/null \
  | rg -q 'python|mcp-hangar'; then
  die "port ${HANGAR_HOST_PORT} already taken by a host process (often /tmp/arbiter-live Hangar). Kill it first, e.g.:
  pkill -f '/tmp/arbiter-live/hangar/config.yaml' || true
  # or: kill \$(cat /tmp/arbiter-live/hangar.pid)"
fi

# Force recreate so Hangar picks up a fresh ARBITER_HANGAR_RESOLVE_TOKEN.
# Plain `up -d` leaves a running container with a stale env → voters finish,
# then POST /approvals/{id}/resolve returns 401 and the hold hangs until the
# OpenCode hangar_call timeout.
log "compose up (force-recreate)"
"${COMPOSE[@]}" --env-file "$ENV_FILE" up -d --build --force-recreate --remove-orphans

log "waiting for Hangar health…"
ok=0
for _ in $(seq 1 60); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "X-API-Key: ${HANGAR_API_KEY}" \
    "http://127.0.0.1:${HANGAR_HOST_PORT}/health/live" 2>/dev/null || true)"
  if [[ "$code" == "200" ]]; then
    ok=1
    break
  fi
  sleep 1
done
[[ "$ok" -eq 1 ]] || {
  "${COMPOSE[@]}" --env-file "$ENV_FILE" logs --tail=80 >&2 || true
  die "Hangar /health/live not ready"
}

log "waiting for discovery to register arbiter…"
registered=0
hangar_cid=""
for _ in $(seq 1 45); do
  hangar_cid="$(hangar_container_id)"
  if [[ -n "$hangar_cid" ]] && hangar_logs_match "$hangar_cid" \
    "discovery_registered_mcp_server|fleet_restored.*arbiter|mcp_servers.: .\\[.*arbiter|conflicts with static config"; then
    # "conflicts with static config" = prior discovery persisted arbiter into Hangar DB.
    registered=1
    break
  fi
  body="$(curl -sS -H "X-API-Key: ${HANGAR_API_KEY}" \
    "http://127.0.0.1:${HANGAR_HOST_PORT}/api/mcp_servers" 2>/dev/null || true)"
  if printf '%s' "$body" | rg -q 'arbiter' \
    && ! printf '%s' "$body" | rg -q 'authentication_failed|rate_limit'; then
    registered=1
    break
  fi
  sleep 2
done

if [[ "$registered" -eq 1 ]]; then
  sync_arbiter_endpoint
  # health after Hangar bounce
  for _ in $(seq 1 30); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' \
      -H "X-API-Key: ${HANGAR_API_KEY}" \
      "http://127.0.0.1:${HANGAR_HOST_PORT}/health/live" 2>/dev/null || true)"
    [[ "$code" == "200" ]] && break
    sleep 1
  done
fi

delivery_ok=0
hangar_cid="$(hangar_container_id)"
if [[ -n "$hangar_cid" ]] && hangar_logs_match "$hangar_cid" "ArbiterApprovalDelivery"; then
  delivery_ok=1
fi

# OpenCode project (MCP + L2 plugin) — `source env.sh && opencode` from repo
# root is NOT enough; OpenCode reads config from the project directory.
PROJECT_DIR="$STATE_DIR/project"
mkdir -p "$PROJECT_DIR/.opencode/plugins" "$PROJECT_DIR/src" "$PROJECT_DIR/auth" \
  "$STATE_DIR/decisions-l2"
cp "$ROOT/client/opencode/plugins/arbiter-gate.js" \
  "$PROJECT_DIR/.opencode/plugins/arbiter-gate.js"
python3 - "$PROJECT_DIR" "$HANGAR_API_KEY" "$HANGAR_HOST_PORT" "$OPENCODE_MODEL" <<'PY'
import json, sys
from pathlib import Path

proj, key, port, model = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
cfg = {
    "$schema": "https://opencode.ai/config.json",
    "model": model,
    "permission": {
        "*": "ask",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        # bash passes; local curl/wget are denied so HTTP goes through
        # Hangar mockhttp/curl → three voters (not silent L1 deny of all bash).
        "bash": {
            "*": "allow",
            "curl *": "deny",
            "curl": "deny",
            "*curl*": "deny",
            "wget *": "deny",
            "*wget*": "deny",
            "rm *": "deny",
            "sudo *": "deny",
        },
        "edit": {"*": "ask"},
        "write": {"*": "ask"},
        "external_directory": {"*": "deny"},
        "webfetch": "deny",
        "websearch": "deny",
        # MCP Hangar tools (hangar_hangar_call / list / …) — must be allow so
        # mockhttp/curl can reach voters; do not leave them under "*": ask only.
        "hangar*": "allow",
        "hangar_*": "allow",
        "task": {
            "*": "ask",
            "explore": "allow",
            "scout": "allow",
            "general": "ask",
        },
    },
    "mcp": {
        "hangar": {
            "type": "remote",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {"X-API-Key": key},
            "enabled": True,
        }
    },
}
(proj / "opencode.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
(proj / "README.md").write_text(
    "# Arbiter Podman demo\n\n"
    "- Mutating tools need a voted **plan** first (plugin → Hangar → arbiter "
    "`ensure_plan`), then `check_coverage`.\n"
    "- `bash` is allowed (except local `curl`/`wget`).\n"
    "- HTTP: call Hangar tool `mockhttp/curl` → hold → **three voters**.\n"
    "- Files: `mockfs/*` also held → voters.\n",
    encoding="utf-8",
)
(proj / "AGENTS.md").write_text(
    "# Arbiter gate\n\n"
    "Before `edit`/`write`/… the OpenCode plugin calls Hangar → arbiter.\n"
    "If you see `ARBITER_PLAN_REQUIRED` (OpenCode may label it \"Patch failed\"), call "
    "the Hangar MCP tool (often named `hangar_hangar_call` because OpenCode prefixes "
    "the server key onto Hangar's `hangar_call`):\n\n"
    "```\n"
    "hangar_call → mcp_server=arbiter, tool=ensure_plan\n"
    "plan: {\n"
    '  "goal": "…",\n'
    '  "steps": [{"action": "…", "paths": ["auth/handler.py"]}],\n'
    '  "scope": ["auth/**"]\n'
    "}\n"
    "```\n\n"
    "Use project-relative scope (e.g. `auth/**`), not universal `**/*` "
    "(formulation refuses that).\n"
    "After `approved: true`, retry the original tool.\n"
    "HTTP/files via mockhttp/mockfs still go through Hangar hold + voters.\n",
    encoding="utf-8",
)
(proj / "src" / "readme_probe.py").write_text("print('hello')\n", encoding="utf-8")
(proj / "auth" / "handler.py").write_text("def login(): pass\n", encoding="utf-8")
PY

cat >"$STATE_DIR/env.sh" <<EOF
# Generated by deploy/podman/up.sh — host-side helpers
export ARBITER_PODMAN_STATE=$(printf '%q' "$STATE_DIR")
export HANGAR_HOST_PORT=${HANGAR_HOST_PORT}
export HANGAR_URL=http://127.0.0.1:${HANGAR_HOST_PORT}
export HANGAR_API_KEY=$(printf '%q' "$HANGAR_API_KEY")
export ARBITER_HANGAR_RESOLVE_TOKEN=\$HANGAR_API_KEY
export ARBITER_HANGAR_PRINCIPAL_ID=service:arbiter
export ARBITER_VOTER_1_KEY=$(printf '%q' "$COPILOT_ACCESS")
export ARBITER_VOTER_2_KEY=\$ARBITER_VOTER_1_KEY
export ARBITER_VOTER_3_KEY=\$ARBITER_VOTER_1_KEY
export OPENCODE_PROJECT=$(printf '%q' "$PROJECT_DIR")
# L2 plugin (arbiter-gate) — Hangar → injected arbiter MCP (plan + coverage)
export PATH=$(printf '%q' "$ROOT/.venv/bin"):\$PATH
export ARBITER_BIN=$(printf '%q' "$ROOT/.venv/bin/arbiter")
export ARBITER_MCP_SERVER=arbiter
export ARBITER_DATA_DIR=$(printf '%q' "$STATE_DIR/decisions-l2")
export ARBITER_RULES_PATH=$(printf '%q' "$ROOT/deploy/podman/config/arbiter.rules.yaml")
export ARBITER_VOTERS_PATH=$(printf '%q' "$ROOT/deploy/podman/config/arbiter.voters.yaml")
export ARBITER_GATE_ALL=1
export ARBITER_ALLOW_BASH=1
EOF
chmod 600 "$STATE_DIR/env.sh"

cat <<EOF

────────────────────────────────────────────────────────────
PODMAN STACK READY

  hangar   : http://127.0.0.1:${HANGAR_HOST_PORT}/mcp
  arbiter  : discovered via labels (not published to host)
  delivery : $([[ "$delivery_ok" -eq 1 ]] && echo ArbiterApprovalDelivery || echo CHECK LOGS)
  discover : $([[ "$registered" -eq 1 ]] && echo arbiter registered || echo PENDING — see docs)
  opencode : $PROJECT_DIR

  source $STATE_DIR/env.sh
  cd \$OPENCODE_PROJECT && opencode
  # plan gate: ARBITER_PLAN_REQUIRED → hangar_call arbiter/ensure_plan
  # bash OK; local curl denied → hangar_call mockhttp/curl (voters)
  # (MCP/plugins live in the project dir — not in the arbiter git root)

Useful:
  podman compose -f $DEPLOY/compose.yaml --env-file $ENV_FILE ps
  curl -sS -L -H "X-API-Key: \$HANGAR_API_KEY" \$HANGAR_URL/api/mcp_servers | jq .
  $DEPLOY/up.sh --logs
  $DEPLOY/up.sh --down

Docs: $ROOT/docs/cookbooks/podman.md
────────────────────────────────────────────────────────────
EOF

[[ "$delivery_ok" -eq 1 ]] || die "Hangar did not load ArbiterApprovalDelivery — check image build / entry point"
if [[ "$registered" -ne 1 ]]; then
  log "WARN: arbiter not registered yet — check PODMAN_SOCKET (/run/podman/podman.sock), /health, quarantine in hangar logs"
  if [[ -n "$hangar_cid" ]]; then
    podman logs "$hangar_cid" 2>&1 | rg -i 'quarantine|Health check|discovery_registered' | tail -20 || true
  fi
  exit 1
fi
