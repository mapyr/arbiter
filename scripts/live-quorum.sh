#!/usr/bin/env bash
# One live ensure-plan against the three independent OpenAI-compatible
# voters in arbiter.voters.yaml.example. CI never runs this.
#
# Usage:
#   export ARBITER_VOTER_1_KEY=…   # OpenAI
#   export ARBITER_VOTER_2_KEY=…   # OpenRouter
#   export ARBITER_VOTER_3_KEY=…   # Gemini
#   ./scripts/live-quorum.sh
#
# Override: LIVE=/tmp/arbiter-live-quorum VENV=…/arbiter/.venv
#
# Cookbook: docs/cookbooks/live-quorum.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${REPO:-$SCRIPT_DIR/..}" && pwd)"
LIVE="${LIVE:-/tmp/arbiter-live-quorum}"
VENV="${VENV:-$REPO/.venv}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

# Fail closed before any HTTP.
for k in ARBITER_VOTER_1_KEY ARBITER_VOTER_2_KEY ARBITER_VOTER_3_KEY; do
  if [[ -z "${!k:-}" ]]; then
    die "missing $k — refusing to call providers"
  fi
done

[[ -d "$VENV" ]] || die "venv not found: $VENV (pip install '.[dev]' first)"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
command -v arbiter >/dev/null || die "arbiter not on PATH after activating venv"

rm -rf "$LIVE"
mkdir -p "$LIVE/decisions"
cp "$REPO/arbiter.voters.yaml.example" "$LIVE/arbiter.voters.yaml"
cp "$REPO/arbiter.rules.yaml.example" "$LIVE/arbiter.rules.yaml"

cat > "$LIVE/plan.json" <<'JSON'
{
  "goal": "Update auth handler login path",
  "steps": [
    {"action": "edit auth handler", "paths": ["auth/handler.py"]}
  ],
  "scope": ["auth/**"]
}
JSON

export ARBITER_DATA_DIR="$LIVE/decisions"
export ARBITER_VOTERS_PATH="$LIVE/arbiter.voters.yaml"
export ARBITER_RULES_PATH="$LIVE/arbiter.rules.yaml"

log "ensure-plan (OpenAI + OpenRouter + Gemini)"
set +e
arbiter ensure-plan --plan-file "$LIVE/plan.json" --json | tee "$LIVE/ensure-plan.json"
plan_rc=${PIPESTATUS[0]}
set -e

python3 - "$ARBITER_DATA_DIR" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
ledger = root / "ledger.jsonl"
if not ledger.is_file():
    sys.stderr.write(f"missing ledger: {ledger}\n")
    sys.exit(1)

events = []
for line in ledger.read_text(encoding="utf-8").splitlines():
    if line.strip():
        events.append(json.loads(line))

kinds = [e.get("event") for e in events]
opened = [e for e in events if e.get("event") == "decision.opened"]
votes = [e for e in events if e.get("event") == "vote.cast"]
failed = [e for e in events if e.get("event") == "vote.failed"]
resolved = [e for e in events if e.get("event") == "decision.resolved"]

print("ledger_events", kinds)
print("opened", len(opened), "vote.cast", len(votes), "vote.failed", len(failed), "resolved", len(resolved))
for e in votes:
    meta = e.get("meta") or {}
    print("vote", e.get("voter"), e.get("option"), "model=" + str(meta.get("model")))
for e in failed:
    meta = e.get("meta") or {}
    print("failed", e.get("voter"), e.get("reason"), "model=" + str(meta.get("model")))
for e in resolved:
    print("resolved", e.get("verdict"), e.get("chosen_option"), e.get("reason"))

if len(opened) != 1 or len(resolved) != 1:
    sys.stderr.write("expected one decision.opened and one decision.resolved\n")
    sys.exit(1)
if len(votes) + len(failed) < 3:
    sys.stderr.write("expected three vote.cast or vote.failed rows\n")
    sys.exit(1)

models = []
resp_root = root / "responses"
if resp_root.is_dir():
    for path in sorted(resp_root.rglob("*-r1.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        models.append(payload.get("model"))
        print("response", path.name, "model=" + str(payload.get("model")))
if len(set(m for m in models if m)) < 3:
    sys.stderr.write("expected three distinct models in responses/*-r1.json\n")
    sys.exit(1)
PY

log "ensure-plan exit $plan_rc (deny is ok if ledger proof passed)"
log "workspace $LIVE"
exit 0
