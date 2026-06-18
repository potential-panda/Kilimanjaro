# Hypothesis Registry And Spec Contract

## Purpose

The hypothesis registry is Kilimanjaro's durable research memory. It prevents repeated work, records failed ideas, and supplies structured inputs for scanner and backtest handoff.

## Hypothesis Ids

Format:

- parent hypothesis: `hyp-YYYYMMDD-NNN`
- variant: `hyp-YYYYMMDD-NNN-vNN`

Variants must share a parent when they test the same mechanism with changed threshold, horizon, symbol, regime bucket, or parameter range.

## Strategy Family Taxonomy

Allowed v1 families:

1. order book imbalance;
2. weighted imbalance and microprice;
3. top-of-book reset behavior;
4. depth pull/refill asymmetry;
5. spread widening/narrowing regimes;
6. trade aggression bursts;
7. buy/sell pressure exhaustion;
8. queue depletion before price movement;
9. lead-lag between venues or execution sizes;
10. volatility/spread regime filters;
11. liquidity shock mean reversion;
12. adverse selection avoidance.

Custom families are allowed only when the spec explains how existing Makalu data can test the mechanism.

## Required Spec Fields

```yaml
schema_version: 1
hypothesis:
  id: hyp-20260618-001
  parent_id: null
  title: "BINANCE BTC_USDT order book imbalance predicts GMO BTC_JPY 120s mid return"
  status: draft
  family: order_book_imbalance
  family_label: "Order book imbalance"
  custom_family_justification: null
  created_at: "2026-06-18T00:00:00Z"
  owner: agent|human
  summary: ""
  expected_edge_mechanism: ""
  economic_rationale: ""
  duplicate_search:
    searched_at: "2026-06-18T00:00:00Z"
    query_terms:
      - imbalance
      - BINANCE
      - BTC_JPY
      - lead_lag
    possible_duplicates: []
    conclusion: "new|variant|duplicate"
  data_requirements:
    markets:
      - exchange: BINANCE
        symbol: BTC_USDT
      - exchange: GMO
        symbol: BTC_JPY
    datasets:
      - layer: bronze
        dataset: OrderBook
    minimum_distinct_days: 5
    required_columns: []
    unavailable_data_policy: needs-more-data
    research_metadata:
      dataset: null
      param_id: null
      build_id: null
      generated_at: null
      label_assumptions: {}
      metadata_status: complete|partial|missing|null
  feature:
    sql_expression: ""
    source_datasets:
      - layer: bronze
        dataset: OrderBook
        exchange: BINANCE
        symbol: BTC_USDT
    primary_timestamp_column: ts
    point_in_time_rule: ""
  cross_market_rationale: ""
  target:
    type: next_return|spread_adjusted_return|research_label
    primary_horizon_seconds: 120
    comparison_horizons_seconds: [30, 120, 300]
    source_dataset:
      layer: bronze
      dataset: OrderBook
      exchange: GMO
      symbol: BTC_JPY
    timestamp_cutoff_rule: ""
  execution_assumptions:
    order_type: market|limit|passive|not_applicable
    fees_bps: null
    half_spread_included: true
    slippage_bps: null
    latency_ms: null
    liquidity_capacity_assumption: ""
  falsification_tests:
    - name: ""
      expected_failure_meaning: ""
  scan_plan:
    primary_horizon_seconds: 120
    threshold_grid: []
    regime_buckets: []
    significance_method: block_bootstrap_by_day
  backtest_handoff_intent:
    potential_panda_core_strategy_pattern: ""
    himalaya_config_notes: ""
  decisions:
    - at: "2026-06-18T00:00:00Z"
      actor: agent|human
      decision: draft
      reason: ""
```

`feature.source_datasets[]` and `target.source_dataset` each carry their
own `exchange`/`symbol` — a feature is not assumed to be computed on the
same market as the target. See "Cross-Market Signal Sourcing" below.

## Enum Normalization

Machine fields use snake_case. Human-readable labels may be generated for review views.

Family enum values:

- `order_book_imbalance`
- `weighted_imbalance_microprice`
- `top_of_book_reset`
- `depth_pull_refill_asymmetry`
- `spread_regime`
- `trade_aggression_burst`
- `buy_sell_pressure_exhaustion`
- `queue_depletion`
- `lead_lag`
- `volatility_spread_filter`
- `liquidity_shock_mean_reversion`
- `adverse_selection_avoidance`
- `custom`

If `family: custom`, `custom_family_justification` is required and must name the existing Makalu datasets/columns that can test the mechanism.

## Cross-Market Signal Sourcing

A feature is not assumed to be computed on the same market as the target.
Where a lead-lag relationship exists between two markets (see
`markets/waves/leadlag_*.csv`, built by `tools/lead_lag_wave.py`), the
follower market's own order-book/flow signals are largely a lagged copy of
the leader's and are not independently informative — the informative
signal is computed on the leader, predicting the follower's future return.

