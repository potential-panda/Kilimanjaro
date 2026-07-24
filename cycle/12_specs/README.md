# Scan specs — the loop's queue

One flat directory of scan-spec YAMLs. `python src/cli.py loop` (and the
`factory` cadence) processes every `*.yaml` here: validated, deduped against the
ledger, scanned (train/holdout + verdicts), and recorded. Survivors get a
`REVIEW_PACKET.md` in their results folder.

These are scan *configurations*, not hypotheses. Every spec names its hypothesis
via `hypothesis_id:`, and the idea itself — title, mechanism, falsification
tests, decision trail — lives in the registry at `../11_hypotheses/hyp-*.yaml`.

## The invariant: one idea → many specs

A **hypothesis is an idea** — an alpha / strategy / formula / expression (e.g.
"does momentum on a BINANCE leader predict the JPY venue?"). A **spec is one
concrete, executable rendering** of that idea: a market (leader → target) plus a
parameter grid. The same idea tested on ETH and on BTC is **one hypothesis with
two specs**, never two hypotheses.

The idea is identified by `driver.generate.idea_key` — family + the *set* of
numeral-blanked expression shapes. Market, target, thresholds and horizons are
*execution*, not idea, so they do not create a new hypothesis. Two rules, both
enforced:

1. **one idea → one hypothesis id** (never split an idea across ids), and
2. **one hypothesis id → one idea** (never pin two ideas to one id).

This is not a convention — it is checked by
`driver.validate.idea_consistency_errors`, and `tests/test_generate.py` runs that
check against this directory, so a spec that re-fragments an idea fails the test
suite. `cli.py generate` already assigns ids this way (it reuses the hypothesis
already covering an idea); the guard exists for hand-added or hand-edited specs.

Processing is idempotent — a spec whose (content, window) fingerprint is already
in the ledger is skipped, so re-running the loop is safe.

## Naming

| kind | filename | written by |
|------|----------|------------|
| generated | `hyp-YYYYMMDD-NNN_<family>_<target>.yaml` | `cli.py generate` |
| hand-written | `hyp-YYYYMMDD-manual_<name>.yaml` | a human |

The `manual_` infix marks specs a person authored. They matter because the
generator can only emit what `driver/generate.py:FAMILIES` encodes, at
`return_calc: fixed_horizon` — so mechanisms like `episode`, and any idea outside
those families, can *only* arrive by hand. Keep that path open.

Note the filename prefix is not the hypothesis id: ids are `hyp-YYYYMMDD-NNN`
(schema-constrained), and several manual specs may share one. The id inside the
file is authoritative; the filename only names the results folder.

```bash
# run one spec directly, outside the loop
python src/cli.py grid horizon --spec cycle/12_specs/hyp-20260714-manual_basis_eth.yaml \
    --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
```
