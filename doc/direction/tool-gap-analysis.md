# Tool Gap Analysis

## What already exists (verified against repo structure/docs)

**Makalu** — the catalog (`doc/catalog.yaml`) defines five layers, not three.
Corrected inventory:

- **Bronze** (`/bronze/`, raw or near-raw, no analytics):
  - `Trade` — raw trade stream.
  - `OrderBook` — full 20-level snapshots.
  - `BookChange` — per-update depth deltas, pull/refill rates by depth
    level. Derived from `OrderBook` but classified bronze, not silver.
  - `BookState` — **already computes spread, volume imbalances, and
    microprice at multiple depth levels, per snapshot.** This directly
    covers taxonomy families #1 (order book imbalance) and #2 (microprice)
    as precomputed queryable columns — an agent does not need to derive
    these from raw `OrderBook` levels itself.
- **Silver** (`/silver/`, point-in-time derived metrics, no rolling
  windows): `MarketPrice` (execution-walked bid/ask/mid/spread by
  `execution_size`), `TradeAggression` (rolling-window trade flow metrics
  suffixed by window, e.g. `buy_qty_5s`).
- **Gold** (`/gold/`, window-based/statistical signals): `Chart_CANDLE` /
  `Chart_HEIKIN_ASHI` / `Chart_RANGE` bars, `LeadLag` (cross-market lead-lag
  correlation/optimal-lag/stability — directly relevant to taxonomy family
  #9), `VolSpread` (EMA-smoothed realized vol/spread — the conditioning
  variable families #5/#10 need, already built, not something to compute
  ad hoc).
- **Feature** (`/feature/`, ML-ready tick-level tables): `Feature_Mid` and
  `Feature_NearHFT` — **already combine trade aggression, book state, book
  change, execution price, trend, volatility, and cross-features into one
  queryable table per exchange/symbol/date.** This is a much stronger
  starting point for the scanner than assembling features from scratch.
