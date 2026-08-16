#!/usr/bin/env bash
# Live full-stack playbook: OpenCode + L2 gate-all + Hangar hold-* +
# arbiter ENFORCE (full quorum gates; no shadow passthrough).
#
# Usage:
#   ./scripts/live-smoke-up.sh              # setup + start
#   ./scripts/live-smoke-up.sh --reset      # wipe LIVE dir, then setup + start
#   ./scripts/live-smoke-up.sh --no-start   # only write configs / env
#   ./scripts/live-smoke-up.sh --down       # stop listeners on LIVE ports
#
# Override:
#   LIVE=/tmp/arbiter-live REPO=… ARBITER_PORT=18781 HANGAR_PORT=18782 \
#   OPENCODE_MODEL=github-copilot/gpt-4o ./scripts/live-smoke-up.sh
#
# After success:
#   source "$LIVE/env.sh"
#   See docs/cookbooks/live-smoke.md for manual tests.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${REPO:-$SCRIPT_DIR/..}" && pwd)"
LIVE="${LIVE:-/tmp/arbiter-live}"
ARBITER_PORT="${ARBITER_PORT:-18781}"
HANGAR_PORT="${HANGAR_PORT:-18782}"
VENV="${VENV:-$REPO/.venv}"
OPENCODE_AUTH="${OPENCODE_AUTH:-$HOME/.local/share/opencode/auth.json}"
OPENCODE_MODEL="${OPENCODE_MODEL:-github-copilot/gpt-4o}"

DO_RESET=0
DO_START=1
DO_DOWN=0

for arg in "$@"; do
  case "$arg" in
    --reset) DO_RESET=1 ;;
    --no-start) DO_START=0 ;;
    --down) DO_DOWN=1 ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
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

shell_quote() {
  python3 -c 'import sys,shlex; print(shlex.quote(sys.argv[1]))' "$1"
}

kill_ports() {
  local p pid
  for p in "$ARBITER_PORT" "$HANGAR_PORT"; do
    pid="$(lsof -nP -iTCP:"$p" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    if [[ -n "${pid:-}" ]]; then
      log "stopping pid $pid on :$p"
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

if [[ "$DO_DOWN" -eq 1 ]]; then
  kill_ports
  log "down"
  exit 0
fi

[[ -d "$VENV" ]] || die "venv not found: $VENV (pip install '.[hangar,dev]' first)"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
command -v arbiter >/dev/null || die "arbiter not on PATH after activating venv"
command -v mcp-hangar >/dev/null || die "mcp-hangar missing — pip install 'arbiter[hangar]' / mcp-hangar==2.6.0"
[[ -f "$OPENCODE_AUTH" ]] || die "OpenCode auth missing: $OPENCODE_AUTH (run: opencode auth login)"

export OPENCODE_AUTH
COPILOT_ACCESS="$(
  python3 - <<'PY'
import json, os, sys
from pathlib import Path
p = Path(os.environ["OPENCODE_AUTH"])
data = json.loads(p.read_text())
block = data.get("github-copilot") or {}
access = block.get("access")
if not access:
    sys.stderr.write(
        "github-copilot.access missing in auth.json — "
        "run: opencode auth login (GitHub Copilot)\n"
    )
    sys.exit(1)
print(access)
PY
)"
[[ -n "$COPILOT_ACCESS" ]] || die "empty Copilot access token"

if [[ "$DO_RESET" -eq 1 ]]; then
  log "reset: removing $LIVE"
  kill_ports
  rm -rf "$LIVE"
fi

log "workspace $LIVE (repo=$REPO)"
mkdir -p \
  "$LIVE/decisions" \
  "$LIVE/hangar-data" \
  "$LIVE/hangar" \
  "$LIVE/project/.opencode/plugins" \
  "$LIVE/project/auth" \
  "$LIVE/project/src" \
  "$LIVE/bin"

# Deep demo: every workspace path is critical → L2/coverage fail-closed without
# a covering allow. Formulation barriers stay on (no universal scope stamps).
cat > "$LIVE/arbiter.rules.yaml" <<'YAML'
critical:
  paths:
    - "**"
