# Test Plan

## Test Strategy

v1 tests must prove that Kilimanjaro enforces workflow discipline before any expensive backtest handoff. The highest-risk failures are look-ahead leakage, duplicate/rejected idea loss, unpinned research labels, overfit promotion, and hidden execution assumptions.

## Automated Tests

### Hypothesis Schema Validation

Test cases:

- valid order book imbalance spec passes;
- missing edge mechanism fails;
- missing falsification test fails;
- missing duplicate-search summary fails;
- unsupported family without justification fails;
- spread/volatility family as standalone alpha fails or requires explicit conditioning role;
- adverse-selection family as directional alpha fails;
- research-layer target without `param_id` fails as `invalid-spec`;
- research-layer target without `build_id` or `generated_at` sets `metadata_status: partial`, maps to `needs-tooling`, may allow exploratory scan output, and cannot become `scan-passed` unless the narrow non-primary-target waiver applies;
- arbitrary Python transform fails with `needs-tooling`.

### Ledger And Duplicate Search

Test cases:

- exact duplicate is detected by family, dataset, symbol, horizon, and mechanism;
- variant is linked to parent id;
- rejected idea remains searchable by reason;
- ledger status transition rejects invalid jumps such as `scan-failed` to `promoted`;
- scan-passed entry cannot become `backtest-approved` without human approval marker.

### Inventory

Test cases:

- catalog parser recognizes bronze, silver, gold, feature, and research layers;
- `BookState`, `BookChange`, `VolSpread`, `LeadLag`, `Feature_Mid`, and `Feature_NearHFT` are categorized correctly;
- research datasets are marked `research-ephemeral`;
- missing dates are represented;
- insufficient distinct days produces `needs-more-data`.

### Scanner Input Safety

Test cases:

- SQL expression over whitelisted columns passes;
- expression referencing non-whitelisted dataset fails;
- future-looking target join fails;
- window aggregation without point-in-time cutoff fails;
- raw Python, shell, import, or UDF execution fails;
- family #9 scan fails when timestamp alignment metadata is missing.

### Scan Metrics

Test cases:

- IC is computed per-day and pooled;
- hit rate includes binomial significance;
- bucketed returns include all signal quantiles;
- horizon decay reports all configured horizons;
- autocorrelation reduces effective sample size;
- significance calculation uses effective sample size;
- stability is reported by day and regime bucket;
- full grid is persisted, not only best cell.

### Threshold Evaluation

Test cases:

- fewer than 5 distinct days blocks promotion;
- raw sample count below 10,000 blocks tick-level promotion unless waiver exists;
- effective sample count below 1,000 blocks tick-level promotion unless waiver exists;
- sign flipping across days fails;
- effect in only one spread/volatility bucket is flagged;
- expected edge below fees plus half-spread plus slippage/latency plus 25% buffer fails;
- isolated best grid cell fails overfit check;
- declared primary horizon is evaluated instead of best observed horizon.

### Handoff Generation

Test cases:

- review packet can be generated from `scan-passed`;
- handoff cannot be generated from any status other than `backtest-approved`;
- handoff requires a prior human approval event;
- handoff includes potential-panda-core mapping notes;
- handoff includes Enoshima train/test/forward split windows;
- handoff includes execution assumptions and expected result fields;
- handoff does not create strategy code.

## Fixture Tests

Create small deterministic fixtures for:

- Makalu inventory with complete data;
- Makalu inventory with missing dates;
- research-layer target with and without build id;
- synthetic feature/target pair with known positive IC;
- synthetic feature/target pair with high autocorrelation;
- synthetic one-cell overfit grid;
- synthetic regime-fragile signal.

Expected outputs should be checked exactly for statuses, failed rules, and key metric values within tolerances.

Fixture schemas:

- inventory fixtures are YAML matching `makalu-integration-contract.md`.
- spec fixtures are YAML matching `hypothesis-contract.md`.
- ledger fixtures are JSONL events.
- scan report fixtures are JSON matching `data-contract.md`.

Formula-backed expected outputs:

- Spearman IC fixture with monotonic feature/target must produce positive IC.
- Null/tie fixture must drop nulls and average ties.
- Lag-1 autocorrelation fixture must produce `n_eff = n * (1 - rho1) / (1 + rho1)` after clipping `rho1`.
- Block bootstrap fixture must use fixed seed and stable confidence interval bounds.
- One-cell grid fixture must fail when no adjacent cell has same-sign IC with at least 50% of primary absolute IC.
- Cost fixture must fail when expected spread-adjusted return bps is not greater than `1.25 * (fees_bps + half_spread_bps + slippage_bps + latency_penalty_bps)`.

## Integration Tests

Run against local Makalu/Enoshima paths when available:

- inventory refresh reads Makalu catalog and executes safe list/probe queries;
- scanner can query a small real Makalu window for `BookState` or `Feature_Mid`;
- scan report can be attached to ledger;
- scan-passed ledger entry can produce a review packet but not handoff YAML;
- backtest-approved ledger entry can produce a handoff packet;
- handoff packet can be read by an implementer without additional product clarification.

Integration tests must not:

- modify Makalu datasets;
- submit live/paper trades;
- auto-submit Enoshima backtests unless explicitly run in a controlled test environment.

## Manual Review Tests

Before enabling unsupervised scanner volume:

1. Run 3-5 manual loops across different taxonomy families.
2. Review the first 5 runs for each family before autonomous volume.
3. Verify at least one rejected idea is recorded with useful memory.
4. Verify at least one scan-passed idea requires human approval before transitioning to `backtest-approved` and generating handoff YAML.
5. Verify a human can understand why each decision was made from persisted artifacts alone.

## Regression Tests

Add regression cases for:

- known rejected hypotheses not being recreated as new parent ids;
- prior scanner threshold bugs;
- research label regeneration invalidating promotion readiness;
- look-ahead leakage bugs;
- cost buffer calculation bugs;
- status transition bugs;
- taxonomy/threshold change audit requirements.

## Acceptance Bar

The test suite is sufficient for v1 when:

- a code agent cannot accidentally promote PnL-only results;
- unsupported feature execution is blocked;
- missing data and missing metadata create parked statuses;
- every valid spec creates durable memory;
- human gates are enforced;
- a backtest-approved hypothesis can be handed to an implementer without guessing the research workflow.