Worked example: BINANCE leads GMO BTC_JPY 81-97% of the time across every
30-minute JST bucket. A hypothesis like "order book imbalance predicts GMO
BTC_JPY return" should compute the imbalance feature on `BINANCE:BTC_USDT`
(`feature.source_datasets`), not `GMO:BTC_JPY`, while the target return is
still measured on `GMO:BTC_JPY` (`target.source_dataset`) — that market is
where the strategy actually trades.

Whenever any `feature.source_datasets[].exchange`/`symbol` differs from
`target.source_dataset.exchange`/`symbol`, `cross_market_rationale` is
required and must name which market the feature is computed on and why
that market's signal is informative for the target market's return. This
applies beyond the `lead_lag` family — any family whose mechanism depends
on order flow or book state should consider whether the target market is a
follower before assuming its own book is the right feature source.

## Horizon Selection Must Be Justified By The Volatility Wave

`target.primary_horizon_seconds` (and `scan_plan.primary_horizon_seconds`,
which must match it) is not a free choice. Before scanning, check
`markets/waves/<exchange>_<symbol>.csv` (built by `tools/volatility_wave.py`)
for the target market: a horizon where price rarely moves past the spread
cannot be profitable regardless of what the signal predicts, and is not
worth scanning. `economic_rationale` or `summary` should note which wave
bucket/horizon combination motivated the choice, especially when the
target market's activity varies a lot through the day (see that market's
daily wave) — the same horizon can be reasonable at one time of day and not
at another.

## Validation Rules

A spec is invalid when:

- `expected_edge_mechanism` is empty;
- the family is not recognized and no custom-family justification exists;
- feature expression is not SQL over whitelisted Makalu datasets;
- target horizon is selected after scanning;
- execution assumptions are omitted;
- falsification tests are omitted;
- duplicate search was not performed;
- data requirements cannot be matched to Makalu inventory;
- a research-layer target is missing `param_id`;
- a feature is sourced from a different market than the target and `cross_market_rationale` is empty.

For research-layer targets, missing `param_id` is invalid. Missing `build_id` or `generated_at` sets `research_metadata.metadata_status: partial`, maps the lifecycle to `needs-tooling`, and blocks `scan-passed` / `backtest-approved` unless the narrow non-primary-target waiver in the Makalu integration contract applies.

## Lifecycle Statuses

Allowed statuses:

- `draft`
- `invalid-spec`
- `duplicate`
- `needs-more-data`
- `needs-tooling`
- `scan-ready`
- `scan-failed`
- `scan-passed`
- `backtest-approved`
- `backtest-running`
- `backtest-passed`
- `awaiting-promotion-decision`
- `promoted`
- `retired`

## Decision Reasons

Decision reasons are not lifecycle statuses. Refinement is modeled as a decision outcome that either returns a non-terminal hypothesis to `draft` with a revised spec, or retires the current hypothesis and creates a variant when the change would alter the tested claim.

Decision reasons must be selected from structured categories plus prose detail:

- `missing-mechanism`
- `duplicate`
- `insufficient-data`
- `unsupported-feature-transform`
- `lookahead-risk`
- `timestamp-alignment-risk`
- `failed-ic-threshold`
- `failed-hit-rate-threshold`
- `failed-cost-buffer`
- `autocorrelation-adjustment-failed`
- `regime-fragile`
- `parameter-overfit`
- `latency-infeasible`
- `liquidity-infeasible`
- `human-rejected-backtest-cost`
- `backtest-overfit`
- `promotion-approved`
- `promotion-declined`

## Duplicate Detection

Before creating a new id, the agent must search existing specs and ledger entries by:

- family;
- mechanism keywords;
- feature dataset and columns;
- target type and horizon;
- exchange and symbol;
- rejection category;
- parent id.

Duplicate handling:

- exact duplicate: mark `duplicate` and link to existing id;
- same mechanism with different parameter/horizon: create variant id;
- same data pattern but different mechanism: allow new parent id with explanation;
- rejected prior idea: require a specific reason why the new attempt is materially different.

## Rejection Memory

Every rejected or parked idea must preserve:

- the rejected claim;
- data window checked;
- scan report id when applicable;
- failed threshold or feasibility rule;
- whether retry is allowed;
- what condition would make retry valid, such as more data, new Makalu feature, lower latency, or taxonomy change.

## Backtest Readiness Criteria

A hypothesis is ready for human backtest review only when:

- status is `scan-passed`;
- scan window covers at least 5 distinct trading days or has a recorded human waiver;
- raw sample count is at least 10,000 and effective sample count is at least 1,000 for tick-level scans;
- declared primary horizon passes thresholds without post-hoc selection;
- effect direction is stable across at least 3 scanned days;
- cost buffer exceeds fees plus half-spread plus slippage/latency allowance with 25% margin;
- neighboring horizon/threshold cells show same-sign support;
- known yellow flags are disclosed.