default: critical

formulation:
  deny_universal_scope: true
  deny_filler_options: true

# Align with Podman demo / OpenCode plugin (get_gate_policy → ensure_plan).
client_gate:
  plan:
    mode: on_uncovered
    arbiter_mcp_server: arbiter
YAML

# Adjudicate every mockfs tool. Hangar management + arbiter MCP stay unlisted
# (holding hangar_call / open_decision would deadlock the control plane).
cat > "$LIVE/arbiter.intercept.yaml" <<'YAML'
hold:
  - mcp_server: "mockfs"
    tool: "*"
YAML

cat > "$LIVE/arbiter.voters.yaml" <<'YAML'
# OpenAI-compatible chat API (demo uses OpenCode GitHub Copilot oauth).
# Override models / base_url for your provider. Do not put API keys in YAML.
# ENFORCE: shadow_mode false — quorum verdict gates Hangar resolve.
voters:
  - id: voter-1
    base_url: https://api.githubcopilot.com
    model: gpt-4o
    api_key_env: ARBITER_VOTER_1_KEY
    temperature: 0
    max_tokens: 800
    timeout_seconds: 60
  - id: voter-2
    base_url: https://api.githubcopilot.com
    model: gpt-4o-mini
    api_key_env: ARBITER_VOTER_2_KEY
    temperature: 0
    max_tokens: 800
    timeout_seconds: 60
  - id: voter-3
    base_url: https://api.githubcopilot.com
    model: gpt-4o-mini
    api_key_env: ARBITER_VOTER_3_KEY
    temperature: 0
    max_tokens: 800
    timeout_seconds: 60

round_deadline_seconds: 180
reveal_round: true
shadow_mode: false
baseline_voter: voter-1
YAML

cp "$REPO/deploy/podman/mockfs_server.py" "$LIVE/mockfs_server.py"

SECRET_FILE="$LIVE/.http-secret"
if [[ ! -f "$SECRET_FILE" ]]; then
  openssl rand -hex 16 > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
fi
ARBITER_HTTP_SECRET="$(cat "$SECRET_FILE")"

PYTHON_BIN="$VENV/bin/python"
cat > "$LIVE/hangar/config.yaml" <<YAML
logging:
  level: INFO
  json_format: false

persistence:
  backend: sqlite
  sqlite:
    data_dir: ${LIVE}/hangar-data

auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key

tool_access:
  mode: egress

approvals:
  enabled: true
  channel: arbiter
  arbiter:
    data_dir: ${LIVE}/decisions
    intercept_rules_path: ${LIVE}/arbiter.intercept.yaml
    voters_path: ${LIVE}/arbiter.voters.yaml
    rules_path: ${LIVE}/arbiter.rules.yaml
    resolve_base_url: http://127.0.0.1:${HANGAR_PORT}/api
    resolve_token_env: ARBITER_HANGAR_RESOLVE_TOKEN
    principal_id_env: ARBITER_HANGAR_PRINCIPAL_ID
    hold_margin_seconds: 10
    min_round_seconds: 20

mcp_servers:
  mockfs:
    mode: subprocess
    command:
      - ${PYTHON_BIN}
      - ${LIVE}/mockfs_server.py
    idle_ttl_s: 300
    tools:
      # Hangar fnmatch — hold every mockfs tool before execution.
      approval_list:
        - "*"
      approval_timeout_seconds: 900
      approval_channel: arbiter

  arbiter:
    mode: remote
    endpoint: "http://127.0.0.1:${ARBITER_PORT}/mcp"
    description: "Arbiter decision gateway"
    auth:
      type: api_key
      api_key: ${ARBITER_HTTP_SECRET}
      api_key_header: X-Arbiter-Secret
    tools:
      # Control-plane tools must not enter the hold channel.
      deny_list:
        - resolve_decision
YAML

KEY_FILE="$LIVE/.hangar-api-key"
if [[ -f "$KEY_FILE" ]]; then
  HANGAR_API_KEY="$(cat "$KEY_FILE")"
  log "reusing Hangar API key from $KEY_FILE"
