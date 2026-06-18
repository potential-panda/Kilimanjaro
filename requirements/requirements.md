# Kilimanjaro Implementation Requirements

## Product Goal

Kilimanjaro v1 is an orchestration workspace for AI-assisted strategy discovery. It turns a microstructure intuition into one of three auditable outcomes:

- rejected with a recorded reason;
- parked because required data/tooling is missing;
- promoted to a human-approved Himalaya backtest task.

The product is the repeatable loop and its memory, not autonomous live strategy deployment or automatic code generation.

## Source Of Truth

These requirements preserve the approved phase-1 direction from:

- `/home/lulurun/workspace/Kilimanjaro/direction/workflow-strategy.md`
- `/home/lulurun/workspace/Kilimanjaro/direction/tool-gap-analysis.md`
- `/home/lulurun/workspace/Kilimanjaro/direction/hypothesis-taxonomy.md`
- `/home/lulurun/workspace/Kilimanjaro/direction/evaluation-framework.md`

## Scope

v1 must implement the smallest useful strategy discovery loop:

1. data inventory against existing Makalu catalog/query surfaces;
2. structured hypothesis specification;
3. persistent research ledger with duplicate/rejection memory;
4. signal-scan requirements and report format;
5. evaluation decision protocol;
6. manual backtest handoff package for Himalaya/potential-panda-core implementation.

## Non-Goals

Kilimanjaro v1 must not:

- duplicate Makalu datasets, pipelines, exchange connectors, or query engine;
- duplicate Himalaya backtest simulation;
- duplicate potential-panda-core strategy/runtime abstractions;
- generate strategy implementation code automatically;
- submit or schedule Himalaya runs without human approval;
- promote to paper or live trading;
- treat one profitable backtest or one PnL value as proof;
- accept mechanism-free feature mining or AutoML search;
- hide fees, spread, slippage, latency, capacity, or liquidity assumptions;
- run arbitrary Python feature transforms in the scanner.

## Workflow Stages

### 1. Data Inventory

Kilimanjaro reads Makalu's `doc/catalog.yaml` and probes available partitions through Makalu's existing query CLI. It produces a refreshed inventory of available markets, symbols, dates, dataset layers, feature families, and known gaps.

Acceptance criteria:

- Inventory identifies available datasets across bronze, silver, gold, feature, and research layers.
- Inventory reports exchange, symbol, date range, dataset name, and layer for each availability entry.
- Inventory distinguishes canonical Makalu data from research-layer outputs that are ephemeral and rebuildable.
- Inventory does not write to Makalu storage or create a new data pipeline.

### 2. Hypothesis Generation

An agent proposes a hypothesis from the approved taxonomy or explicitly justifies a new family using existing Makalu data.

Acceptance criteria:

- Every hypothesis names a strategy family, expected edge mechanism, target horizon, data source, and falsification test.
- Mechanism-free proposals such as "ML finds patterns" are rejected before scanning.
- Families #5 and #10 are treated as regime/conditioning layers, not standalone alpha sources.
- Family #12 is treated as execution-cost/adverse-selection logic, not directional alpha.

### 3. Hypothesis Specification

The agent creates a machine-readable hypothesis spec. The spec is the unit of work that moves through scan, ledger, and handoff.

Acceptance criteria:

- No scan runs without a valid spec.
- The spec predeclares the primary horizon and parameter grid before results are known.
- The spec declares all execution assumptions: fees, half-spread, slippage, latency, liquidity/capacity, and order type assumptions.
- The spec includes a duplicate-search summary from the ledger.
- The spec can be rejected as invalid without consuming scan/backtest resources.

### 4. Signal Scan

The scanner evaluates a feature expression against a target without order simulation or Himalaya.

Acceptance criteria:

- v1 scanner accepts SQL expressions only over whitelisted Makalu datasets/columns.
- Arbitrary Python transforms are refused and recorded as `needs-tooling`.
- Scanner computes IC, hit rate, bucketed return, horizon decay, raw/effective sample count, autocorrelation, significance using effective sample size, and stability by day/symbol/regime.
- Scanner output includes the full horizon/threshold grid, not only the best cell.
- Scanner emits a structured report attached to the ledger entry.

### 5. Feasibility Review

Automated checks apply the scan thresholds; human review gates promotion from scan to Himalaya.

