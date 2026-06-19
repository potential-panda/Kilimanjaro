# Scan Reports

One JSON file per signal scan, in the format defined by
`../requirements/data-contract.md` ("Scan Report Output").

Naming: `scan-YYYYMMDD-NNN.json`, where `NNN` is a zero-padded sequence
number unique within the date. A report's own `id` field must match its
filename. `hypothesis_id` links it back to the spec in `../specs/`.

A scan report has three kinds of content:

- `metrics` — signal-specific: IC, hit rate, bucketed returns, decay,
  sample counts, significance, all for the **full scanned window**.
- `market_context` — signal-*independent*, full-window: real conditions
  for the exact scanned market/window, always present no matter what's
  being tested.
  - `avg_half_spread_bps` — measured directly from the target market's own
    data over the exact scanned window, not a value declared ahead of time.
  - `realized_volatility_bps` — stdev of the target's realized return at
    the primary horizon, same window.
  - `lead_lag` — `null` if feature and target are the same market (not
    applicable); otherwise `{distinct_days, feature_leads_fraction,
    mean_best_lag_seconds, mean_best_corr, mean_corr_at_zero}`, computed
    directly from bronze OrderBook (1-second resampled mid-price
    cross-correlation, per day) for exactly this window -- not Makalu's
    gold `LeadLag` dataset, whose only covered period
    (2025-12-30..2026-01-31) doesn't match any window this project scans.
- `regime_restricted` — the same metrics and market context, but
  restricted to a **static, history-derived favorable trading window**
  (`tools/regime_window.py`), disclosed alongside the full-window result
  above, never replacing it. `available: false` (with a `reason`) when no
  precomputed artifact exists for this market, or too few rows fall in
  the favorable window to report.

It is attached to the hypothesis's ledger entry in `../ledger/`.

Generate one with `tools/scanner.py` (validate the spec first):

```bash
python tools/validate_spec.py specs/hyp-20260618-001.yaml
python tools/scanner.py --spec specs/hyp-20260618-001.yaml --start 2026-05-01 --end 2026-05-15
```

`threshold_verdict.status` is always `"not_evaluated"` straight out of
`scanner.py` — apply the v1 pass/fail thresholds with
`tools/threshold_evaluator.py`:

```bash
python tools/threshold_evaluator.py --report scan-reports/scan-20260618-001.json --spec specs/hyp-20260618-001.yaml --write
```

Status is one of `pass`, `fail`, `needs-more-data` (fewer than 5 distinct
days), or `needs-tooling` (the cost buffer can't be computed without
`market_context.avg_half_spread_bps` -- shouldn't happen with a current
`scanner.py`, but checked rather than assumed). `failed_rules` use the
exact `decision_reason` codes from `../requirements/hypothesis-contract.md`'s
enum. `threshold_verdict.cost_buffer` carries the full structured
breakdown (`edge_bps`, `fee_bps`, `half_spread_bps`, `slippage_bps`,
`safety_multiplier`, `required_bps`, `cleared`) -- not just the
human-readable summary in `verdict_text`.

`threshold_verdict.regime_restricted_cost_buffer` is the same breakdown
computed from `regime_restricted`'s rows -- purely informational, never
affects `status`/`failed_rules`. A spec would need to explicitly declare
an intended trading-hours restriction as part of its own design before a
regime-restricted result could become official; right now it's a
disclosed second opinion, not a verdict.

v1 scope notes (see `tools/scanner.py`'s and `tools/threshold_evaluator.py`'s
module docstrings): one feature source dataset and one target source
dataset, both bronze `OrderBook`; `target.type: research_label` is not
implemented; regime-bucket stability (`stability_by_regime`) is always
empty and always disclosed as the `regime-stability-unproven` yellow flag,
never silently assumed fine; look-ahead/timestamp-alignment/missing-
`param_id` checks are spec-validation concerns, not re-verified here.