elif [[ -f "$LIVE/env.sh" ]] && rg -q 'ARBITER_HANGAR_RESOLVE_TOKEN=' "$LIVE/env.sh"; then
  # Recover key from a previous env.sh (hash is not recoverable from Hangar DB).
  # shellcheck disable=SC1091
  source "$LIVE/env.sh"
  HANGAR_API_KEY="${ARBITER_HANGAR_RESOLVE_TOKEN:-}"
  [[ -n "$HANGAR_API_KEY" ]] || die "env.sh has empty ARBITER_HANGAR_RESOLVE_TOKEN — use --reset"
  printf '%s\n' "$HANGAR_API_KEY" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  log "recovered Hangar API key from env.sh → $KEY_FILE"
else
  log "bootstrapping Hangar admin (service:arbiter)"
  BOOT_OUT="$(mktemp)"
  if ! mcp-hangar auth bootstrap-admin \
    --config "$LIVE/hangar/config.yaml" \
    --principal service:arbiter \
    --key-name "arbiter-live-smoke" \
    --show-key >"$BOOT_OUT" 2>&1; then
    if rg -q "already been bootstrapped" "$BOOT_OUT"; then
      cat "$BOOT_OUT" >&2
      rm -f "$BOOT_OUT"
      die "Hangar admin already bootstrapped but no key on disk — re-run with --reset"
    fi
    cat "$BOOT_OUT" >&2
    rm -f "$BOOT_OUT"
    die "bootstrap-admin failed (try --reset if store is half-initialized)"
  fi
  export BOOT_OUT
  HANGAR_API_KEY="$(
    python3 - <<'PY'
import os, re, sys
from pathlib import Path
text = Path(os.environ["BOOT_OUT"]).read_text()
m = re.search(r"api key\s*:\s*(mcp_\S+)", text)
if not m:
    sys.stderr.write(text + "\n")
    sys.exit(1)
print(m.group(1))
PY
  )" || {
    cat "$BOOT_OUT" >&2
    rm -f "$BOOT_OUT"
    die "could not parse bootstrap API key — re-run with --reset"
  }
  rm -f "$BOOT_OUT"
  unset BOOT_OUT
  printf '%s\n' "$HANGAR_API_KEY" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  log "saved Hangar API key → $KEY_FILE"
fi

{
  echo "# Generated by scripts/live-smoke-up.sh — do not commit"
  echo "export LIVE=$(shell_quote "$LIVE")"
  echo "export REPO=$(shell_quote "$REPO")"
  echo "export PATH=$(shell_quote "$VENV/bin"):\$PATH"
  echo "export ARBITER_BIN=$(shell_quote "$VENV/bin/arbiter")"
  echo "export ARBITER_DATA_DIR=\$LIVE/decisions"
  echo "export ARBITER_RULES_PATH=\$LIVE/arbiter.rules.yaml"
  echo "export ARBITER_VOTERS_PATH=\$LIVE/arbiter.voters.yaml"
  echo "unset ARBITER_SHADOW_MODE"
  echo "export ARBITER_GATE_ALL=1"
  echo "export ARBITER_HTTP_SECRET=$(shell_quote "$ARBITER_HTTP_SECRET")"
  echo "export ARBITER_HANGAR_PRINCIPAL_ID=service:arbiter"
  echo "export ARBITER_HANGAR_RESOLVE_TOKEN=$(shell_quote "$HANGAR_API_KEY")"
  echo "export HANGAR_API_KEY=\$ARBITER_HANGAR_RESOLVE_TOKEN"
  echo "export ARBITER_PORT=$ARBITER_PORT"
  echo "export HANGAR_PORT=$HANGAR_PORT"
  echo "export ARBITER_VOTER_1_KEY=$(shell_quote "$COPILOT_ACCESS")"
  echo "export ARBITER_VOTER_2_KEY=\$ARBITER_VOTER_1_KEY"
  echo "export ARBITER_VOTER_3_KEY=\$ARBITER_VOTER_1_KEY"
  echo "export MCP_CONFIG=\$LIVE/hangar/config.yaml"
} > "$LIVE/env.sh"
chmod 600 "$LIVE/env.sh"
log "wrote $LIVE/env.sh"