- **Research** (`/research/`, **"Experiment outputs. Ephemeral and
  rebuildable. Not canonical."** — catalog's own description):
  `TripleBarrierLabel`, `TripleBarrierLabel_tmp`, `BuyLabel_tmp` (name
  literally flags it as temporary), `BacktestPnL`. These are useful as scan
  targets but must be treated as **versioned, regenerable experiment
  artifacts, not stable ground truth** — see ledger schema note below.
- A SQL query CLI (`src/cli.py query`) with documented rules, partition
  discovery, and JSON/table output — already a usable, cheap data access
  pattern for an agent, across all five layers.
- `feature_engineering/` source modules (trade aggression, order book
  state, book change, execution price, interactions, volatility, trend) —
  these are the code that produces the bronze/silver/feature columns above,
  not a separate asset an agent needs to invoke directly.
- Backtest orchestration hooks into Himalaya, with `BacktestPnL` itself
  living in the research layer as one record per fill.

**Himalaya**
- Backtest engine replaying stored data through a simulator gateway.
- Parameter grid support, documented metrics (`doc/metrics.md`): TRADE,
  MARKET_PRICE, BAR, ORDER, and presumably PNL measurements with InfluxDB
  tags including `param_set_id`.
- Results land in InfluxDB, Parquet, and run logs.

**potential-panda-core**
- Strategy base/variant contracts (`qate/trading/strategy.py`,
  `qate/strategy/*` examples: arb, vineyard, pisces, corvus).
- Simulator/exchange abstractions, gateway, order/PnL tracking.
- This is sufficient as the *target* contract for an approved hypothesis to
  eventually compile into — no new abstraction needed for that step.

## What is missing

1. **Signal scanner** — there is no existing tool that takes a feature
   expression and a target, computes IC/hit-rate/decay/significance/
   stability, and reports pass/fail against thresholds, independent of
   Himalaya. Today the only way to know if a feature has value is to wire it
   into a full strategy and run a backtest — too expensive to use as a
   pre-filter. Note: because `BookState`, `VolSpread`, `LeadLag`, and
   `Feature_Mid`/`Feature_NearHFT` already exist as queryable columns, the
   v1 scanner can be scoped to **SQL expression + whitelisted Makalu
   dataset/column names only** — it does not need an arbitrary
   Python-transform execution path to cover the brief's priority taxonomy
   families. See the resolved decision in `evaluation-framework.md`.
2. **Research ledger** — there is no persistent, queryable record of
   hypotheses, scan results, decisions, and rejection reasons. Without it,
   every agent session restarts from zero and can re-propose ideas that were
   already rejected. The ledger schema must also record, for any entry that
   used a research-layer label (`TripleBarrierLabel*`, `BuyLabel_tmp`) or
   feature table, the dataset name and its build/regen identifier — the
   catalog marks this layer "ephemeral and rebuildable, not canonical," so a
   scan result against it is only reproducible if the ledger pins which
   version produced it.
3. **Hypothesis spec format** — no structured schema exists for what a
   hypothesis must declare (mechanism, horizon, costs, falsification test)
   before it's allowed to consume scan or backtest resources.
4. **Feature/data discoverability for an agent** — `catalog.yaml` and
   `doc/querying.md` are good but human-oriented; an agent needs a fast way
   to check "do I have N days of OrderBook for GMO/BTC_JPY in this window"
   without trial-and-error SQL.
5. **Promotion/decision protocol** — no documented rule set for "scan metrics
   X, Y, Z must clear thresholds before a Himalaya run is authorized."

## Gaps that block v1 autonomy

- **Signal scanner** — blocking. Without it, every hypothesis either skips
  pre-screening (defeats the brief's purpose) or requires a full Himalaya
  run to get any feedback (too slow/expensive to run repeatedly).
- **Research ledger** — blocking. Without it, "remember" and "don't
  rediscover rejected ideas" — both explicit goals in the brief — are
  impossible. Even a minimal flat-file ledger is required before declaring
  any version of this loop autonomous.
- **Hypothesis spec format** — blocking, but cheap to build (a schema/template,
  not a service). Needed so scanner input and ledger entries are structured
  rather than free text.

## Gaps that can wait

- **Feature/data discoverability tool** — Makalu's existing catalog + CLI is
  workable for v1 if the agent is instructed to always probe partitions
  before querying (already documented in `doc/querying.md`). A purpose-built
  "data availability report" is a nice-to-have, not a blocker.
- **New feature extraction / additional silver-gold metrics** — existing
  feature_engineering modules (order book state, book change, trade
  aggression, volatility) already cover most of the brief's priority
  taxonomy (imbalance, depth pull/refill, aggression bursts, volatility
  regimes). New metrics can be added per-hypothesis as scans reveal a need,
  not pre-built speculatively.
- **Automated promotion-to-backtest gate** — v1 keeps this as a human
  decision informed by documented thresholds; full automation is deferred.
- **Strategy code generation** — explicitly out of scope per the brief's
  initial opinion; revisit only after the ledger shows a backlog of
  approved-but-unimplemented hypotheses.

## Direct answers to the brief's tool questions

- **Is potential-panda-core enough to express generated strategy ideas?**
  Yes for the target contract. A discovered strategy should eventually
  become a `qate.trading.strategy` variant with declared parameters —
  existing examples (arb/vineyard/pisces/corvus) are adequate precedent. No
  new abstraction is needed until a hypothesis needs an order/signal type
  the current contract can't express (e.g., cross-venue lead-lag may need
  multi-feed wiring — flag if/when it comes up, don't build speculatively).
- **Is existing silver/gold (and feature) data sufficient?** Yes, for the
  brief's priority families. `BookState` (bronze) already provides
  imbalance and microprice columns directly (families #1, #2).
  `BookChange` (bronze) covers depth pull/refill asymmetry (family #4).
  `VolSpread` (gold) covers the spread/volatility conditioning variable
  (families #5, #10). `LeadLag` (gold) covers cross-venue lead-lag (family
  #9). `Feature_Mid`/`Feature_NearHFT` (feature layer) already assemble
  most of these into single queryable tables. No new dataset is required to
  start scanning the priority taxonomy — this also means the scanner does
  not need an arbitrary-code feature-transform path for v1 (see below).
- **Do we need a feature catalog, availability report, or query helper?**
  The catalog already exists (`catalog.yaml`) and is more complete than
  initially assumed (5 layers, including ready-made feature tables); a thin
  availability helper on top of it is a fast-follow, not a v1 blocker.
- **Can Makalu support a fast signal scanner over bronze/silver/gold?**
  Yes structurally — the SQL CLI plus partitioned Parquet across all five
  layers is a reasonable substrate for pulling a window of data into memory
  and computing scan statistics, and the existing `BookState`/`VolSpread`/
  `LeadLag`/`Feature_Mid` columns mean most v1 scans need only a SQL
  expression, not a code-execution path. The scanner itself does not exist
  yet and is the single biggest gap.
- **Is Himalaya enough for strategy evaluation?** Yes for the *backtest*
  step. It is not, and should not be, the tool used for pre-screening — it
  has no fast feature-only evaluation path, which is exactly why a separate
  scanner is required.
