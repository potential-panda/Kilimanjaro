# Flip Scan Baseline

## What This Is

The flip scan (`tools/flip_scan.py`) measures the profitability of holding a
position between consecutive SAR reversals on BINANCE range-bar charts, then
exiting at the next SAR cross. The "between-flip" return captures the skew
inherent to a trend-following signal in mean-reverting or trending markets.

## Baseline Parameters

| Parameter | Value |
|---|---|
| `box_size_rate` | 0.0025 |
| `sar_step` | 0.02 |
| `costs_bps` | 1.0 |
| `rolling_dd_window_seconds` | 21600 (6 hours) |
| `rolling_dd_threshold_bps` | 1000 |

These parameters are fixed across all scans unless a variant explicitly states
otherwise (e.g. variant C uses step=0.04).

## Risk Gate

The **only** gate is `RollingPeakDrawdownGate`. No spread gate, no trade-rate
gate, no SAR-flip-rate gate.

The gate blocks new entries when the cumulative signed return (in bps) within
the trailing `rolling_dd_window_seconds` window has fallen more than
`rolling_dd_threshold_bps` below its rolling peak. When the gate is active,
flips are skipped (counted as `n_rolling_dd_blocked`) rather than traded.

This matches the backtest (`orion/template.py`), which has only
`RollingPeakDrawdownGate` with an equivalent threshold (threshold_pct=10
≈ threshold_bps=1000 at typical order sizes).

### Gate Calibration Intent

The gate should activate **2–3 times** over the full validated dataset
(2025-07-01 to 2026-06-29), targeting extreme volatile events rather than
routine drawdowns. The primary event it must handle is:

**2025-10-10 (Oct 10 chaos)**: A sharp price crash produced ~10,645 SAR flips
on XRP in a single day (vs. a typical 20–80/day). Without the gate, all those
flips would be traded during a period of total signal breakdown. With
`threshold_bps=1000 / window=21600s`, the gate blocks 10,609 of those flips
(99.7%), resuming normal trading once the drawdown recovers.

A threshold set too low would trigger on routine volatility. A threshold set
too high would fail to protect against chaos days. 1000 bps / 6-hour window
was chosen so that normal month-to-month drawdowns (typically tens of bps per
6-hour period) do not trigger the gate, while a single-day crash of the Oct-10
magnitude (thousands of bps of cumulative loss within hours) does.

## Validated Window

`2025-07-01` to `2026-06-29`

Data before 2025-07-01 is not validated and has been removed from
`ChartRangeSAR`. The dataset is updated daily; scans should use the most
recently available end date.

## Execution Sizes

| Market | `execution_size` |
|---|---|
| GMO:XRP_JPY | 150 |
| GMO:ETH_JPY | 0.1 |
| GMO:BTC_JPY | 0.002 |

## Active/Inactive Windows

Derived from VolSpread stability analysis (see
`/home/lulurun/env/MARIO_MARIO/models/active_windows_summary.md`):

| Market | Active (JST) | Inactive (JST) | Weekend |
|---|---|---|---|
| XRP_JPY | 22:00–07:00 | 07:00–22:00 | inactive |
| ETH_JPY | 22:00–06:00 | 06:00–22:00 | inactive |
| BTC_JPY | 22:00–06:00 | 06:00–22:00 | inactive |

## Scan Variants

| Variant | Schedule filter | step |
|---|---|---|
| A | none (all hours, all days) | 0.02 |
| B | active hours only, no Saturday | 0.02 |
| C | active hours only, no Saturday | 0.04 |
| D | inactive hours only, no Saturday | 0.02 |

Variants B/C/D use `block_jst_hours` and `block_sat_jst_hours` to restrict
trading to the declared window.

## Baseline Results (2025-07-01 to 2026-06-29)

### XRP/JPY (execution_size=150)

| Variant | Raw flips | DD-blocked | Executed | Avg net |
|---|---|---|---|---|
| A (no filter) | 22,929 | 10,609 | 12,320 | +1.48 bps |
| B (active, step=0.02) | 22,929 | 0 | 5,258 | +1.05 bps |
| C (active, step=0.04) | 28,767 | 0 | 6,807 | +1.41 bps |
| D (inactive) | 22,929 | 0 | 6,168 | +2.22 bps |

The inactive window (D) shows the strongest edge for XRP. Variant A's large
DD-blocked count (10,609) is almost entirely the Oct-10 chaos day.

### ETH/JPY (execution_size=0.1)

| Variant | Raw flips | DD-blocked | Executed | Avg net |
|---|---|---|---|---|
| A (no filter) | 19,099 | 8,197 | 10,902 | +2.79 bps |
| B (active, step=0.02) | 19,099 | 826 | 4,576 | +2.17 bps |
| C (active, step=0.04) | 24,128 | 1,042 | 5,939 | +1.58 bps |
| D (inactive) | 19,099 | 7,369 | 5,582 | +2.88 bps |

ETH shows positive edge across all variants. The inactive window (D) is
marginally strongest. Variant A's large DD-blocked count is also concentrated
on Oct-10. Variant B's low block count (826) reflects that the Oct-10 crash
mainly fell during inactive JST hours.

### Consistent finding across both markets

- All 8 variants are positive after costs.
- The rolling_dd gate alone is sufficient to handle the Oct-10 chaos without
  any manual date exclusions.
- The inactive window (D) slightly outperforms the active window (B) for both
  XRP and ETH.

## Related Files

- `tools/flip_scan.py` — scan implementation
- `scan-reports/xrp-active-{A,B,C,D}-*.json` — XRP baseline scan outputs
- `scan-reports/eth-active-{A,B,C,D}-*.json` — ETH baseline scan outputs
- `scan-reports/flip/baseline/` — earlier exploratory scans (pre-baseline)
