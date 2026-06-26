# Rolling Wave Design

## Motivation

The volatility-vs-spread wave (P(|return_H| > k×spread) by time of day) is not
static — it drifts as market microstructure evolves (spread compression, exchange
API changes, regime shifts, participant composition). A wave computed once over
all available history silently becomes stale. This document describes the rolling
wave design that supersedes the original single-file approach.

## Data Flow: X → Y → Z

Three parameters control the computation:

| Param | Meaning | Example |
|---|---|---|
| **X** (seconds) | Resample granularity: raw OrderBook ticks are aggregated to X-second bars (last mid/spread per window) | 1 |
| **Y** (seconds) | Observation interval: one metricA sample is taken every Y seconds | 300 |
| **Z** (seconds) | Wave bucket width: one wave point covers Z seconds of JST time-of-day | 3600 |

### Step 1 — Resample to X-second bars

```
raw OrderBook ticks  →  1 row per X-second window  (last mid, last spread)
```

### Step 2 — Sample metricA every Y seconds

For each Y-second mark t_y:
- Take the X-second bar at or just before t_y for the signal (mid, spread)
- ASOF-join the X-second bars to get mid at t_y + H (the forward return)
- Compute the indicator: `|mid(t_y+H) − mid(t_y)| > k × spread(t_y)`  →  0 or 1

This is metricA. It is defined for any scalar metric computable per Y-second
observation; P(|return_H| > k×spread) is the first and primary instance.

### Step 3 — Bucket into Z-second wave points

Each wave point covers the JST time-of-day window [Z_center − Z/2, Z_center + Z/2].
For Z=3600, the 24 wave points are centered on each full hour; the window for
the 03:00 point uses metricA observations in [02:30, 03:30] JST.

Key properties:
- **Non-overlapping**: with window width = Z, adjacent wave points get
  non-overlapping slices. Each metricA observation belongs to exactly one bucket.
- **Can use future data**: the wave is a historical profile computed after the
  fact, not a causal signal. The ±Z/2 centering gives a better estimate of the
  true hour's characteristics than a trailing-only window would.

### Sample count per wave point

```
observations per day per bucket  =  Z / Y          (e.g. 3600 / 300 = 12)
observations over 60-day window  =  60 × (Z / Y)   (e.g. 60 × 12 = 720)
```

720 observations per wave point is comfortably stable for a probability estimate.

## Rolling Window

- **Lookback**: 60 days (2 months) of data per wave computation
- **Cadence**: recomputed weekly
- **Gap**: no wave is available during the first 60 days of data history
  (e.g. 2025-07 and 2025-08 for GMO BTC_JPY, which starts 2025-07-01)

## File Layout

```
markets/waves/
  {exchange}_{symbol}/
    x{X}_y{Y}_z{Z}/
      {window_end_date}.csv     ← one file per weekly run
      {window_end_date}.csv
      ...
```

Example:
```
markets/waves/GMO_BTC_JPY/x1_y300_z3600/2026-06-22.csv
markets/waves/GMO_BTC_JPY/x1_y300_z3600/2026-06-15.csv
```

The filename is the **end date** of the 60-day data window. The start date is
`end_date − 60 days` and is also stored as a column inside the CSV.

### CSV Schema

| Column | Type | Description |
|---|---|---|
| `bucket` | int | 0 … 86400/Z−1, JST time-of-day index |
| `bucket_start_jst` | str | `HH:MM` label for the bucket center |
| `horizon_seconds` | int | H value for this row |
| `avg_spread` | float | mean spread (native units) across all observations |
| `n` | int | number of metricA observations (≈ 60 × Z/Y) |
| `p_gt_1x` | float | P(\|return_H\| > 1×spread) |
| `p_gt_2x` | float | P(\|return_H\| > 2×spread) |
| `p_gt_3x` | float | P(\|return_H\| > 3×spread) |
| `window_start` | str | first date of the 60-day data window (YYYY-MM-DD) |
| `window_end` | str | last date of the 60-day data window (YYYY-MM-DD) |

## Lookup API

```python
from tools.volatility_wave import load_wave

df = load_wave(
    exchange="GMO", symbol="BTC_JPY",
    x_seconds=1, y_seconds=300, z_seconds=3600,
    as_of_date="2026-06-20",           # picks latest file with end_date <= this
)
```

Rule: pick the most recent wave file whose `window_end` ≤ `as_of_date`. This
ensures the wave used for a scan decision reflects only information that existed
at the time of that decision.

## Generality

The X→Y→Z framework applies to any scalar metric computable per Y-second
observation from X-second resampled data. The current implementation is
P(|return_H| > k×spread), but the same pipeline could produce:

- Fill probability wave (P(fill within T seconds at distance D))
- Lead-lag strength wave (cross-correlation per bucket)
- SAR-conditioned volatility wave (P(|return_H| > k×spread | sar_flip))

## Legacy Files

The original single-file waves (`markets/waves/GMO_BTC_JPY.csv`, etc.) remain
as-is for reference. New code writes only to the dated directory structure above.
The `load_wave()` helper reads only from the new layout.
