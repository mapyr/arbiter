# Cookbook: three client layers

Gate built-in editor tools (edit / write / bash) with Arbiter — without putting
policy logic inside the agent.

**Rule source:** only Arbiter decides criticality and coverage. The client asks;
it does not classify paths itself.

## When to use

- OpenCode (or similar) should refuse uncovered mutations
- You still want CI to block bad commits if the plugin is off

## Layer 1 — declarative permissions

Template: `client/opencode/opencode.permissions.jsonc`

| Rule | Intent |
|------|--------|
| Helper agents: `edit` / `bash` deny | Subagents cannot write or shell |
| `external_directory: deny` | Stay inside the worktree |
| Primary `bash` allow-list | Narrow day-to-day commands only |

## Layer 2 — thin plugin (execution)

Plugin: `client/opencode/plugins/arbiter-gate.js`  
Install: copy into `.opencode/plugins/` (Podman `up.sh` does this for the demo).

1. Intercept mutating tools (`edit`, `write`, `bash`, `apply_patch`, …).
2. With `ARBITER_GATE_ALL=1`: gate every tool except a small read-only list
   (`read` / `glob` / `grep` / `list`). Skip Hangar MCP tools (`hangar*`) so
   control-plane calls do not fail-close.
3. Prefer Hangar → Arbiter MCP (`get_gate_policy`, `check_coverage`) when
   `HANGAR_URL` + `HANGAR_API_KEY` are set. Fallback: local `arbiter check-coverage`.
4. Uncovered paths → throw `ARBITER_PLAN_REQUIRED` so the agent can call
   `ensure_plan`, then retry.
5. Arbiter down / parse error → **deny**.
6. Emergency: `ARBITER_BREAK_GLASS=1` for one process — still writes
   `break_glass.used` to the ledger. Invisible break-glass is forbidden.

## Layer 3 — commit backstop

CLI: `arbiter verify-commit`  
Optional hook: `scripts/git-hooks/pre-commit-arbiter`  
CI job: `.github/workflows/ci.yml` → `commit-gate`.

Checks against the ledger:

1. Decision exists and verdict is allow
2. Scope covers **all** critical changed paths
3. Decision not expired at commit time

Trailer required on critical-path commits:

```text
Arbiter-Decision: <decision_id>
```

Break-glass on those paths fails CI unless `ARBITER_ALLOW_BREAK_GLASS=1`.

## Invariants (plain language)

- One rule source — Arbiter only
- No answer → deny
- Break-glass is a ledger event
- Scope is immutable after open
- Plugin off ⇒ CI still blocks

## See also

- [Podman demo](./podman.md)
- [Formulation barriers](./formulation.md)
- [Tutorial § commit gate](../tutorial.md#9-commit-gate-l3)
