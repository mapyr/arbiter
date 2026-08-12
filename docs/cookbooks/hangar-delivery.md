# Cookbook: Hangar hold → Arbiter

Wire Hangar so held MCP tools notify Arbiter, run coverage/quorum, then
resolve the hold. Pin: **`mcp-hangar==2.6.0`**.

## Goal

```text
Agent → Hangar (hold) → Arbiter delivery → ledger → REST resolve → Hangar continues/denies
```

Delivery is a **notification** (`send` returns `None`). The verdict is a
**separate** authenticated `POST /approvals/{id}/resolve`.

## Quick setup

1. Install `pip install "arbiter[hangar]"` so the entry point
   `mcp_hangar.approvals.delivery` → `arbiter` is registered.
2. Hangar config: `approvals.enabled: true`, `approvals.channel: arbiter`,
   plus `resolve_base_url` / `resolve_token_env` (see `deploy/podman/config/`).
3. Enable Hangar auth. Put resolve credentials in the environment only
   (`ARBITER_HANGAR_RESOLVE_TOKEN`, `ARBITER_HANGAR_PRINCIPAL_ID`) — never in YAML.
4. List tools to adjudicate in `arbiter.intercept.yaml`. Missing file → refuse start.
5. Prefer the Podman demo: [`podman.md`](./podman.md).

## Behavioural wiring check

A bad channel **does not** stop Hangar boot — it degrades to `noop`. Prove the
wire: send a test hold and require `hold.accepted` in Arbiter’s ledger
(`assert_delivery_wired` in the adapter). Config presence alone is not proof.

## Adapter contract (must hold)

1. `send` writes `hold.accepted` immediately, then returns.
2. Adjudication / REST resolve runs in the background; failures are logged, never
   raised from `send`.
3. Quorum budget comes from Hangar `expires_at` minus `hold_margin_seconds`.
   Too little time → immediate deny `insufficient_time_for_quorum:…` (no quorum).

## Idempotency

Key = `mcp_server_id` + `tool_name` + `arguments_hash` from Hangar’s payload.
Identical arguments share a decision even across callers — intentional for
correlation. Hangar’s `approval_list` decides what is held; Arbiter’s intercept
file decides what to adjudicate. Unlisted delivered tools are passthrough-approved
and recorded.

## See also

- [Podman demo](./podman.md)
- [Live smoke (host)](./live-smoke.md)
- Hangar `ApprovalDelivery` / resolve routes in the installed `mcp-hangar` package
