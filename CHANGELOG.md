# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.2] — 2026-08-16

Home-lab production slice (private, single trust boundary).

### Added

- Ledger `flock` + `fsync`; `GET /health` is 200 only if that path is writable
- Hangar delivery factory probes `hold.accepted` and exits on failure
- Podman lab: static arbiter remote + `ARBITER_HTTP_SECRET`, host-bind ledger,
  commit-msg L3 hook on the demo workspace
- [docs/cookbooks/lab.md](docs/cookbooks/lab.md) — shadow window then enforce

### Changed

- Podman no longer uses Hangar docker discovery or `ARBITER_ALLOW_INSECURE_HTTP`
- Lab voters default to `shadow_mode: true` until you flip them

## [0.7.1] — 2026-08-12

First public open-source release.

### Added

- Deterministic MCP decision gateway with append-only JSONL ledger
- Hexagonal CQRS core: closed options, immutable votes, quorum thresholds
- HTTP transport with shared-secret gate (`X-Arbiter-Secret`)
- Hangar hold-delivery adapter and plan / coverage client gate (L2)
- Model quorum voters (OpenAI-compatible), shadow-mode evaluation
- Decision ladder (offline scenarios S1–S6) and domain CI gates
- Podman demo stack (`deploy/podman/up.sh`) and live-smoke scripts
- English OSS docs: tutorial, how-it-works, security policy, contributing

### Security

- HTTP MCP refuses to start without `ARBITER_HTTP_SECRET` unless
  `ARBITER_ALLOW_INSECURE_HTTP=1` (private networks / compose only)
- Fail-closed defaults for missing votes, coverage, and rules
- Secrets expected via environment variables; `*.yaml.example` only in git

## [0.7.0] — 2026-08-11

Internal milestone: plan gate, Podman demo, decision ladder, English docs.

[0.7.2]: https://github.com/mapyr/arbiter/releases/tag/v0.7.2
[0.7.1]: https://github.com/mapyr/arbiter/releases/tag/v0.7.1
[0.7.0]: https://github.com/mapyr/arbiter/releases/tag/v0.7.0
