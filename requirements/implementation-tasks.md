# Implementation Tasks

## v1 Slice

Implement a file-backed strategy discovery loop that can run manually end-to-end on 3-5 taxonomy families:

1. Makalu inventory reader/prober;
2. hypothesis spec schema and validator;
3. file-backed ledger with duplicate search;
4. SQL-expression signal scanner contract/report;
5. decision threshold evaluator;
6. manual Himalaya handoff packet generator.

## Ordered Tasks

### 1. Create v1 artifact layout

Dependencies: none.

Deliver:

- `specs/` for hypothesis specs;
- `ledger/` for ledger entries;
- `scan-reports/` for scanner outputs;
- `handoffs/` for approved backtest handoffs;
- `inventory/` for inventory snapshots.

Acceptance:

- Layout is documented.
- Files are version-controllable.
- Id conventions match the hypothesis contract.

### 2. Implement hypothesis schema

Dependencies: task 1.

Deliver:

- machine-readable schema or template for hypothesis specs;
- enum definitions for family, status, decision reason, target type, and significance method.

Acceptance:

- Valid sample spec passes.
- Missing mechanism fails.
- Missing duplicate search fails.
- Research-layer target without version metadata fails.

### 3. Implement ledger schema and duplicate search

Dependencies: task 2.

Deliver:

- ledger entry format;
- append/update convention;
- search helper over family, mechanism, datasets, symbols, horizons, and decision reasons.

Acceptance:

- Agent can find prior rejected ideas.
- Variants link to parent ids.
- Duplicate entry can be marked without running a scan.

### 4. Implement Makalu inventory snapshot

Dependencies: task 1.

Deliver:

- feasibility spike that runs the exact commands from `makalu-integration-contract.md` against current Makalu;
- reader for Makalu `doc/catalog.yaml`;
- probe wrapper for Makalu query CLI/date partition availability;
- inventory output file.

Acceptance:

- Inventory includes all five layers.
- Inventory distinguishes canonical and research-ephemeral datasets.
- Missing dates are represented.
- No writes occur to Makalu datasets.
- Unsupported or unparseable Makalu metadata maps to `needs-tooling`; unavailable data maps to `needs-more-data`; both block promotion.

### 5. Implement spec validation against inventory

Dependencies: tasks 2 and 4.

Deliver:

- validator that verifies datasets, required columns where available, date coverage, minimum distinct days, and research metadata.

Acceptance:

- Missing data yields `needs-more-data`.
- Unsupported feature transform yields `needs-tooling`.
- Valid SQL-based feature over whitelisted datasets yields `scan-ready`.

### 6. Implement SQL AST allowlist

Dependencies: tasks 4 and 5.

Deliver:

- `sqlglot` DuckDB-dialect expression parser validation;
- allowlist for AST nodes and scalar functions;
- explicit rejection for subqueries, DDL/DML, UDFs, file/network functions, non-deterministic functions, and arbitrary joins;
- point-in-time join/window validation.

Acceptance:

- Safe scalar expressions pass.
- Unsafe SQL constructs fail before query execution.
- Window frames ending after current row fail.
- Feature specs cannot provide complete SELECT statements.

### 7. Implement signal scanner

Dependencies: task 6.

Deliver:

- scanner accepting SQL feature expression, target, window, and grid;
- Makalu query integration;
- metric computation.

Acceptance:

- Computes IC, hit rate, bucketed returns, decay, raw/effective sample counts, autocorrelation, corrected significance, and stability.
- Uses effective sample size, not naive IID row count.
- Emits full grid.
- Does not run order simulation.
- Refuses arbitrary Python transforms.

### 8. Implement threshold evaluator

Dependencies: task 7.

Deliver:

- pass/fail/yellow-flag evaluator using v1 defaults.

Acceptance:

- Requires at least 5 distinct trading days for promotion unless waived.
- Requires at least 10,000 raw and 1,000 effective samples for tick scans unless waived.
- Checks declared primary horizon.
- Checks cost buffer with 25% safety margin.
- Detects single-cell overfit across neighboring grid cells.

### 9. Implement ledger update workflow

Dependencies: tasks 3, 7, 8.

Deliver:

- command or documented procedure to attach scan reports and decisions to ledger entries.

Acceptance:

- Every valid spec receives a ledger entry.
- Failed scans preserve failed threshold reasons.
- Scan-passed entries are marked for human review, not auto-approved.

### 10. Implement handoff packet generator

Dependencies: task 9.

Deliver:

- manual handoff template for potential-panda-core/Himalaya implementation.

Acceptance:

- Includes entry/exit sketch, parameter grid, split windows, execution assumptions, expected outputs, and links to hypothesis/scan.
- Does not generate strategy code.
- Requires status `backtest-approved` before handoff YAML creation.
- Handoff generation does not perform the `scan-passed` to `backtest-approved` approval transition.

### 11. Run bootstrap validation

Dependencies: tasks 1-10.

Deliver:

- 3-5 completed manual loop examples across different taxonomy families.

Acceptance:

- At least one rejection is recorded.
- At least one `needs-more-data` or `needs-tooling` path is exercised if naturally available.
- At least one `scan-passed` review packet is reviewed by a human before transition to `backtest-approved` and handoff creation.
- Findings are used to revise schema/threshold clarity before autonomous volume.

## Commands To Provide

Exact command names may follow repo conventions, but v1 should expose equivalents of:

```bash
kilimanjaro inventory refresh
kilimanjaro hypothesis validate path/to/spec.yaml
kilimanjaro ledger search "imbalance BookState BTC_JPY"
kilimanjaro scan run path/to/spec.yaml
kilimanjaro ledger attach-scan hyp-... scan-...
kilimanjaro handoff create hyp-...
```

## Explicit Out Of Scope

- UI/dashboard.
- Database service.
- New Makalu pipeline.
- New exchange connector.
- New potential-panda-core strategy abstraction.
- Automatic strategy code generation.
- Automatic Himalaya run submission.
- Paper/live trading integration.
- Arbitrary Python feature execution.
- AutoML or broad feature search.

## Open Feasibility Items For Technical Review

- Verify the `makalu-integration-contract.md` command assumptions against real Makalu output during task 4.
- Decide whether missing research-layer `build_id`/`generated_at` requires a Makalu enhancement before any research-label-backed hypothesis can be promoted.
- Confirm `sqlglot` compatibility with the exact DuckDB expressions needed by v1 scanner specs.
- Confirm append-only `ledger/events.jsonl` is acceptable for concurrent agent sessions under repository lock or single-writer discipline.