cp "$REPO/client/opencode/plugins/arbiter-gate.js" \
  "$LIVE/project/.opencode/plugins/arbiter-gate.js"

python3 - "$LIVE" "$HANGAR_API_KEY" "$HANGAR_PORT" "$OPENCODE_MODEL" <<'PY'
import json, sys
from pathlib import Path

live = Path(sys.argv[1])
key = sys.argv[2]
port = int(sys.argv[3])
model = sys.argv[4]
cfg = {
    "$schema": "https://opencode.ai/config.json",
    "model": model,
    "permission": {
        "*": "ask",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "bash": {
            "*": "deny",
            "git status*": "ask",
            "git diff*": "ask",
            "git log*": "ask",
            "rm *": "deny",
            "sudo *": "deny",
        },
        # Declarative ask; L2 (ARBITER_GATE_ALL) still fail-closes via coverage.
        "edit": {"*": "ask"},
        "write": {"*": "ask"},
        "external_directory": {"*": "deny"},
        "webfetch": "deny",
        "websearch": "deny",
        "task": {
            "*": "ask",
            "explore": "allow",
            "scout": "allow",
            "general": "ask",
        },
    },
    "agent": {
        "explore": {
            "mode": "subagent",
            "permission": {
                "edit": "deny",
                "bash": "deny",
                "write": "deny",
                "external_directory": {"*": "deny"},
                "task": "deny",
            },
        },
        "scout": {
            "mode": "subagent",
            "permission": {
                "edit": "deny",
                "bash": "deny",
                "write": "deny",
                "external_directory": {"*": "deny"},
                "task": "deny",
            },
        },
        "general": {
            "mode": "subagent",
            "permission": {
                "edit": "deny",
                "bash": "deny",
                "write": "deny",
                "external_directory": {"*": "deny"},
                "task": "deny",
            },
        },
    },
    "mcp": {
        "hangar": {
            "type": "remote",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {"X-API-Key": key},
        }
    },
}
(live / "project" / "opencode.json").write_text(
    json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
)
PY

printf 'def login(): pass\n' > "$LIVE/project/auth/handler.py"
printf 'hello\n' > "$LIVE/project/src/readme_probe.py"
printf '# live smoke project\n' > "$LIVE/project/README.md"

cat > "$LIVE/bin/smoke-a-l2.sh" <<'EOF'
#!/usr/bin/env bash
# Expect deny: every path is critical and no covering allow exists.
# check-coverage exits 2 on deny — that is success for this smoke.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/env.sh"

expect_deny() {
  local tool="$1" path="$2" out status=0 approved
  out="$(arbiter check-coverage --json --tool "$tool" --path "$path")" || status=$?
  echo "$out"
  approved="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approved"))')"
  if [[ "$approved" != "False" || "$status" -ne 2 ]]; then
    echo "EXPECTED deny (exit 2, approved=false) for: --tool $tool --path $path" >&2
    return 1
  fi
  return 0
}

fail=0
expect_deny edit auth/handler.py || fail=1
expect_deny bash 'bash:echo hi' || fail=1
expect_deny write src/readme_probe.py || fail=1
exit "$fail"
EOF
chmod +x "$LIVE/bin/smoke-a-l2.sh"

cat > "$LIVE/bin/smoke-b-hold.sh" <<'EOF'
#!/usr/bin/env bash
# Hangar hold → enforce quorum. Tool succeeds only if quorum allows.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/env.sh"
TOOL="${1:-write_note}"
exec python - "$TOOL" <<'PY'
import asyncio, os, sys, httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

tool = sys.argv[1]
URL = f"http://127.0.0.1:{os.environ['HANGAR_PORT']}/mcp"
KEY = os.environ["HANGAR_API_KEY"]

