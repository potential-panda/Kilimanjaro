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
python src/cli.py generate --queue scan-specs/queue --limit 5
                                             # propose new formula specs (stage B)
python src/cli.py loop --queue scan-specs/queue --start ... --end ...
                                             # autonomous scanner loop (stages 1-4)
```

Subcommand modules live in `src/command/` (populate.py, verify.py, scan.py,
loop.py, generate.py).

The **driver** of the 1-7 research loop lives in `src/kilimanjaro/driver/` (the
driver of the framework, not part of framework/core):

- `generate` (**Stage B**) enumerates economic formula families (momentum,
  order-flow, OBI, reversion, cross-market basis) over the markets that actually
  have data, dedups against every scan-spec on disk by formula signature, and
  writes new specs (fresh hypothesis ids) into the queue. Data-gated; execution
  sizes read from the data, never guessed.
- `loop` (**Stage A**) walks the queue and runs each spec through validate →
  dedup → scan → verdict → ledger, drafting a `REVIEW_PACKET.md` for rank-stable
  survivors and parking them at the human gate (never auto-promotes). Idempotent
  — a spec whose (content, window) fingerprint is already in the ledger is skipped.

Stages 5-7 (build qate strategy, Himalaya, prod) stay human-gated. Stage C (a
forever cadence wrapping generate+loop) is not built yet. See
`doc/requirements/agent-loop-design.md` for the state machine.

## Key modules

| module | purpose |
|--------|---------|
| `src/kilimanjaro/dataset/range_sar_store.py` | populate + verify ChartRangeSAR parquet store |
| `src/kilimanjaro/scan/flip_sar.py` | `scan()` and `verify()` for flip-anchored SAR signals |
| `src/kilimanjaro/framework/` | scan framework: `run_grid_split` (train/holdout + rank stability — the default way to run any grid), Gate protocol + RollingDrawdownGate, block-bootstrap p-value |
| `src/kilimanjaro/driver/` | the research-loop driver (not core). Stage A: `loop.run_once`/`process_spec` (validate→dedup→scan→verdict→ledger→review-packet); canonical `scan_runner.build_combos`. Stage B: `generate.propose_specs` (formula families × data-viable markets, deduped by signature). |
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

## Execution cost policy (base rule)

Fixed policy for **every** scan — see `src/kilimanjaro/framework/costs.py`:

1. **Scans execute at the actual bid/ask, never mid.** Every return calc
   (`BetweenSignalReturn`, `EpisodeReturn`, `FixedHorizonReturn`) enters at the
   aggressive side (ask for BUY, bid for SELL) and exits at the passive side.
   The **bid-ask spread is therefore already paid inside every scan return.**
2. **Do NOT estimate or add a separate spread cost.** Charging the spread on top
   of a bid/ask fill double-counts it. (There is no `estimate_round_trip_cost_bps`
   anymore — it was removed for this reason.)
3. **The only added cost is latency slippage: `ROUND_TRIP_COST_BPS = 1.0`**
   (0.5 bps/order × 2 orders per round-trip). The 0.5 bps/order is the **realized
   average from a full year of real trading** — volatile-window spikes are
   already inside that average, so it is not an under-estimate. This is the
   single `costs_bps` knob and the default in `run_grid` / `run_grid_split`.

**This deliberately matches Himalaya**, which applies the same flat 0.5 bps/order
on top of bid/ask execution. Same execution price source (bid/ask right after the
signal) + same flat cost ⇒ the scan is designed to reproduce Himalaya closely.

Trust hierarchy: the **scan** aims to be ~95% of real; **Himalaya** is ~99% (it
runs the same codebase as live trading) and is the **final examination of
survivors** — the last line before a promotion decision. The scan filters
cheaply and honestly; Himalaya confirms the few that pass.

## Verification philosophy

The scan is **not** expected to be a 100% replica of the backtest. The goal is
to confirm the scan reflects the **same signal direction and magnitude trend**
as the backtest, so it can be used to generate entry signals for live trading.

**Calibrate per mechanism, not per formula** (full write-up: `doc/calibration.md`).
The formula is only an entry trigger; scan↔Himalaya fidelity lives in the
*mechanism* = entry style + return calc + execution model. Verify a mechanism
once (a few param sets, varied entry conditions), and every formula using it
inherits the trust — a handful of calibrations, not one per idea. The acceptance
bar scales with tightness: a close mirror (flip + between-signal) demands
near-exact agreement; a looser mechanism (episode) demands only direction +
magnitude-order + param-ranking agreement. Calibration bounds model risk, not
eliminates it — re-check when the mechanism or simulator changes; own the rest.
Currently only flip + between-signal is calibrated (ETH). Expression +
fixed-horizon and expression + episode are **trend-only** until verified.

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
