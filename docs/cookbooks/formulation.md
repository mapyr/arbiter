# Cookbook: writing good decisions

How to open decisions that voters can reuse — and that barriers will accept.

Use after a shadow window (`arbiter report-eval`) to see whether scopes and
options are paying for themselves; rewrite this habit if medians move.

## Checklist before `open_decision`

1. **Options: 2–3 real alternatives.** Each must be something a careful
   reviewer could defend. Do not pad with `n/a`, `other`, or empty `yes`/`no`
   next to one substantive string. Arbiter refuses filler sets when
   `formulation.deny_filler_options` is on.
2. **Scope: name the call or path family, not the world.** Prefer
   `github/create_issue` or `arbiter/domain/**` over `**/*`. Universal patterns
   are refused when `formulation.deny_universal_scope` is on.
3. **Evidence: put the disagreement material in the bundle.** Paths, diffs,
   prior decision ids, agent rationale. If round 1 often misses quorum, rewrite
   the question before adding voters.
4. **Prefer reuse over re-adjudication.** Scope allows so later holds hit
   `path=covered`; if coverage stays near zero, scopes are too tight.

## Barriers (rules only)

```yaml
formulation:
  deny_universal_scope: true
  deny_filler_options: true
```

Callers cannot disable these at open time — edit `arbiter.rules.yaml`.

## Reading `report-eval` signals

| Signal | Reading |
|--------|---------|
| Unanimous allow + high `covered` share | Scope/options specific enough to reuse |
| Round-1 miss / reveal | Question or option set underspecified |
| One-shot decisions (never covering later holds) | Scope too narrow or call identity missing |
| Very high coverage with broad scopes | Stamp factory — barrier should have fired |

## See also

- [Tutorial § shadow vs enforce](../tutorial.md#8-shadow-vs-enforce)
- [Client layers](./client-layers.md)
