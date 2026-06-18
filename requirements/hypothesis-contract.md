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
  title: "Depth imbalance predicts 5s mid return on GMO BTC_JPY"
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
      - BookState
      - BTC_JPY
    possible_duplicates: []
    conclusion: "new|variant|duplicate"
  data_requirements:
    markets:
      - exchange: GMO
        symbol: BTC_JPY
    datasets:
      - layer: bronze
        dataset: BookState
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
    source_datasets: []
    primary_timestamp_column: ts
    point_in_time_rule: ""
  target:
    type: next_return|spread_adjusted_return|research_label
    primary_horizon_seconds: 5
    comparison_horizons_seconds: [1, 5, 30, 120]
    source_dataset: {}
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
    primary_horizon_seconds: 5
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
- a research-layer target is missing `param_id`.

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
