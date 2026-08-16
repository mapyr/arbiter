# Cookbook: home-lab production slice

Private, single-trust-boundary stack. Not a multi-tenant product. Not PyPI.

Bring-up: [`podman.md`](./podman.md) (`./deploy/podman/up.sh --reset` once after
this slice; then `up.sh` to recreate).

## Slice (what is in, what is out)

| Surface | Lab contract |
|---------|----------------|
| Client | OpenCode in `$OPENCODE_PROJECT` (`/tmp/arbiter-podman/project`) |
| L1 | permissions in that project's `opencode.json` |
| L2 | `arbiter-gate.js`; uncovered mutations → `ARBITER_PLAN_REQUIRED` |
| L3 | `commit-msg` hook in the demo git repo; trailer `Arbiter-Decision: <id>` |
| Held MCP | `mockfs/*` and `mockhttp/*` (`approval_list: *` **and** intercept) |
| Voters | 1–3 Copilot-compatible models; **start in shadow** |
| Rules | `critical.paths: ["**"]` — everything is critical |
| Break-glass | `ARBITER_BREAK_GLASS=1` records; CI/hook ack is `ARBITER_ALLOW_BREAK_GLASS=1` |
| HTTP | `ARBITER_HTTP_SECRET` on the compose network; arbiter port not published |
| Ledger | host bind `$ARBITER_PODMAN_STATE/decisions` = container `/data/decisions` |

Out of scope: other IDEs, public install, mTLS, multi-tenant, Hangar docker discovery.

## Shadow window, then enforce

Compose ships `shadow_mode: true` in `deploy/podman/config/arbiter.voters.yaml`.
Quorum is ledgered; holds still proceed. Stay in shadow until
`compounding.ready_to_enforce` is true:

```bash
source /tmp/arbiter-podman/env.sh
arbiter report-eval --horizon-days 14 --format json
```

Gates (`compounding.gates`, same keys in [`formulation.md`](./formulation.md)):

| Key | Default |
|-----|---------|
| `hold_total_min` | 10 |
| `hold_covered_share_min` | 0.4 |
| `hold_quorum_share_max` | 0.5 |
| `one_shot_share_max` | 0.5 |

Do not flip compose to enforce from a thin sample. When ready:

1. Set `shadow_mode: false` in `deploy/podman/config/arbiter.voters.yaml`.
2. `./deploy/podman/up.sh` (recreate Hangar so voters reload).
3. Quorum deny now blocks held tools. L2 still denies uncovered edits.

## Operator loop

```bash
source /tmp/arbiter-podman/env.sh
cd "$OPENCODE_PROJECT" && opencode
# uncovered edit → hangar_call arbiter/ensure_plan with a narrow scope (auth/**)
tail -n 20 "$ARBITER_DATA_DIR/ledger.jsonl" | jq -c '{e:.event,mode:.mode,approved:.approved}'
```

Backup: copy `$ARBITER_DATA_DIR/ledger.jsonl` and `bundles/`. Never hand-edit the ledger.

Voter keys down → missing votes → deny (or shadow still proceeds). That is the product.

Break-glass: one process `ARBITER_BREAK_GLASS=1`; later commits need
`ARBITER_ALLOW_BREAK_GLASS=1` or they fail L3.

## Layer 3

`up.sh` `git init`s the demo project and installs `commit-msg`. Commits that
touch critical paths (here: all paths) need a covering allow and the trailer.
Emergency skip: `ARBITER_SKIP_COMMIT_GATE=1` (still fix the ledger afterwards).

This git repo (`mapyr/arbiter`) stays private and does not force-add a ledger
into CI. The lab workspace is the gated tree.

## Wiring and health

Hangar boot runs a ledger probe (`hold.accepted` for `__arbiter_wiring__`).
If that write fails, Hangar must not degrade to `noop` — the process exits.

`GET /health` (no secret) is 200 only if the ledger path can be locked and
fsynced. It does not append an event.