Acceptance criteria:

- Agent may autonomously reject failed scans with recorded reasons.
- Agent may mark insufficient data as `needs-more-data`.
- Agent may mark unexpressible SQL features as `needs-tooling`.
- Only a human can approve `scan-passed` to become `backtest-approved`; handoff generation is allowed only after that transition.
- Any threshold/taxonomy change requires a human-reviewed meta-decision and changelog entry.

### 6. Ledger Update

Every specified hypothesis gets persisted, including rejected and parked ideas.

Acceptance criteria:

- Ledger entry is created when a valid spec exists, even if the scan later fails.
- Rejection reasons are structured enough to prevent rediscovery of the same failed idea.
- Variants share a parent hypothesis id.
- Entries using research-layer labels record dataset name, `param_id`, available build/regeneration metadata, and `metadata_status`.
- Agent checks the ledger before drafting new hypotheses.

### 7. Backtest Review And Handoff

A `scan-passed` hypothesis produces a human review packet. Only after a human approval event transitions it to `backtest-approved` may Kilimanjaro generate the manual implementation handoff package for Himalaya/potential-panda-core.

Acceptance criteria:

- Review packet is generated from `scan-passed`.
- Handoff YAML is generated only from `backtest-approved`.
- Handoff identifies the target potential-panda-core strategy/variant pattern where known.
- Handoff declares entry/exit rules, parameter grid, train/test/forward splits, execution assumptions, and expected diagnostics.
- Handoff is a task package, not generated strategy code.
- Handoff references the exact hypothesis id, scan report id, and ledger entry.
- Handoff generation itself does not approve the hypothesis.

### 8. Backtest Evaluation

Himalaya results are ingested or linked and evaluated against the approved protocol.

Acceptance criteria:

- Results include net PnL after fees/spread/slippage, trade count, turnover, win rate, risk-adjusted metric, drawdown, capacity/liquidity sensitivity, parameter sensitivity, and regime breakdown.
- Train/test/forward splits are evaluated separately.
- Forward-test failure is recorded as overfit evidence and must not be tuned away inside the same hypothesis id.
- Promotion cannot be based on a single run, single period, or PnL alone.

### 9. Decision Memory And Iteration

The agent records the decision and next experiment.

Acceptance criteria:

- Decision outcome is one of `promote`, `retire`, `refine`, `park-needs-more-data`, or `park-needs-tooling`.
- Decision outcome is distinct from lifecycle status; `refine` either returns a non-terminal hypothesis to `draft` with a revised spec or creates a variant and retires the prior id when the tested claim changes.
- Decision cites the specific metric, feasibility, data, or execution reason.
- Next suggested experiment is recorded when useful.
- Failed ideas remain searchable.

## User Stories

### Strategy Researcher

As a strategy researcher, I want to see which Makalu data is available before proposing hypotheses, so that I do not design scans against missing or stale datasets.

Acceptance criteria:

- I can request an inventory by exchange/symbol/date/family.
- The inventory shows canonical vs research-layer data.
- Missing required data blocks or parks the hypothesis before scan.

### Strategy Researcher

As a strategy researcher, I want every hypothesis to include a mechanism and falsification test, so that the system rejects pattern mining that cannot explain why an edge should exist.

Acceptance criteria:

- Missing mechanism prevents spec validation.
- Missing falsification test prevents scan.
- The ledger records invalid-spec rejections.

### AI Research Agent

As an AI research agent, I want a fixed hypothesis schema and ledger, so that I can run repeated research sessions without rediscovering rejected ideas.

Acceptance criteria:

- Agent must perform duplicate search before creating a new hypothesis id.
- Duplicate or near-duplicate hypotheses are linked to existing parent ids.
- Rejection memory is searchable by family, mechanism, data source, symbol, and reason.

### Human Reviewer

As a human reviewer, I want scan-passed ideas to arrive with full diagnostics and assumptions, so that I can decide whether a Himalaya run is worth the cost.

Acceptance criteria:

- Promotion request includes spec, scan report, full grid, stability breakdown, cost assumptions, and known yellow flags.
- Reviewer can approve, reject, or request revision.
- Reviewer decision is recorded in the ledger.

### Engineering Implementer

