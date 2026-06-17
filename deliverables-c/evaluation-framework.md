# Evaluation Framework

Two distinct protocols, deliberately kept separate in cost and rigor: the
**signal-scan protocol** (cheap, runs per-hypothesis, agent-autonomous) and
the **backtest protocol** (expensive, runs per-approved-candidate,
human-gated).

## 1. Signal-scan protocol

### Input format — resolved decision
**v1 scanner accepts SQL expressions over whitelisted Makalu dataset/column
names only.** Arbitrary Python feature transforms are explicitly deferred
until a sandboxing/review mechanism is designed (no execution timeout,
import restriction, or review step exists today, and none is being built in
this v1 slice). This is workable because the highest-priority taxonomy
families already have precomputed columns available via SQL: `BookState`
(imbalance, microprice), `BookChange` (pull/refill), `VolSpread`
(volatility/spread regime), `LeadLag` (cross-venue lag), and
`Feature_Mid`/`Feature_NearHFT` (combined tick-level feature tables). A
hypothesis that genuinely cannot be expressed in SQL against these tables is
parked in the ledger as `needs-tooling`, not silently routed around the
restriction.

A hypothesis spec resolves to:
- a **feature expression**: a SQL expression over one or more whitelisted
  Makalu datasets/columns (see list below).
- a **target**: one of (a) next-N-second mid/microprice return (computable
  from silver `MarketPrice` or bronze `BookState`), (b) spread-adjusted
  return (return minus half-spread, using `MarketPrice`'s spread column),
  or (c) an existing research-layer label (`TripleBarrierLabel`,
  `BuyLabel_tmp`) where the hypothesis claims to predict that label.
- a **window**: explicit exchange/symbol/date range(s), chosen to span more
  than one day and, where possible, more than one volatility regime
  (`VolSpread` bucket).

### Datasets read, in priority order
1. **Bronze** `BookState`, `BookChange`, `Trade`, `OrderBook` — `BookState`
   and `BookChange` are checked first since they already provide
   precomputed imbalance/microprice/pull-refill columns; raw `Trade`/
   `OrderBook` are used only when a hypothesis needs a column neither
   provides.
2. **Silver** `MarketPrice` (execution-aware bid/ask/mid/spread) and
   `TradeAggression` (rolling trade-flow metrics).
3. **Gold** `VolSpread` (volatility/spread regime conditioning) and
   `LeadLag` (cross-venue lag) — used directly rather than recomputed.
4. **Feature** `Feature_Mid` / `Feature_NearHFT` — checked for an existing
   combined column before assembling one from the layers above.
5. **Research** `TripleBarrierLabel`, `BuyLabel_tmp` — used as targets when
   applicable, but **flagged, not treated as stable ground truth**: the
   catalog documents this layer as "ephemeral and rebuildable, not
   canonical." Any scan using a research-layer target must record the
   dataset name and its build/regen identifier (e.g., `param_id` plus a
   generation timestamp or pipeline run id) in the scan report, so a later
   regeneration of that label with different parameters doesn't silently
   invalidate a recorded decision without anyone noticing.

### Metrics computed (a scan is incomplete without all of these)
- **Information coefficient (IC)** — rank correlation between signal and
  target, computed per-day and pooled.
- **Hit rate / sign accuracy** — % of cases where sign(signal) matches
  sign(target), with a binomial significance test against 50%.
- **Return by signal bucket** — target return averaged in quantile buckets
  of the signal, to catch non-monotonic or threshold effects an IC alone
  would miss.
- **Decay by horizon** — IC/hit-rate recomputed at multiple horizons (e.g.,
  1s/5s/30s/120s) to show whether the edge is real and short-lived or
  noise that "improves" arbitrarily with horizon choice.
- **Sample count** — raw N and effective N (accounting for autocorrelation;
  see below) at each horizon/bucket.
- **Autocorrelation of the signal** — high autocorrelation inflates
  apparent sample count and significance; must be reported and used to
  correct the significance estimate, not ignored.
- **Significance estimate** (p-value or equivalent) — computed using
  effective sample size, not raw row count.
- **Stability** — IC/hit-rate broken out by day, by symbol/exchange (if
  multiple), and by spread/volatility bucket (using family #5/#10 as the
  conditioning variable). A signal that only works in one bucket is reported
  as conditional, not as a general edge.

### Pass / promote-to-backtest thresholds (v1 defaults — tunable, but changes
must be logged and treated as a meta-decision per `workflow-strategy.md`)
- The primary horizon was declared before scanning, not chosen after seeing
  results.
- Scan window covers at least 5 distinct trading days; if fewer are
  available, the ledger records `needs-more-data` instead of a promotion
  decision.
