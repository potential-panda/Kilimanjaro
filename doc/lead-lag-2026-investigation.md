# Lead-Lag Investigation: Did BINANCE Stop Leading GMO in March 2026?

**Date:** 2026-07-13
**Trigger:** Train/holdout grid scan (`results/grid_scan/flip/*_20260713_*`)
showed the flip-scan edge collapsing out-of-sample in 2026-03..06 (0/110 ETH,
0/105 XRP, 1/25 BTC candidates survive). Hypothesis to test: BINANCE is not
leading GMO as strongly as in 2025.

**Verdict: the lead-lag is intact. The apparent deterioration was a data
artifact. The hypothesis is disconfirmed — lead decay is not what killed the
Mar–Jun 2026 flip edge.**

## 1. Makalu LeadLag verification

Makalu's gold `LeadLag` dataset (EWLL: EW correlation over 100ms returns,
lag search −500..+2000ms, MID config = 60–300s windows / 60s stride) was
cross-checked against an independent recomputation
(`tools/lead_lag_check.py`: plain Pearson cross-correlation on 100ms and 1s
grids, no EW weighting, no windowing) over 2025-07-01..2026-06-29.

Agreement on everything that matters:

| metric | Makalu EWLL (120s/60s) | independent |
|---|---|---|
| direction | BINANCE leads (lead share ~90–95% of windows) | BINANCE leads (~99–100% of days) |
| optimal lag | 600–700 ms | 600–1000 ms |
| stability over the year | stable | stable |

Sign convention confirmed in `Makalu/src/lead_lag/ewll.py`: correlation is
`corr(target_t, leader_{t−k})`, so positive `ll_opt_lag_ms` = BINANCE leads.
`ll_opt_lag_ms` is **normalized by the 2000ms max lag** (0.35 = 700ms).

**Caveat (see §3): LeadLag is computed from silver MarketPrice, which is
contaminated by BINANCE order-book glitches in specific months. Median
aggregates over its windows remain usable; window-level values in glitch
periods are not trustworthy.**

## 2. 2025 vs 2026-03+ comparison (glitch-robust)

Median daily max cross-correlation at 1s grid, winsorized (99.9% clip):

| period | ETH | XRP |
|---|---|---|
| 2025-07 .. 2026-02 | 0.48 | 0.53 |
| 2026-03 .. 2026-06 | 0.40* | 0.41 |

\* ETH's 2026-03/05/06 values are still depressed by glitch contamination
that winsorizing cannot fully remove. With a 5s median filter on the mid
(removes the glitches completely), sample days show **no degradation at
all**: 2025-08 ≈ 0.61–0.63, 2026-05 ≈ 0.60–0.61, 2026-06 ≈ 0.66–0.71.

- **ETH: lead unchanged.** Optimal lag still ~1s, lead share ~100% of days
  all year, glitch-filtered correlation in May/Jun 2026 equal to or higher
  than 2025.
- **XRP: mildly weaker but intact.** Max corr down ~20% (0.53→0.41),
  positive-lag asymmetry 0.47→0.36, lead share still ~99%, lag unchanged.
  Context: BINANCE XRP tick activity fell ~25% over the same period
  (~800k→~600k MarketPrice ticks/day).

## 3. Root cause of the apparent ETH collapse: BINANCE OrderBook glitches

Raw (un-winsorized) daily correlation for ETH swings 10–20x between months
(0.03 in 2026-03/05/06 vs 0.47–0.61 in 2026-01/02/04). The cause is in
**bronze**: `OrderBook/exchange=BINANCE/symbol=ETH_USDT` occasionally emits
degenerate snapshots — book collapses to 1–3 levels with far-away stale
prices (e.g. one ask at 2563.53 while the true market is 2009.55, verified
2026-03-03 ~00:52 UTC). The silver MarketPrice walk happily prices that book,
producing a ±200–1100 bps mid spike that reverts on the next snapshot.

