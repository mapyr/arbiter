# Decision ledger directory

Point `ARBITER_DATA_DIR` here when you want CI’s `commit-gate` job to enforce
layer 3 against a real registry (`ledger.jsonl`).

This path is gitignored for local scratch (`decisions/*` except this README).
To enforce in CI, force-add a shared ledger:

```bash
export ARBITER_DATA_DIR="$(pwd)/decisions"
# … open + resolve a covering decision …
git add -f decisions/ledger.jsonl decisions/bundles/
```

Commits that touch critical paths (see `arbiter.rules.yaml`) must carry:

```text
Arbiter-Decision: <decision_id>
```
