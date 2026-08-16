# Security policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security-sensitive reports.

Prefer [GitHub private vulnerability reporting](https://github.com/mapyr/arbiter/security/advisories/new)
for this repository. Include:

- a short description of the issue
- impact / attack scenario
- steps to reproduce (or a minimal PoC)
- affected version / commit if known

We will acknowledge receipt and work on a fix or mitigation.

## What Arbiter is (and is not)

Arbiter is a **decision gateway**: closed option sets, immutable votes, quorum
thresholds, and an append-only ledger. It is **not** a complete multi-tenant
security product.

In particular:

- `ARBITER_HTTP_SECRET` only raises the bar from “know the URL” to “also know
  the shared secret”. Real isolation is a network concern (bind address,
  reverse proxy, private network, mTLS).
- Demo stacks (`deploy/podman/up.sh`, `scripts/live-smoke-up.sh`) generate API
  keys under `/tmp/…` for local use. Treat them as throwaway; never reuse demo
  secrets in production. The Podman lab uses `ARBITER_HTTP_SECRET` on the
  compose network and does not publish the arbiter port.
- Model voters receive only the evidence bundle over chat completions. Do not
  put secrets in evidence that should not leave your trust boundary.

## Safe contribution habits

- Never commit API keys, Hangar tokens, or `.env` files (see `.gitignore`).
- Prefer example configs (`*.yaml.example`) over live configs in PRs.
- Fail-closed behaviour is intentional: when in doubt, a change that silently
  allows mutations will be rejected in review.