Spike rate (1s bars with >20 bps move that immediately reverts, BINANCE
ETH_USDT MarketPrice):

| period | glitches/day |
|---|---|
| 2025-07 .. 2025-12 | 9–26 |
| 2026-01, 02, 04 | ~0 |
| 2026-03, 05, 06 | **56–73** |

GMO ETH_JPY: 0/day all year. The spikes inflate BINANCE's return σ up to
~10x, which mechanically crushes any Pearson-style correlation — this fully
explains the "low-corr months", including why high-vol March measured *low*
correlation.

### Why the month pattern (traced 2026-07-13, after the initial write-up)

The per-month pattern above is **not** collector quality varying over time.
Bronze contains degenerate snapshots at a broadly similar rate in every
month and every symbol (~200–700 insane top-of-book ticks/day on ETH, XRP,
BTC alike). What differs is which **silver MarketPrice partitions were built
before vs after Makalu commit `24ecb0d` (2026-06-30, "Remove spread filter
from PopulateMarketPriceStep")**:

- Built before 2026-06-30 (Jan/Feb built in Feb; Apr and part of May built
  2026-06-19): the old `max_spread_bps=10.0` filter incidentally dropped the
  degenerate snapshots → clean.
- Rebuilt 2026-07-03 (Jul–Dec 2025, Mar, most of May, Jun): no filter →
  glitches pass through into MarketPrice.

The filter was removed for a good reason — it silently dropped ~7% of
snapshots, disproportionately at SAR flip moments, causing a systematic
~0.40 bps scan-vs-backtest gap. The blanket spread filter should not come
back; what's needed is a **targeted degenerate-snapshot filter** (e.g. drop
books with fewer than N populated levels, or with a top-of-book mid that
deviates hundreds of bps from adjacent snapshots and immediately reverts).

Consequence for LeadLag: gold LeadLag partitions computed from
post-rebuild MarketPrice (including the XRP sampled-day populate started
2026-07-13) inherit the contamination in the affected months. After a
MarketPrice fix + rebuild, LeadLag for those months should be re-populated.

**Downstream impact:**

- `ChartRangeSAR` (the flip signal) is built from bronze **Trade** ticks,
  not OrderBook/MarketPrice — real executions don't contain the phantom
  quotes, so the flip scan itself is not contaminated.
- Anything computed from BINANCE ETH_USDT **MarketPrice** in the affected
  months is suspect: Makalu LeadLag window values, VolSpread-style vol
  metrics, naive realized-vol estimates. (An earlier read of "March 2026 was
  the highest-vol ETH month" was exactly this artifact.)
- Worth fixing in Makalu: either filter degenerate snapshots when building
  MarketPrice (e.g. reject books with < N levels or spread > sanity bound)
  or repair bronze ingestion.

## 4. Implication for the flip-edge collapse

The Mar–Jun 2026 out-of-sample failure is **not** explained by lead-lag
decay. Remaining candidate explanations, in rough order of prior:

1. Overfit train-window selection (the default explanation; the grid had 245
   combos and the filters allowed ~12 false positives by chance).
2. Regime change in trend persistence — the SAR between-flip return depends
   on trends continuing, not on the lead existing; a chop-ier market kills
   it with the lead fully intact. XRP activity down ~25% points this way.
3. XRP-specific: the ~20% lead weakening may contribute at the margin, but
   its per-trade edge was already near the 1 bps cost floor.

## Artifacts

- `tools/lead_lag_check.py` — independent daily cross-correlation tool
- `results/lead_lag/daily_raw.csv` — raw daily metrics (shows the artifact)
- `results/lead_lag/daily_winsorized.csv` — winsorized daily metrics
- Makalu gold LeadLag: ETH pair fully populated 2025-07..2026-06; XRP pair
  populated for sampled days (1st/11th/21st of each month), kicked off
  2026-07-13
