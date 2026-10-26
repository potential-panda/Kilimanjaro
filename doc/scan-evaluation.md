# Scan Evaluation: Split, Rank Stability, and Verdicts

How every grid scan in this repo is judged. Implemented in
`src/kilimanjaro/framework/evaluate.py` (`run_grid_split`); used by every
expression grid scan (`cli.py grid horizon`, one shared core in
`driver/scan_runner.run_grid_scan`). Every run records its outcome as a
`scan_attached` ledger event.

## Why this exists

With hundreds of correlated combinations, a p ≤ 0.05 filter admits false
positives by construction; the data that selects a winner cannot also certify
it. The chronological split therefore lives in the framework rather than in
operator convention.

## The forced train/holdout split

Every grid runs on TWO windows: a train window and a holdout window
(default: last third of the range; override with `--split`). All combos run
on both. There is no code path that scans a single window and reports
candidates.

- The split date belongs to the holdout; the train window ends the day
  before (`split_windows`).
- The holdout is the *most recent* slice on purpose: deployment happens in
  next month's market, which resembles the recent window most.
- A holdout only protects the first pass — repeatedly tweaking until the
  holdout looks good quietly turns it into training data. Final tests for
  promotion candidates use *forward* data that did not exist at selection
time.

## Selection: rank stability, not p-values

A combo is `stable` when it is in the top `top_quantile` (default 10%) of
**both** windows ranked by `rank_metric` (default t_stat), with positive
avg_net in both, and at least `min_n` trades (default 30 — kills the
tiny-n significance mirage). Rationale: a real edge should be near the top
on data it never saw; a lucky one has no reason to be.

p-values remain in the output as evidence, never as the gate:

- `p_value_1sided` — classic t-test (i.i.d. assumption, overconfident for
  overlapping/autocorrelated trade returns).
- `p_value_bootstrap` — circular moving-block bootstrap (block ≈ n^(1/3)),
  robust to that autocorrelation. Deterministic seed.

`n_combos_tested` is written into every row so a result can never hide how
many tickets were bought.

## The verdict layer

A rejection is not one thing. Each combo gets a `verdict` computed from
three pieces of evidence the scan already produces:

1. **Rank** in each window (`train_rank_pct`, `holdout_rank_pct`).
2. **Monthly profile** — per-month n and avg_net across both windows
   (written to `output_monthly.csv`; months with < `min_month_n` trades,
   default 10, are ignored). Computed from trades already in memory, so it
   is ~free, and it provides the "fails only recently vs fails everywhere"
   evidence a k-fold walk-forward would give at 3–5x the scan cost.
3. **Parameter plateau** (`train_plateau`) — the share of the combo's grid
   neighbors (rows identical in every param except one numeric one,
   adjacent in that param's sorted values) with positive train avg_net.
   Real edges live on plateaus; an isolated winner whose neighbors
   disagree is selection noise.

Rules (`_classify`, cutoff = 1 − top_quantile):

| verdict | condition | follow-up |
|---|---|---|
| `stable` | rank stability criterion above | promotion candidate; run forward-window test before backtest handoff |
| `decayed` | train rank > cutoff AND ≥ 70% of train months positive AND plateau ≥ 0.5 AND ≤ 1/3 of holdout months positive | genuinely worked, then stopped. Don't deploy; record; "what turned it off" is a new hypothesis; monitor for recovery |
| `regime_dependent` | train rank > cutoff with alternating train months (40–70% positive), or a decayed-shaped train with mixed (not uniformly bad) holdout months | hunt for the on/off conditioning variable |
| `noise` | everything else, including isolated winners | forget it; the ledger remembers so it isn't re-tested |
| `no_data` | either window errored or produced no signals | fix data or spec |

Verdict counts go into each run's ledger event (`details.verdicts`), so
"0/245 stable (10 decayed)" is distinguishable from "0/245 stable" forever.

## Output files per run

```
results/grid_scan/{horizon,expr}/{scan_id}/
    params.json          full config: windows, grid, gates, ranking params
    output.csv           all combos: params + train_* + holdout_* + rank
                         pcts + pos_month_shares + train_plateau + verdict
    output_stable.csv    the stable subset
    output_monthly.csv   long format: params + window + month + n + avg_net
```

## Speed and determinism

Target prices load once per window and joins use in-memory search. Runs with the
same inputs and seed must produce byte-identical outputs. Cheap, deterministic
evaluation keeps regression checks straightforward.