ARGS = {
    "write_note": {"path": "notes/manual.txt", "content": "smoke-b"},
    "append_note": {"path": "notes/manual.txt", "content": "\nmore"},
    "read_note": {"path": "notes/manual.txt"},
    "delete_note": {"path": "notes/manual.txt"},
    "rename_note": {"src": "notes/manual.txt", "dst": "notes/renamed.txt"},
    "migrate.dry_run": {"migration": "001_init"},
    "migrate.apply": {"migration": "001_init"},
    "contract_test": {"path": "notes/manual.txt"},
}
if tool not in ARGS:
    raise SystemExit(f"unknown tool {tool!r}; choose from {sorted(ARGS)}")

async def main() -> None:
    async with httpx2.AsyncClient(
        headers={"X-API-Key": KEY},
        timeout=httpx2.Timeout(600.0, connect=30.0),
    ) as http:
        async with Client(streamable_http_client(URL, http_client=http)) as c:
            await c.call_tool("hangar_start", {"mcp_server": "mockfs"})
            r = await c.call_tool(
                "hangar_call",
                {
                    "calls": [
                        {
                            "mcp_server": "mockfs",
                            "tool": tool,
                            "arguments": ARGS[tool],
                        }
                    ]
                },
            )
            print(r.content[0].text)

asyncio.run(main())
PY
EOF
chmod +x "$LIVE/bin/smoke-b-hold.sh"

cat > "$LIVE/bin/smoke-allow-paths.sh" <<'EOF'
#!/usr/bin/env bash
# Open + model-quorum + resolve an allow decision with narrow scope (L2 unlock).
# Usage: smoke-allow-paths.sh auth/** src/**
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/env.sh"
[[ "$#" -ge 1 ]] || {
  echo "usage: $0 <scope-glob> [more-globs…]" >&2
  exit 2
}
exec python - "$@" <<'PY'
import asyncio, json, os, sys, httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

scopes = list(sys.argv[1:])
URL = f"http://127.0.0.1:{os.environ['ARBITER_PORT']}/mcp"
SECRET = os.environ["ARBITER_HTTP_SECRET"]


def payload(result) -> dict:
    if getattr(result, "is_error", False):
        raise RuntimeError(getattr(result, "content", result))
    if getattr(result, "structured_content", None) is not None:
        return dict(result.structured_content)
    return json.loads(result.content[0].text)


async def main() -> None:
    sample_path = scopes[0].replace("/**", "/x.py").replace("**", "x.py")
    async with httpx2.AsyncClient(
        headers={"X-Arbiter-Secret": SECRET},
        timeout=httpx2.Timeout(600.0, connect=30.0),
    ) as http:
        async with Client(streamable_http_client(URL, http_client=http)) as c:
            body = payload(
                await c.call_tool(
                    "open_decision",
                    {
                        "question": (
                            "Allow mutating the listed workspace scopes after review?"
                        ),
                        "options": ["allow", "deny"],
                        "voters": ["voter-1", "voter-2", "voter-3"],
                        "evidence": {"paths": scopes},
                        "criticality": "critical",
                        "ttl_seconds": 1800,
                        "scope": scopes,
                    },
                )
            )
            did = body["decision_id"]
            print("opened", did, "scope", scopes)
            print(payload(await c.call_tool("run_model_quorum", {"decision_id": did})))
            print(payload(await c.call_tool("resolve_decision", {"decision_id": did})))
            print(
                "coverage_sample",
                payload(
                    await c.call_tool(
                        "check_coverage",
                        {"paths": [sample_path], "tool": "edit"},
                    )
                ),
            )

asyncio.run(main())
PY
EOF
chmod +x "$LIVE/bin/smoke-allow-paths.sh"

cat > "$LIVE/bin/verify-commit-staged.sh" <<'EOF'
#!/usr/bin/env bash
# Layer-3 backstop helper (run from git repo with staged changes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/env.sh"
MSG_FILE="$(mktemp)"
trap 'rm -f "$MSG_FILE"' EXIT
git -C "${1:-$ROOT/project}" log -1 --pretty=%B 2>/dev/null >"$MSG_FILE" || true
if [[ ! -s "$MSG_FILE" ]]; then
  printf 'demo commit\n\nArbiter-Decision: missing\n' >"$MSG_FILE"
fi
arbiter verify-commit --paths-from staged --message-file "$MSG_FILE" --json || true
EOF
chmod +x "$LIVE/bin/verify-commit-staged.sh"

