# Contributing

Thanks for improving Arbiter. This document is the short path from clone to PR.

## Development setup

```bash
git clone https://github.com/mapyr/arbiter.git
cd arbiter
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install ".[dev]"
```

Optional Hangar extras (already included in `.[dev]`):

```bash
pip install ".[hangar]"
```

Copy example configs for local experiments (do not commit secrets):

```bash
cp arbiter.rules.yaml.example arbiter.rules.yaml
cp arbiter.voters.yaml.example arbiter.voters.yaml
cp arbiter.intercept.yaml.example arbiter.intercept.yaml
```

## Tests

```bash
pytest                         # full suite
pytest -m "not integration"    # faster unit path
pytest -m integration          # MCP / HTTP / stub providers
python scripts/check_hexagon_boundaries.py
```

All quorum tests run **offline** against an OpenAI-compatible stub. Do not add
tests that require live API keys in CI.

## Architecture constraints

The package is hexagonal:

| Layer | May depend on | Must not |
|-------|---------------|----------|
| `arbiter.domain` | stdlib / itself | application, adapters, I/O frameworks |
| `arbiter.application` | domain | adapters |
| `arbiter.adapters` | application + domain | (outbound I/O lives here) |

`scripts/check_hexagon_boundaries.py` enforces this in CI. Domain changes also
require review (`CODEOWNERS`).

## Documentation

- User-facing docs live under [`docs/`](docs/README.md) and must stay in
  **English**. Prefer updating the tutorial / how-it-works when behaviour
  changes.
- Do not commit proprietary provider endpoints, internal model ids, or
  machine-local absolute paths.
- Do not commit generated demo state under `/tmp/arbiter-*`.

## Pull requests

1. Keep changes focused; avoid drive-by refactors.
2. Add or update tests for behavioural changes.
3. Run the relevant pytest subset locally before opening the PR.
4. Describe *why* in the PR body; link issues when applicable.

## Release notes (maintainers)

Bump `version` in `pyproject.toml` when shipping a user-visible change.
Keep `mcp` / `mcp-hangar` pins intentional — document pin bumps in the PR.
