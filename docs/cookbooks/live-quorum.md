# Cookbook: live three-model quorum

Prove one `ensure-plan` against **three independent** OpenAI-compatible
providers (a 3-voter recipe; the engine accepts 1..7, including the same
`base_url` with different `model` ids). No Hangar, no OpenCode, no Copilot token.
CI never runs this.

Protocol is unchanged: [`OpenAIVoterGateway`](../../arbiter/adapters/outbound/openai_voter_gateway.py)
POSTs `{base_url}/chat/completions`; [`ModelQuorumService`](../../arbiter/application/services/model_quorum.py)
parses a JSON vote. The stub in `tests/openai_stub.py` stays offline-only.

## Keys

| Voter | `base_url` (from example YAML) | Env |
|-------|--------------------------------|-----|
| voter-1 | `https://api.openai.com/v1` | `ARBITER_VOTER_1_KEY` (OpenAI) |
| voter-2 | `https://openrouter.ai/api/v1` | `ARBITER_VOTER_2_KEY` (OpenRouter) |
| voter-3 | `https://generativelanguage.googleapis.com/v1beta/openai` | `ARBITER_VOTER_3_KEY` (Google AI) |

Do not put keys in YAML. Local `arbiter.voters.yaml` at the repo root is gitignored.

## Quick start

```bash
cd /path/to/arbiter
export ARBITER_VOTER_1_KEY='…'
export ARBITER_VOTER_2_KEY='…'
export ARBITER_VOTER_3_KEY='…'
./scripts/live-quorum.sh
```

Missing any key → exit 1 **before** HTTP. Workspace: `/tmp/arbiter-live-quorum`
(override `LIVE=`).

The script copies `arbiter.voters.yaml.example` + `arbiter.rules.yaml.example`,
runs `arbiter ensure-plan` on a narrow `auth/**` plan, then prints ledger rows.

## Expected ledger

`$ARBITER_DATA_DIR/ledger.jsonl` (`/tmp/arbiter-live-quorum/decisions` by default):

- one `decision.opened`
- three `vote.cast` **or** `vote.failed` (timeout / `http_401` / unparseable)
- one `decision.resolved`

Raw replies: `decisions/responses/<decision_id>/<voter>-r1.json` — three
different `model` values from the YAML.

A **deny** verdict is still a successful smoke: the brain ran. Fail-closed on
missing votes is intended.

## Diagnostics

| Symptom | Likely cause |
|---------|----------------|
| script dies `missing ARBITER_VOTER_*_KEY` | env unset; no provider call |
| `vote.failed` `http_401` | wrong or empty key for that voter |
| `vote.failed` `http_404` / `http_400` | `model` id stale on that provider — edit YAML `model` only |
| `vote.failed` `timeout` | raise `timeout_seconds` in YAML |
| `vote.failed` unparseable / not JSON | model ignored the JSON instruction; one retry then deny |
| `decision.resolved` `reason` incomplete | missing vote counts as no vote → usually deny |

If a provider returns `http_404` / `http_400`, change **only** `model` (or
`base_url`) in [`arbiter.voters.yaml.example`](../../arbiter.voters.yaml.example)
and re-run. Do not add a new adapter. The script wipes `$LIVE` and recopies the
example on every run.

## Not this cookbook

Full Hangar + OpenCode stack (Copilot token, holds): [live-smoke.md](./live-smoke.md).
Containers: [podman.md](./podman.md).