log "probing Copilot chat/completions…"
export COPILOT_ACCESS
python3 - <<'PY'
import os, sys, httpx
token = os.environ["COPILOT_ACCESS"]
r = httpx.post(
    "https://api.githubcopilot.com/chat/completions",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 8,
    },
    timeout=45,
)
print("copilot_status", r.status_code)
if r.status_code >= 400:
    print(r.text[:300], file=sys.stderr)
    sys.exit(1)
PY

if [[ "$DO_START" -eq 0 ]]; then
  log "setup complete (--no-start). Next:"
  echo "  source $LIVE/env.sh"
  echo "  # start arbiter + hangar (or re-run without --no-start)"
  exit 0
fi

kill_ports
# shellcheck disable=SC1091
source "$LIVE/env.sh"

log "starting arbiter on :$ARBITER_PORT"
nohup arbiter serve --transport http --host 127.0.0.1 --port "$ARBITER_PORT" \
  >"$LIVE/arbiter.log" 2>&1 &
echo $! >"$LIVE/arbiter.pid"

log "starting hangar on :$HANGAR_PORT"
nohup mcp-hangar --config "$LIVE/hangar/config.yaml" serve --http --host 127.0.0.1 --port "$HANGAR_PORT" \
  >"$LIVE/hangar.log" 2>&1 &
echo $! >"$LIVE/hangar.pid"

ok=0
for _ in $(seq 1 40); do
  if lsof -nP -iTCP:"$ARBITER_PORT" -sTCP:LISTEN >/dev/null 2>&1 \
    && lsof -nP -iTCP:"$HANGAR_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 0.25
done
[[ "$ok" -eq 1 ]] || {
  echo "--- arbiter.log ---" >&2
  tail -40 "$LIVE/arbiter.log" >&2 || true
  echo "--- hangar.log ---" >&2
  tail -40 "$LIVE/hangar.log" >&2 || true
  die "servers did not start listening"
}

if ! rg -q "ArbiterApprovalDelivery" "$LIVE/hangar.log"; then
  echo "--- hangar.log (delivery) ---" >&2
  rg -n "delivery|noop|ERROR|SystemExit" "$LIVE/hangar.log" | tail -20 >&2 || true
  die "Hangar did not load ArbiterApprovalDelivery"
fi

code="$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "X-API-Key: $HANGAR_API_KEY" \
  "http://127.0.0.1:${HANGAR_PORT}/health/live" || true)"
[[ "$code" == "200" ]] || die "Hangar health/live returned HTTP $code"

cat <<EOF

────────────────────────────────────────────────────────────
LIVE SMOKE READY (ENFORCE / deep gate)

  workspace : $LIVE
  mode      : enforce (shadow off) + ARBITER_GATE_ALL=1
  critical  : every path (**)
  hangar    : mockfs approval_list "*"
  arbiter   : http://127.0.0.1:${ARBITER_PORT}/mcp
  hangar    : http://127.0.0.1:${HANGAR_PORT}/mcp
  ledger    : $LIVE/decisions/ledger.jsonl

  source $LIVE/env.sh

Manual checks:
  # A — L2 deny without covering allow (any path / bash)
  $LIVE/bin/smoke-a-l2.sh

  # B — Hangar hold → enforce quorum (may DENY the tool)
  $LIVE/bin/smoke-b-hold.sh              # write_note
  $LIVE/bin/smoke-b-hold.sh read_note    # reads also held
  $LIVE/bin/smoke-b-hold.sh migrate.apply  # precondition deny without dry_run
  rg 'mode|baseline|hold.adjudicated|decision.resolved' \$ARBITER_DATA_DIR/ledger.jsonl | tail

  # D — unlock L2 with narrow scoped allow (formulation blocks "**/*")
  $LIVE/bin/smoke-allow-paths.sh 'auth/**' 'src/**'

  # C — report
  arbiter report-eval --horizon-days 14

Stop:
  $REPO/scripts/live-smoke-up.sh --down

Docs: $REPO/docs/cookbooks/live-smoke.md
────────────────────────────────────────────────────────────
EOF
