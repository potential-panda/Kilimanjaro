# Hypothesis Taxonomy (v1)

Each family below states: required data, expected edge mechanism, an example
hypothesis, and what would make the agent reject it outright (before even
running a scan). All families are testable against existing Makalu bronze
(`Trade`, `OrderBook`) and silver (`BookChange`, feature_engineering outputs)
without new pipelines.

## Addendum: cross-market sourcing and horizon selection (2026-06-18)

Added after the market-characterization layer (`markets/`) was built, based
on direct user guidance — applies to every family below, not just #1 and #9:

- **BINANCE is the default feature source for predicting any JP market with
  a confirmed lead-lag leader — always, not just "consider it."** Per direct
  user instruction (2026-06-18, repeated for emphasis): BINANCE leads GMO
  BTC_JPY/ETH_JPY/XRP_JPY 81-97% of the time across every 30-minute JST
  bucket (`markets/waves/leadlag_BINANCE_GMO_*.csv`), so in theory and in
  practice its own order-book/flow state is more informative than the
  follower's lagged copy of it. Compute the signal on BINANCE; measure the
  target return on the market actually traded (GMO). The only exceptions
  are families inherently about the target market's own local execution
  mechanics, where BINANCE has no analogue: queue depletion (#8), adverse
  selection (#12), and limit-order fill probability
  (`tools/fill_probability.py`). See `requirements/hypothesis-contract.md`'s
  "Cross-Market Signal Sourcing" section for the schema-level rule
  (`cross_market_rationale`, now required to justify *not* using BINANCE,
  not just to justify using it).
- **The tested horizon must be justified by the volatility-vs-spread wave**
  (`markets/waves/<exchange>_<symbol>.csv`, `tools/volatility_wave.py`), not
  picked by convention. A horizon where price rarely clears the spread on
  the target market cannot be profitable regardless of what the signal
  predicts — e.g. 5s is too short for GMO BTC_JPY at most times of day; ~120s
  reaches a much more reasonable spread-clearing probability. This applies
  per time-of-day bucket, not just as a single global default.

## 1. Order book imbalance
- **Data**: bronze `BookState` — already computes volume imbalance at
  multiple depth levels per snapshot; no need to derive from raw `OrderBook`
  levels. Raw `OrderBook` remains available for custom depth cuts.
- **Mechanism**: persistent bid/ask depth imbalance reflects near-term
  net order flow pressure that has not yet been arbitraged into price.
- **Example**: `BookState`'s imbalance column at depth level 3 predicts sign
  of mid-price move over next 1–5s.
- **Reject if**: imbalance only "works" on one symbol/day, or effect
  disappears once spread/fee-equivalent move is required (i.e., it predicts
  direction but not enough magnitude to clear costs).
- **Cross-market note**: if the target market is a known follower (see
  addendum above), compute the imbalance on the leader's `OrderBook`, not
  the follower's — e.g. BINANCE imbalance predicting GMO BTC_JPY return, not
  GMO's own imbalance. Also prefer a horizon long enough to clear the
  target market's spread per its volatility wave; 1-5s is too short for
  GMO BTC_JPY at most times of day.

## 2. Weighted imbalance and microprice
- **Data**: bronze `BookState` — already computes microprice at multiple
  depth levels per snapshot.
- **Mechanism**: microprice (depth-weighted mid) is a less noisy fair-value
  estimate than mid-price; deviations of trade price from microprice signal
  short-term mean reversion or informed-flow continuation.
- **Example**: `BookState` microprice − last trade price predicts
  next-N-second return.
- **Reject if**: indistinguishable from plain mid-price reversion (no
  incremental value over the simplest baseline) — compare directly against
  silver `MarketPrice` mid as the baseline.

## 3. Top-of-book reset behavior
- **Data**: bronze `OrderBook`, `BookChange` (also bronze layer — a per-update
  diff of `OrderBook`, not silver; corrected from earlier draft).
- **Mechanism**: after a top-of-book level is fully consumed, the speed and
  side of the reset reveals whether liquidity providers are retreating
  (informed flow) or replenishing (uninformed/noise).
- **Example**: asymmetric reset speed (bid resets faster than ask after a
  sell-side sweep) predicts continuation.
- **Reject if**: reset speed is dominated by exchange-specific
  matching/latency artifacts rather than economic behavior — check this
  before trusting the signal.

## 4. Depth pull/refill asymmetry
- **Data**: bronze `BookChange` (already computes pull/refill rates by depth
  level).
- **Mechanism**: faster passive cancellation (pulls) on one side than
  refill on the other indicates liquidity providers pricing in adverse
  selection risk ahead of a move.
- **Example**: pull-rate asymmetry over trailing 2s predicts next 5s return
  direction.
- **Reject if**: pull/refill asymmetry only correlates with overall
  volatility (i.e., it's a volatility proxy, not a directional signal) —
  check this confound explicitly in the scan.

## 5. Spread widening/narrowing regimes
- **Data**: gold `VolSpread` — already provides EMA-smoothed realized
  volatility and spread resampled to 1s, purpose-built as a conditioning
  variable per the catalog's own description.
- **Mechanism**: spread regime is a proxy for realized/expected volatility
  and liquidity provider risk appetite; regime shifts often precede or
  accompany directional moves and change the profitability of other
  signals.
- **Example**: used primarily as a **conditioning variable** for other
  hypotheses (e.g., "imbalance signal only works when spread regime is
  narrow") rather than a standalone directional signal.
- **Reject if**: treated as a standalone PnL source without conditioning
  another mechanism — spread regime alone is a weak edge candidate.

## 6. Trade aggression bursts
- **Data**: bronze `Trade`, feature_engineering `trade_aggression.py`.
- **Mechanism**: a burst of same-side aggressive (taker) trades exhausts
  passive liquidity and signals short-term continuation until liquidity
  refills.
- **Example**: rolling count/notional of same-side aggressive trades in
  last 2s predicts next 3–10s continuation.
- **Reject if**: the burst itself was already followed by the price move
  used to detect it (look-ahead/labeling leakage) — falsification test must
  use a hard cutoff timestamp.

## 7. Buy/sell pressure exhaustion
- **Data**: bronze `Trade`, `trade_aggression.py`.
- **Mechanism**: after sustained one-sided pressure, marginal aggressive
  orders get smaller/less frequent, indicating exhaustion and reversion.
- **Example**: deceleration of aggressive trade rate after a directional
  run predicts short-term reversal.
- **Reject if**: only observed at one specific run length threshold tuned
  post-hoc (parameter overfit) — must show stability across a *range* of
  thresholds.

## 8. Queue depletion before price movement
- **Data**: bronze `OrderBook`, `BookChange`.
- **Mechanism**: queue position at best level depleting faster than typical
  (without a matching trade print) suggests hidden/iceberg activity or
  imminent price change.
- **Example**: depletion rate at best bid without corresponding trade volume
  predicts downward move.
- **Reject if**: cannot distinguish depletion from simple cancel-and-replace
  noise at the venue — needs explicit handling in spec, not assumed away.

## 9. Lead-lag between venues or execution sizes
- **Data**: gold `LeadLag` — already computes cross-market correlation
  structure, optimal lag, and lag stability between two markets
  (`market_a` leads `market_b`), built from silver `MarketPrice`. Currently
  populated only for `market_a=BINANCE:*_USDT` / `market_b=GMO:*_JPY`
  (btc/eth/xrp, ~33 days) — confirmed 2026-06-18, see `tools/lead_lag_wave.py`.
- **Mechanism**: price discovery happens first on a more liquid/faster venue
  and propagates with a lag to others; the lag is exploitable if it's larger
  than execution latency + costs.
- **Example**: BINANCE BTC_USDT mid-price move predicts GMO BTC_JPY
  mid-price move over the next K ms — confirmed on real data: BINANCE leads
  81-97% of the time across every 30-minute JST bucket (corrected from an
  earlier draft example, "GMO predicts COINCHECK," which named the wrong
  direction and a pair with no populated lead-lag data at all).
- **Reject if**: lag is smaller than realistic execution latency for the
  lagging venue (no exploitable mechanism) or clock skew between sources is
  not verified.
- **Also see**: this family's leader/follower relationship is the basis for
  the cross-market sourcing rule in the addendum above — it's not limited to
  predicting the follower's *price*, it also governs which market other
  families (e.g. #1 imbalance) should source their *feature* from when the
  target is a follower market.

## 10. Volatility/spread regime filters
- **Data**: gold `VolSpread` (same dataset as family #5).
- **Mechanism**: same as #5 — primarily a conditioning/regime layer, not a
  standalone hypothesis. Used to test whether other signals are
  regime-dependent (a required diagnostic, not just an optional one).
- **Reject if**: proposed as an independent PnL source rather than a filter.

## 11. Liquidity shock mean reversion
- **Data**: bronze `Trade`, `OrderBook`.
- **Mechanism**: a large/aggressive trade that moves price beyond what
  resulting depth changes justify creates a temporary dislocation that
  reverts as liquidity providers re-quote.
- **Example**: price impact per unit size above a trailing baseline predicts
  short-horizon reversion.
- **Reject if**: reversion window is shorter than realistic order
  round-trip latency, making it untradeable regardless of statistical
  validity — flag as "scan-passed but infeasible" rather than discarding the
  finding.

## 12. Adverse selection avoidance
- **Data**: bronze `OrderBook`, `Trade`, `BookChange`.
- **Mechanism**: not a standalone alpha signal but a feature for *execution
  quality* — detecting when posting passively is likely to get picked off
  by informed flow, so the spec is about when *not* to trade rather than
  predicting direction.
- **Example**: predict realized adverse selection cost of a hypothetical
  passive order conditional on current book/flow state.
- **Reject if**: framed as a directional alpha signal — this family belongs
  in execution-cost modeling, evaluated differently (cost avoided, not
  return predicted). Keep it tagged separately in the ledger so it isn't
  compared apples-to-apples against directional hypotheses.

## Note on feature tables and labels

The feature-layer tables `Feature_Mid` and `Feature_NearHFT` already combine
trade aggression, book state, book change, execution price, trend, and
volatility into one queryable table per exchange/symbol/date. For any
family above, check `Feature_Mid`/`Feature_NearHFT` for an existing column
before deriving the feature from bronze — it's usually already there.

Research-layer datasets (`TripleBarrierLabel`, `TripleBarrierLabel_tmp`,
`BuyLabel_tmp`) are usable as scan targets but are documented in the catalog
as "ephemeral and rebuildable, not canonical." Any scan or ledger entry that
uses one of these as a target must record which build/regen of that dataset
was used — see `evaluation-framework.md` and the ledger schema in
`../../schemas/ledger_event.schema.json`.

## Notes on scope

- Families #5 and #10 (spread/volatility regime) and #12 (adverse selection)
  are intentionally framed as *conditioning or cost* layers, not standalone
  directional hypotheses — this matters for how the scanner and ledger score
  them (see `evaluation-framework.md`).
- Cross-venue families (#9) require verifying clock alignment across data
  sources before any scan is trusted; this is a data-quality precondition,
  not a scan metric.
- The agent may propose families outside this list, but must justify, in the
  spec, why existing bronze/silver data can test the mechanism — this is a
  hard requirement, not a suggestion, to keep the hypothesis space
  data-grounded rather than speculative.