As an implementer, I want a handoff package that maps a human-approved `backtest-approved` hypothesis to potential-panda-core and Himalaya, so that implementation does not require guessing the research intent.

Acceptance criteria:

- Handoff includes entry/exit behavior, target datasets, split windows, parameter grid, expected result fields, and execution assumptions.
- Handoff declares out-of-scope implementation behavior.
- Handoff links to the ledger and scan report.

## Operational Rules

- Agent-autonomous stages: data inventory, hypothesis draft, spec validation, duplicate search, signal scan, failed-scan ledger update.
- Human-gated stages: scan-to-backtest promotion, backtest-to-strategy-library promotion, taxonomy/threshold changes.
- Audit cadence: first 5 autonomous runs per taxonomy family require human review before unsupervised volume; weekly audit samples at least 10% of rejected entries and 100% of scan-passed entries.
- Bug response: scanner or threshold bugs halt autonomous runs for the affected family or scanner scope until all impacted entries are rechecked.
- Research-layer labels are never stable ground truth unless pinned by build/regeneration metadata.

## Scanner SQL Safety Contract

**Open decision, not yet architect-approved**: this section names `sqlglot`
as the AST validation library. That choice is a starting recommendation, not
a requirement derived from phase-1 direction — `evaluation-framework.md`
only requires that SQL input be safely whitelisted, not which library
enforces it. Confirm `sqlglot`'s DuckDB-dialect coverage against the exact
expressions needed by the first 3-5 bootstrap hypotheses (see
`implementation-tasks.md`'s open feasibility items) before treating this as
settled; a hand-rolled allowlist parser is an acceptable fallback if
`sqlglot` proves too permissive or too strict for a real expression.

v1 scanner expressions must be parsed and validated through `sqlglot` using the DuckDB dialect before they are interpolated into any query. If Kilimanjaro later calls Makalu internals directly, the same AST validation remains required before execution.

Allowed expression scope:

- a single scalar expression for `feature.sql_expression`;
- optional boolean predicate expressions for predeclared filters;
- no complete user-supplied `SELECT` statements in hypothesis specs.

Allowed AST node categories:

- column references that resolve to whitelisted datasets/aliases and validated column names;
- numeric and string literals;
- arithmetic operations `+`, `-`, `*`, `/`, `%`;
- comparisons `=`, `!=`, `<`, `<=`, `>`, `>=`;
- boolean operations `AND`, `OR`, `NOT`;
- `CASE WHEN ... THEN ... ELSE ... END`;
- casts between numeric types where the target type is allowlisted;
- scalar functions: `abs`, `sqrt`, `ln`, `log`, `exp`, `least`, `greatest`, `coalesce`, `nullif`, `sign`, `clip` where supported by the execution engine.

Disallowed AST node categories:

- DDL, DML, PRAGMA, COPY, ATTACH, INSTALL, LOAD, EXPORT, CREATE, DROP, INSERT, UPDATE, DELETE;
- subqueries and common table expressions;
- user-defined functions and macros;
- file, network, shell, environment, random, or clock functions, including `read_csv`, `read_parquet`, `httpfs`, `now`, `current_timestamp`, `random`;
- user-supplied joins;
- unbounded window functions;
- any function not explicitly allowlisted.

Window policy:

- v1 specs should prefer precomputed Makalu rolling columns.
- If window functions are allowed in a later v1 increment, the frame must be `ROWS BETWEEN <finite N> PRECEDING AND CURRENT ROW` or an equivalent time-safe preceding-only frame.
- Frames ending after current row, centered windows, `FOLLOWING`, or omitted/unbounded frames are rejected.

Join policy:

- Hypothesis specs do not define arbitrary joins.
- The scanner owns joins between one feature source, one target source, and optional `VolSpread` regime source.
- Joins must be by required partitions plus timestamp alignment.
- Point-in-time joins use the latest row with `source.ts <= signal.ts` within a configured tolerance; target joins use `target.ts > signal.ts` and the declared horizon.
- Lead-lag family scans must include `market_a`, `market_b`, `param_id`, clock-alignment metadata, claimed lag, and latency assumption; missing timestamp alignment metadata blocks promotion.

## Deterministic Scanner Metrics

**Open decision, not yet architect-approved**: `evaluation-framework.md`
allows block bootstrap, Newey-West/HAC, "or an equivalent predeclared
method" for the autocorrelation correction — it does not mandate block
bootstrap specifically. This section picks block bootstrap (1,000
resamples, fixed seed) as the v1 default because it makes fewer distributional
assumptions, at the cost of being more expensive to compute than Newey-West/HAC.
That tradeoff is worth a second opinion before implementation locks it in;
Newey-West/HAC is the documented fallback if bootstrap cost becomes a
bottleneck at scan volume.

v1 defaults:

- IC: Spearman rank correlation between feature value and target value. Compute per day, then report unweighted mean by day and pooled Spearman. Null feature/target rows are dropped; ties use average ranks.
- Hit rate: `sign(feature)` equals `sign(target)` after dropping rows where either sign is zero. Thresholded features may define a neutral band; neutral rows are excluded and the threshold must be predeclared.
- Buckets: 5 quantile buckets by feature value per day. Report count and mean target return per bucket; duplicate quantile edges collapse with a warning.
- Effective sample size: `n_eff = n * (1 - rho1) / (1 + rho1)`, where `rho1` is lag-1 autocorrelation of the feature within each day, clipped to `[0, 0.99]`; report summed day-level `n_eff`.
- Significance: v1 uses a block bootstrap by day with 1,000 resamples and fixed random seed recorded in the scan report. Primary statistic is pooled Spearman IC; pass requires the 95% bootstrap confidence interval to exclude zero with the claimed sign.
- Stability by day: primary-horizon IC sign must match claimed direction on at least 3 days and at least 60% of scanned days.
- Stability by regime: effect must have same sign in at least 2 of 3 `VolSpread` buckets. If fewer than 3 buckets exist, mark `regime-stability-unproven`.
- Isolated grid cell: primary cell passes only if at least 2 adjacent cells in the horizon/threshold grid have same-sign IC and at least 50% of the primary cell's absolute IC.
- Cost buffer: expected spread-adjusted return in basis points must exceed `fees_bps + half_spread_bps + slippage_bps + latency_penalty_bps`, multiplied by `1.25`. `latency_penalty_bps` is estimated from observed adverse mid movement over the declared `latency_ms`; if unavailable, promotion is blocked.

v1 threshold defaults:

- minimum distinct days: 5;
- minimum raw tick-level observations: 10,000;
- minimum effective tick-level observations: 1,000;
- primary pooled absolute Spearman IC: at least 0.02;
- primary hit rate: at least 51% where sign prediction is applicable;
- bootstrap confidence interval: excludes zero with claimed sign;
- cost buffer: positive after 25% safety multiplier;
- yellow flag: regime-stability-unproven, single symbol only, or human waiver used;
- automatic reject: look-ahead leakage, missing timestamp alignment, missing cost assumptions, missing required research `param_id`, or effect failing autocorrelation-adjusted significance.

## v1 Status Model

- `draft`: hypothesis is being written.
- `invalid-spec`: required spec fields are missing or contradictory.
- `duplicate`: same mechanism/data/target was already evaluated.
- `needs-more-data`: Makalu availability is insufficient for the claimed scan.
- `needs-tooling`: v1 SQL scanner cannot express the feature safely.
- `scan-ready`: spec is valid and data is available.
- `scan-failed`: scan completed but failed thresholds.
- `scan-passed`: scan passed automated thresholds, has no blocking metadata gaps, and awaits human review.
- `backtest-approved`: human approved manual implementation/handoff.
- `backtest-running`: Himalaya evaluation is in progress or linked externally.
- `backtest-passed`: backtest protocol passed and awaits promotion review.
- `awaiting-promotion-decision`: backtest passed but library promotion is not decided.
- `promoted`: human approved promotion.
- `retired`: idea is closed with a recorded reason.

Machine-enforceable transition rules are defined in `agent-loop-design.md`. Status labels alone are not sufficient authority for a transition.

## Remaining Product Risks To Challenge

- Whether the Makalu CLI and filesystem probes exactly match `makalu-integration-contract.md` in the target environment.
- Whether missing research-layer `build_id`/`generated_at` should block all research-label promotion until Makalu exposes the metadata.
- Whether `sqlglot` DuckDB-dialect parsing covers every safe expression needed by the first 3-5 taxonomy-family bootstrap runs.
