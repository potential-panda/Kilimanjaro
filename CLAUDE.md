# CLAUDE.md

## Git permissions

The user has given standing authorization to commit and push to this
repo's `main` branch freely, without asking for confirmation first. They
feel safe managing the codebase and explicitly said not to worry about
breaking it. This overrides the general default of confirming before
`git push`. Still use judgment for genuinely destructive operations
(force-push, history rewriting, deleting branches) — this authorization
covers normal commit/push, not those.

## CLI entry point

`src/cli.py` is the single entry point. All old one-off scripts under
`tools/` that duplicated this functionality have been removed.

```
python src/cli.py populate chart-range-sar   # precompute SAR flip events
python src/cli.py verify  chart-range-sar    # compare bars/SAR vs backtest InfluxDB BAR
python src/cli.py scan                       # flip-anchored signal scan
python src/cli.py verify-scan                # compare scan vs backtest ORDER + EVAL
python src/cli.py loop --queue scan-specs/queue --start ... --end ...
                                             # autonomous scanner loop (stages 1-4)
```

Subcommand modules live in `src/command/` (populate.py, verify.py, scan.py, loop.py).

`loop` is the **driver** (Stage A of the 1-7 research loop): it walks a queue of
scan-specs and runs each through validate → dedup → scan → verdict → ledger,
drafting a `REVIEW_PACKET.md` for rank-stable survivors and parking them at the
human gate (never auto-promotes). Idempotent — a spec whose (content, window)
fingerprint is already in the ledger is skipped. Logic lives in
`src/kilimanjaro/driver/` (the driver of the framework, not part of framework/core).
See `doc/requirements/agent-loop-design.md` for the state machine.

## Key modules

| module | purpose |
|--------|---------|
| `src/kilimanjaro/dataset/range_sar_store.py` | populate + verify ChartRangeSAR parquet store |
| `src/kilimanjaro/scan/flip_sar.py` | `scan()` and `verify()` for flip-anchored SAR signals |
| `src/kilimanjaro/framework/` | scan framework: `run_grid_split` (train/holdout + rank stability — the default way to run any grid), Gate protocol + RollingDrawdownGate, block-bootstrap p-value |
| `src/kilimanjaro/driver/` | autonomous scanner loop (Stage A): `run_once`/`process_spec` drive validate→dedup→scan→verdict→ledger→review-packet over a queue; canonical `scan_runner.build_combos`. The driver of the framework, not core. |
| `src/kilimanjaro/signal/expression.py` | `ExpressionSignal`: signals as AST-validated formulas over the feature frame |
| `src/kilimanjaro/data/feature_frame.py` | multi-market 1s feature frame (mid/spread, OBI, TradeAggression) |
| `src/kilimanjaro/data/price_cache.py` | per-window target-price cache — full-year flip grid runs in ~40s (was ~35min); keep return calcs on this, never per-combo parquet scans |
| `src/kilimanjaro/op/series.py` | causal op library (`ema`, `zscore`, `lag`, `ret`, …) — causality is test-enforced |

Grid tools (`tools/grid_scan.py` flip, `tools/grid_scan_horizon.py` OBI-age,
`tools/grid_scan_expr.py` expression specs) all run through `run_grid_split`
and record one `scan_attached` ledger event per run (`--no-ledger` to skip,
`--hypothesis-id` to re-attribute). Selection criterion is rank stability
across the train/holdout split, never in-sample p-values.

## Data paths

| store | path |
|-------|------|
| ChartRangeSAR (features) | `/panda/makalu-data/feature/dataset=ChartRangeSAR/exchange=.../symbol=.../box_size_rate=.../step=.../date=*.parquet` |
| MarketPrice (raw) | `/panda/makalu-data/market/dataset=MarketPrice/exchange=.../symbol=.../size=.../date=*.parquet` |
| InfluxDB backtest | `http://localhost:8086` token=`panda-auth-token` org=`panda-org` |

`warmup_days=90` (default) must be used for `populate()` and `verify()` so
SAR state converges before the target window — backtest runs from Jan 1, cold
start without warmup diverges ~2% of bars.

## Verification philosophy

The scan is **not** expected to be a 100% replica of the backtest. The goal is
to confirm the scan reflects the **same signal direction and magnitude trend**
as the backtest, so it can be used to generate entry signals for live trading.

Known structural differences (not bugs):

| difference | reason |
|------------|--------|
| Entry gap (scan_only) | Backtest has rolling DD gate; scan has none |
| Entry gap (order_only) | Scan over-blocks non-maintenance Saturdays |
| PnL diff on some matched pairs | Scan exits at next overall SAR flip; backtest may hold through maintenance |
| Post-maintenance afternoon flips | Backtest re-entry holdout extends beyond 11:59 (exact rule unknown) |

`verify-scan` is a **sanity check**, not an exact reproduction. A clean verify
means: entry prices exact (0¥ diff), order timing exact (0.000s), and PnL trend
consistent (≥95% exact, mean diff < 1¥ for the matched subset).

## Verification status (as of 2026-07)

Verified `BINANCE/ETH_USDT box=0.003 step=0.04` May 2026 against backtest
bucket `backtest_QASIM_TARIQ` param `GMO_ETH_JPY_0.3__nofilter_300_40_20_200_21600_10`:

- **Bar/SAR**: 2593/2593 bars, all 7 fields 100% exact
- **Order price**: 0¥ diff — GMO bid/ask at first MarketPrice tick after flip
  is the same record the backtest uses as EXEC_PRICE
- **Order time**: 0.000s diff — backtest fill timestamp = that same MarketPrice ts
- **PnL**: 98.3% exact (with `--block-sat-jst-hours 8,9,10,11`), mean diff +0.44¥

### Gap orders (4 remaining with maintenance block)

Remaining 4 gaps are explained and expected:

- 3 × **scan_only** (May 9 16:08, May 23 16:07, May 30 13:26): post-maintenance
  flips the backtest also skipped — re-entry holdout extends past 11:59
- 1 × **order_only** (May 16 08:17): GMO had no maintenance that Saturday,
  backtest traded but scan over-blocked it

GMO exchange maintenance is **not every Saturday** — only specific Saturdays.
In May 2026: May 9, 23, 30 had maintenance; May 16 did not.

### param_set_id format

`GMO_ETH_JPY_0.3__{schedule}_{box_int}_{step_int}_{accel_int}_{max_int}_{window_sec}_{threshold}`

where `box_int = box_size_rate × 100000`, `step_int = step × 1000`.
For `box=0.003, step=0.04`: `GMO_ETH_JPY_0.3__nofilter_300_40_20_200_21600_10`
