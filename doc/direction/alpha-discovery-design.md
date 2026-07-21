# Alpha Discovery Design

Recorded from session 2026-07-01. Extends `workflow-strategy.md` with
decisions specific to the new-alpha track (as opposed to the existing
SAR-validation track). All decisions here are directional; scanner code should
explicitly implement the distinctions below.

---

## 1. Strategy Style — standard vocabulary

Two payoff styles are in scope. Use the word **style** (not family, not type)
for this distinction going forward.

### Precision style
- Win rate drives expectancy: P(win) is high, each win is small.
- PnL distribution is roughly symmetric or slightly left-skewed.
- Typical mechanisms: short-horizon mean reversion, order-book imbalance
  fading, microprice reversion, spread capture.
- Scanner mode: **fixed-horizon** — measure IC and hit rate at a specific
  forward return horizon.
- Pass criterion: `hit_rate_net > 50%` AND `avg_net_return > cost floor`.

### Skew style
- Magnitude drives expectancy: P(win) is low, but E[win] >> E[loss].
- PnL distribution is right-skewed (many small losses, occasional large wins).
- Typical mechanisms: trend-following, momentum, range-bar SAR, breakout.
- Scanner mode: **between-signal** — measure the return from signal entry to
  the next signal reversal (exit = next flip, not a fixed horizon). A
  fixed-horizon window is the wrong lens: it exits before the trend completes
  and systematically underestimates the edge.
- Pass criterion: `avg_net_return > 0` AND `skewness > 1` (right-tailed wins)
  AND `P(win)` is stable enough that ruin risk is bounded.

### Why the distinction matters for scanner code
A skew-style hypothesis evaluated with the precision-style scanner (fixed
horizon) will almost always fail, even if the underlying edge is real. The
scanner must select its mode based on the hypothesis spec's declared style,
not apply one mode uniformly. The spec field `signal_style: precision | skew`
is therefore required before a scan is run.

---

## 2. Horizon — vol/spread constraint

### The fundamental constraint
A fixed horizon H is only tradeable if the expected realized volatility over H
exceeds the round-trip cost (spread + fees + slippage):

```
vol(H, t) > spread(t) + fees + slippage
```

Below this threshold, the strategy loses to costs on every trade regardless of
hit rate. No signal quality can compensate.

This is not a soft preference — it is a mechanical filter. Any horizon that
fails this test should be excluded from the param sweep entirely, not
discovered as a failure after scanning.

### The daily wave
Both vol and spread have a strong time-of-day seasonality (JST). The vol/spread
ratio is not constant: at some hours (e.g. low-activity early morning) even a
300s horizon may not clear the spread; at high-activity hours a 30s horizon
may easily clear it.

The existing `cycle/02_markets/waves/` files already capture this. The wave is the
**knowledge** — it is learned from recent historical data, not adapted
real-time. Real-time adaptation introduces look-ahead and instability; the
wave is a stable prior refreshed periodically (e.g., weekly from the last 30
days of data).

### Dynamic horizon dataset
A pre-computed horizon dataset should be populated and stored per
exchange/symbol. It maps each time-of-day bucket to the minimum viable
horizon:

```
(exchange, symbol, jst_hour_bucket) → min_viable_horizon_seconds
```

Concretely: for each JST bucket (e.g., 30-minute slots), compute from the
wave the smallest H ∈ candidate_horizons such that `vol(H) > spread + cost`.
Store this as a queryable dataset so the scanner can look up the viable
horizon range for any signal timestamp rather than hardcoding a global horizon.

**Candidate horizons to evaluate:** `[15, 30, 60, 120, 300, 600]` seconds.
The dataset records the minimum viable horizon per bucket, not just a binary
pass/fail, so the scanner can pick the most favorable horizon dynamically.

### How the scanner uses it
1. For each signal event timestamp, look up the JST bucket.
2. Fetch `min_viable_horizon` for that bucket from the horizon dataset.
3. Only test horizons ≥ `min_viable_horizon`. Horizons below it are excluded
   before the IC/hit-rate computation, not filtered out from results after.

This means precision-style scans are horizon-aware by construction.
Between-signal scans (skew style) are not directly horizon-filtered —
the holding period is determined by the signal itself — but the vol/spread
wave still matters as a **regime conditioning variable**: if a skew signal
fires during a low-vol/wide-spread bucket, the expected edge may be lower.

### Horizon dataset population
Populate the dataset from the existing wave files as a one-time job, then
refresh on a schedule (e.g. weekly). Source: `cycle/02_markets/waves/<exchange>_<symbol>.csv`
and `tools/volatility_wave.py`. Output: a new Makalu dataset or a local file
in `cycle/02_markets/horizons/<exchange>_<symbol>_horizon.csv` with schema:

```
jst_hour_bucket, min_viable_horizon_seconds, vol_at_min_h_bps, spread_bps, cost_bps
```

---

## 3. Parametric scanner — design

The scanner sweeps a parameter grid for a given hypothesis family and outputs
a metric heatmap, not just a single pass/fail. This is the signal-level
analogue of Himalaya's param grid, but cheap (SQL, no simulation).

```
hypothesis spec (style + param_grid)
    ↓
for each param combo:
    if style == precision:
        for each viable horizon (from horizon dataset):
            compute IC, hit_rate_net, avg_net_return at that horizon
    if style == skew:
        compute avg_net_return, skewness, P(win), MAE from between-signal returns
    ↓
heatmap: (param_a × param_b × horizon?) → metrics
    ↓
gate: any region of the heatmap clears thresholds?
    no  → ledger: scan-failed with heatmap artifact
    yes → surviving combos → human review → Himalaya with narrow grid
```

### Anti-overfitting guard — the neighborhood rule
A combo that clears thresholds but is surrounded by failing neighbors is noise.
A combo at the center of a region where multiple neighbors also pass is signal
worth escalating. The heatmap makes this visible before touching Himalaya.
The scanner should flag isolated winners explicitly in its output.

---

## 4. Relationship to existing work

- The SAR validation track (hyp-20260624-*) used the between-signal mode
  (`flip_scan_v1`). That is correctly categorized as **skew style**.
- New hypotheses from the taxonomy (order book imbalance, trade aggression,
  microprice reversion) are primarily **precision style** and should use the
  fixed-horizon mode with the dynamic horizon dataset.
- The scanner code (`tools/scanner.py`, `tools/flip_scan.py`) should be
  refactored or extended so the style is an explicit input and the scan path
  branches accordingly. The two code paths should not share a function that
  silently applies one mode to both styles.

---

## 5. Next steps (as of 2026-07-01)

1. Populate `cycle/02_markets/horizons/` from existing wave files for GMO BTC/ETH/XRP.
2. Add `signal_style: precision | skew` to the hypothesis spec schema.
3. Extend the scanner to branch on style and use the horizon dataset for
   precision-mode scans.
4. First new alpha target: **order book imbalance** (taxonomy family #1,
   precision style) — `BookState.imbalance` at depth levels 1–5, BINANCE as
   source, GMO as target, param grid over depth × horizon.