- IC magnitude clears a minimum bar at the *primary* claimed horizon, on
  pooled data, after the autocorrelation correction (computed via block
  bootstrap, Newey-West/HAC standard errors, or an equivalent predeclared
  method — not naive IID significance).
- Sign is consistent (not flipping) across at least 3 of the scanned days,
  and does not flip in more than one major spread/volatility bucket.
- Effect survives in at least 2 of 3 spread/volatility buckets — full
  regime-fragility is a yellow flag, not an automatic reject, but must be
  disclosed.
- Raw sample count is at least 10,000 observations and effective sample
  count (after autocorrelation correction) is at least 1,000 observations
  for tick-level scans; lower-frequency scans require an explicit human
  waiver recorded in the ledger.
- Hit rate, where applicable, clears 50% by a margin large enough to plausibly
  survive fee + half-spread, given the family's typical capacity assumptions.
- Estimated edge exceeds a cost buffer: expected spread-adjusted return at
  the primary horizon must exceed fees + half-spread + slippage/latency
  allowance, with a 25% safety margin.
- Neighboring horizons and thresholds do not reveal a single-cell overfit:
  the declared effect must appear with the same sign in at least 2 adjacent
  horizon/threshold cells in the full grid, not only in the best cell.

### Immediate-rejection failure modes
- Signal value only exists at the exact horizon/threshold/bucket boundary
  the agent happened to pick first (smell of post-hoc tuning) — spec must
  show the agent tested neighboring horizons/thresholds and is reporting
  the full grid, not the best cell.
- Target was constructed using information that postdates the signal
  (look-ahead leakage) — verified by checking the cutoff timestamp logic in
  the spec.
- Effect vanishes once corrected for autocorrelation.
- Effect appears in only one day or one symbol with no plausible mechanism
  for why it would be regime-specific (vs. families like #5/#10 where
  regime-dependence is the expected, declared behavior).
- Execution latency at the primary horizon makes the signal untradeable
  regardless of statistical validity.
- Timestamp alignment or venue clock quality is insufficient to support the
  claimed horizon (most relevant to family #9, lead-lag).

### Scanner output (what a human or agent needs to judge pass/fail)
A single report per hypothesis id containing: the metrics above, the
parameter/horizon grid (not just the best cell), the stability breakdown,
and a one-line natural-language verdict citing which threshold(s) passed or
failed. This report is what gets attached to the ledger entry — see
`autonomous-readiness-plan.md` for the minimum schema.

## 2. Backtest protocol (Himalaya)

Only specs that passed the scan gate and received human sign-off reach this
stage.

### Splits
- **Train**: period used to set any strategy parameters.
- **Test**: held-out period, same regime family as train, parameters frozen.
- **Forward**: most recent period available, used once, after train/test
  look acceptable — a forward-test failure after a train/test pass is a
  strong signal of overfitting and must be recorded as such, not retried
  with new parameters on the same forward window.
- Minimum: 2 distinct periods beyond train (test + forward), ideally
  spanning more than one volatility regime per family #5/#10 conditioning.

### Required result fields (beyond PnL)
- Net PnL after fees, spread, slippage (Himalaya's simulator gateway
  already models execution; the agent must confirm fee/slippage parameters
  were not left at idealized defaults).
- Trade count and turnover.
- Win rate.
- Sharpe (or comparable risk-adjusted metric) per split.
- Max drawdown.
- Capacity/liquidity sensitivity: PnL at increasing order/execution size
  (Himalaya's `execution_size` partitioning already supports this).
- Parameter sensitivity: PnL surface across the parameter grid, not just
  the best point — a sharp isolated peak is treated as a red flag.
- Regime breakdown: performance split by the same spread/volatility buckets
  used in the scan, to check the backtest result is consistent with what the
  scan predicted.

### Anti-overfitting rules
- No promotion decision based on a single backtest run or a single period.
- No unbounded parameter search; the grid tested must be declared in the
  spec before running, and the full grid's results (not just the winner)
  must be recorded.
- Forward-test window is used once; re-running forward tests with new
  parameters after a failure is a new hypothesis (new ledger entry), not a
  retry of the old one.
- Realistic fills required: confirm Himalaya's simulator costs (fees,
  spread, slippage, latency) are configured, not defaulted to frictionless.
- Post-hoc label construction is disallowed — target/label must be defined
  in the spec before the scan, not adjusted after seeing results.

## 3. Decision memory

After backtest evaluation, the agent records in the ledger:
- decision: promote / refine (with what specific change) / retire,
- which specific diagnostic(s) drove the decision,
- the next suggested experiment, if any (e.g., "test same mechanism on a
  different symbol," "test with a tighter horizon").

This closes the loop into stage 9 of `workflow-strategy.md` and is what
makes the next research session start from accumulated evidence rather than
zero.
