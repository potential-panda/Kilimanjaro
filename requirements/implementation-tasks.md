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

**Done (2026-06-18).**

Dependencies: task 1.

Delivered:

- `schemas/hypothesis_spec.schema.json` — JSON Schema (2020-12) for the spec
  structure in `hypothesis-contract.md`, with enums for `family`,
  lifecycle `status`/`decision`, target `type`, and `significance_method`
  (`decision_reason` enum is also defined there for reuse once the ledger
  event writer is built in task 3/9, even though specs themselves don't use
  it directly).
- `tools/validate_spec.py` — loads a YAML spec, validates it against the
  schema, then applies the cross-field business rules from
  hypothesis-contract.md's "Validation Rules" that JSON Schema can't express
  cleanly (duplicate search actually performed, `custom` family requires
  justification, research-layer target requires `param_id`). Two rules from
  that section are explicitly deferred (noted in the script): SQL-expression
  safety (task 6) and matching data requirements against a live inventory
  (task 4) — both need data this script doesn't have yet.
- `tests/fixtures/` + `tests/test_validate_spec.py` proving the acceptance
  criteria below; `pyproject.toml` declaring `pyyaml`/`jsonschema` as the
  project's first real dependencies.

Acceptance (all proven by `python -m unittest tests.test_validate_spec -v`):

- Valid sample spec passes. ✅
- Missing mechanism fails. ✅
- Missing duplicate search fails. ✅
- Research-layer target without version metadata fails. ✅

### 3. Implement ledger schema and duplicate search

**Done (2026-06-18).**

Dependencies: task 2.

Delivered:

- `schemas/ledger_event.schema.json` — JSON Schema for `ledger/events.jsonl`
  lines, cross-referencing `hypothesis_spec.schema.json`'s `lifecycle_status`
  and `decision_reason` enums rather than duplicating them.
- `tools/ledger.py` — `append` (validates and appends one event, generating
  `event_id`/`created_at`) and `search` (joins `specs/*.yaml` with
  `ledger/events.jsonl` by `hypothesis_id`, since family/mechanism/dataset/
  symbol/horizon live in the spec, not the ledger event — matches
  `hypothesis-contract.md`'s "Duplicate Detection" search fields).
- `tests/test_ledger.py` proving the acceptance criteria below.

Acceptance (all proven by `python -m unittest tests.test_ledger -v`):

- Agent can find prior rejected ideas. ✅ (`search(rejected_only=True)`,
  matching on lifecycle status or any rejection-category `decision_reason`)
- Variants link to parent ids. ✅ (`parent_id` round-trips through search)
- Duplicate entry can be marked without running a scan. ✅
  (`--to-status duplicate --decision-reason duplicate` with no
  `scan_report_id` in `artifact_ids`)

### 4. Implement Makalu inventory snapshot

**Done (2026-06-18).**

Dependencies: task 1.

**Feasibility spike completed (2026-06-18)**, running the exact commands
from `makalu-integration-contract.md` against the real Makalu repo and data.
Findings folded into `makalu-integration-contract.md` and `data-contract.md`:

- `query --list`'s actual JSON shape differs from what was originally
  assumed (`dataset`/`layer`/`grain`/`partitions`/`source`, no
  `dataset_name`/`view_name`/`description`) — doc corrected.
- `catalog.yaml` declares `BookState`/`BookChange` as `layer: bronze`, but
  their real on-disk storage is `silver/`. Makalu's query engine derives its
  view-registration path from the catalog's declared layer, so it silently
  fails to register either dataset — `DESCRIBE bookstate` errors today even
  though populated data exists (39 days, `BINANCE`/`BTC_USDT` only;
  `GMO`/`BTC_JPY` has none). `data-check` is unaffected (it uses its own
  correct layer map). This is not a blocker — see the Bronze-First
  Reliability Model — but it means inventory cannot trust `catalog.yaml`'s
  `layer` field for non-bronze datasets and must verify queryability
  directly.

Deliver:

- reader for Makalu `doc/catalog.yaml`;
- queryability verifier: for every non-bronze dataset a spec references,
  confirm it's actually registered (`query --list` / `DESCRIBE`) before
  trusting the catalog's declared layer or treating it as available;
- probe wrapper for Makalu query CLI/date partition availability
  (`data-check` preferred, filesystem `path_template` fallback per
  `makalu-integration-contract.md`);
- routing logic implementing the Bronze-First decision order: dataset
  already queryable and covers the scope -> `none`; known Makalu pipeline
  but missing partitions -> `populate`; not worth populating or definition
  mismatch -> `derive_from_bronze`; no existing pipeline produces the
  signal -> `new_dataset_needed`;
- inventory output file (see `data-contract.md`'s Inventory Record schema,
  including `catalog_declared_layer`, `resolved_layer`, `query_verified`,
  `reliability`, and `population_action` fields).

Acceptance:

- Inventory includes all five layers.
- Bronze entries are marked `reliability: guaranteed`; every other layer's
  entries are marked `reliability: candidate` and carry a `query_verified`
  result that is independently checked, not inferred from the catalog.
- Inventory distinguishes canonical and research-ephemeral datasets.
- Missing dates are represented.
- No writes occur to Makalu datasets from the inventory step itself; running
  an existing Makalu populate command in response to a `populate` routing
  decision is a separate, explicit action, not an inventory side effect.
- A non-bronze dataset that is unpopulated, unqueryable, or mislabeled in
  the catalog does not block the hypothesis by itself — it resolves to a
  `population_action` per the Bronze-First model, not directly to
  `needs-tooling`/`needs-more-data`. `needs-tooling`/`needs-more-data` apply
  only when bronze itself can't support the needed window, or when a
  `new_dataset_needed` routing hasn't been actioned yet.

Delivered: `tools/inventory.py`, run and verified against the real Makalu
instance (not mocked — the entire point is checking what's actually true).
Building and testing it surfaced three more real bugs beyond the original
spike, all fixed and folded into `makalu-integration-contract.md`/
`data-contract.md`:

- An early implementation computed `reliability` from `catalog_layer ==
  "bronze"` — i.e., from the catalog's *claim*, which is exactly the
  untrusted value this model exists to route around. `BookState`/
  `BookChange` were showing `reliability: guaranteed` until fixed to check
  the literal dataset name (`Trade`/`OrderBook` only).
- `data-check` exits 0 with no error even for datasets it doesn't support
  (`Feature_NearHFT`, the research labels) — it just logs an "Unknown
  dataset" line to stdout instead of any of the documented field labels.
  Originally fell through to a generic `unknown` status for the wrong
  reason; now detected explicitly and routed to the documented filesystem
  `path_template` fallback, with a warning explaining why.
- `data-check` also logs `"No data for exchange=X under ..."` (a real,
  explicit absence signal) when a dataset has a registered view but zero
  data for the requested exchange — this also fell through to `unknown`
  originally; now mapped to `status: missing`.
- The hardcoded list of "datasets needing extra partitions beyond
  exchange/symbol" (`MarketPrice`/`VolSpread`/`LeadLag`) missed
  `TripleBarrierLabel`/`TripleBarrierLabel_tmp`/`BuyLabel_tmp`/`Chart_*`,
  which also have a `param_id` partition per `catalog.yaml`. Replaced with
  a check against the catalog's own `partition_columns` field instead of a
  hand-maintained list, so it can't silently drift out of sync again.
- `Window`/`Missing` line examples in the integration contract used an
  ASCII `->` and always listed individual missing dates; the real CLI uses
  a Unicode `→` and can express missing dates as ranges. Doc corrected.

Acceptance proven by `tests/test_inventory.py` (skipped automatically if
the real Makalu checkout isn't present, since mocking it would defeat the
point): all five layers present; bronze marked `guaranteed` and everything
else `candidate` independent of the catalog's claim; `BookState`'s
catalog/resolved mismatch surfaces correctly; canonical vs. research-
ephemeral distinguished; missing dates represented (including the
"nothing exists for this scope at all" case, confirmed against
`Feature_NearHFT`); non-bronze unavailability always resolves to a
`population_action`, never a direct block; extra-partition datasets skip
the date check with a warning instead of misreporting; no `populate`
calls anywhere in the tool.

### 5. Implement spec validation against inventory

Dependencies: tasks 2 and 4.

Deliver:

- validator that verifies datasets, required columns where available, date coverage, minimum distinct days, and research metadata.

Acceptance:

- Missing data yields `needs-more-data`.
- Unsupported feature transform yields `needs-tooling`.
- Valid SQL-based feature over whitelisted datasets yields `scan-ready`.

### 6. Implement SQL AST allowlist

**Done (2026-06-18).**

Dependencies: tasks 4 and 5 listed originally, but turned out not to be
real — this task only parses and inspects the `sql_expression` string
itself; it needs no live inventory or schema data, so it was implemented
before task 5 with no issue. (Confirming a referenced *column* actually
exists in Makalu still depends on task 5/4 — that check remains deferred,
unchanged from the original scope.)

Delivered:

- `tools/sql_safety.py` — parses with `sqlglot`'s DuckDB dialect, then
  walks the AST (not just checks for a parse exception — confirmed during
  the spike and re-confirmed here that subqueries and multi-statement
  strings parse without error) and rejects: any disallowed node type
  (`Select`, `Subquery`, `With`/`CTE`, `Join`, `Drop`, `Insert`, `Update`,
  `Delete`, `Create`, `Alter`, `Pragma`, `Copy`, `Attach`, `Command`,
  `Block`/multi-statement); any function call whose name isn't on the
  scalar-function allowlist (checked by literal call name for generic
  `Anonymous` nodes like `now()`/`clip()`, not just by node class); any
  window function whose frame is missing, unbounded, or ends after the
  current row.
- Two corrections found and fixed while building it, beyond what the
  spike predicted: `CASE WHEN` parses to `exp.Case`/`exp.If`, both
  subclasses of `exp.Func`, so they had to be explicitly exempted from the
  function-name check (the first version wrongly rejected every `CASE
  WHEN`); and aggregate functions used as the windowed function
  (`avg`/`min`/`max`/`sum`/`count` inside `OVER (...)`) needed their own
  separate allowlist from the scalar-function one (the first version
  wrongly rejected `avg(x) OVER (...)`, which the Window policy clearly
  expects to be usable). Both are now reflected in `requirements.md`'s
  Scanner SQL Safety Contract.
- Wired into `tools/validate_spec.py`: `feature.sql_expression` is now
  checked for safety, not just non-emptiness.
- `tests/test_sql_safety.py` (18 cases) plus a dedicated unsafe-expression
  fixture in `tests/test_validate_spec.py`.

Acceptance (all proven by `python -m unittest tests.test_sql_safety -v`):

- Safe scalar expressions pass. ✅
- Unsafe SQL constructs fail before query execution. ✅
- Window frames ending after current row fail. ✅
- Feature specs cannot provide complete SELECT statements. ✅

### 7. Implement signal scanner

**Done (2026-06-18).**

Dependencies: task 6.

Delivered:

- `tools/scanner.py` — `run_scan(spec, start_date, end_date)` reads the
  feature and target source datasets directly via DuckDB (same pattern as
  `tools/volatility_wave.py`/`lead_lag_wave.py`/`fill_probability.py`:
  ASOF joins against bronze `OrderBook`, not Makalu's 200-row-capped query
  CLI), substitutes the spec's `feature.sql_expression` (after re-checking
  it with `tools/sql_safety.py` — the scanner refuses to run an expression
  that wasn't already validated safe), and computes every metric in
  `requirements.md`'s "Deterministic Scanner Metrics": pooled + per-day
  Spearman IC, hit rate, 5 quantile-bucketed returns, decay across the full
  horizon grid (not just the primary horizon), raw and autocorrelation-
  corrected effective sample size, and a significance confidence interval.
- `schemas/scan_report.schema.json` and `tests/test_scanner.py` (16 tests:
  fast unit tests on synthetic data for each metric function, plus a real
  integration test re-running the canonical BINANCE-imbalance-predicts-
  GMO-return spec and checking the result matches the manually-computed
  walkthrough this whole task was based on).
- **Real finding that changed the default significance method**: running
  block bootstrap (the original v1 pick) against real tick-level data
  (~13M rows for a 15-day BINANCE/BTC_USDT window) took on the order of
  tens of minutes for 1,000 resamples — directly contradicting the
  scanner's cheap/fast design goal. Switched the default to Newey-West/HAC,
  exactly the fallback `evaluation-framework.md` already named for this
  case; full 3-horizon scan over that same window now completes in well
  under a minute. Block bootstrap remains available (and was fixed to stop
  rebuilding DataFrames per resample) for smaller, non-tick-level datasets.
  See `requirements.md`'s updated "Deterministic Scanner Metrics" section.

v1 scope notes (documented limitations in the module's own docstring, not
silently assumed): supports exactly one feature source dataset and one
target source dataset, both bronze `OrderBook`; `target.type:
research_label` is not implemented; `stability_by_regime` is always
emitted empty (regime-bucket joining against `VolSpread` is deferred);
`threshold_verdict.status` is always `"not_evaluated"` — applying the v1
pass/fail thresholds is task 8's job.

Acceptance (all proven by `python -m unittest tests.test_scanner -v`):

- Computes IC, hit rate, bucketed returns, decay, raw/effective sample counts, autocorrelation, corrected significance, and stability. ✅ (stability by regime is the one documented v1 gap above)
- Uses effective sample size, not naive IID row count. ✅
- Emits full grid. ✅ (`decay_by_horizon` covers every tested horizon)
- Does not run order simulation. ✅
- Refuses arbitrary Python transforms. ✅ (only ever evaluates the spec's already-AST-validated SQL expression)

### 8. Implement threshold evaluator

**Done (2026-06-18).**

Dependencies: task 7.

Delivered:

- `tools/threshold_evaluator.py` — `evaluate(report, spec)` fills in a scan
  report's `threshold_verdict` (always `"not_evaluated"` straight out of
  the scanner) by checking every v1 rule from `requirements.md`'s
  "Deterministic Scanner Metrics" and `evaluation-framework.md`'s
  "Pass / promote-to-backtest thresholds": distinct days, raw/effective
  sample counts, IC magnitude, hit rate, the HAC significance interval,
  day-by-day sign stability, single-cell overfit across the horizon grid,
  and the cost buffer (with the 25% safety margin). `failed_rules` use the
  exact `decision_reason` codes from `hypothesis-contract.md`'s enum, so
  task 9's ledger write-back can use them directly with no re-mapping.
- A real schema gap found and fixed while implementing the cost-buffer
  check: `execution_assumptions.half_spread_included` was only ever a
  boolean (included or not), with no field carrying the actual magnitude
  needed to compute the buffer. Added `half_spread_bps` to
  `schemas/hypothesis_spec.schema.json`, populated the canonical example
  spec with a real value derived from `markets/waves/GMO_BTC_JPY.csv`'s
  `avg_spread`, and caught a latent inconsistency in that same fixture
  (`half_spread_included: true` on a `next_return` target, which doesn't
  subtract spread — should have been `false`).
- `tests/test_threshold_evaluator.py` (15 tests): one synthetic case per
  rule, plus a real integration test re-running the scanner against the
  canonical BINANCE-imbalance-predicts-GMO-return spec — confirms the
  evaluator reaches exactly the conclusion worked through by hand earlier:
  every statistical threshold clears, only `failed-cost-buffer` fires.
- `threshold_verdict.status` extended beyond pass/fail to
  `needs-more-data` (too few distinct days) and `needs-tooling` (cost
  buffer can't be computed without a declared `half_spread_bps`) —
  schema updated to match.

v1 scope notes (documented in the module's own docstring): look-ahead
leakage, timestamp alignment, and missing-`param_id` checks are not
re-verified here (spec-validation concerns, not derivable from the scan
report's numbers); regime-bucket stability is always disclosed as the
`regime-stability-unproven` yellow flag, since `tools/scanner.py` always
emits `stability_by_regime` empty in v1 — never silently treated as fine.

Acceptance:

- Requires at least 5 distinct trading days for promotion unless waived. ✅ (`needs-more-data`; a human waiver is a separate, later ledger action, not part of this evaluator)
- Requires at least 10,000 raw and 1,000 effective samples for tick scans unless waived. ✅
- Checks declared primary horizon. ✅ (against the decay grid's matching entry)
- Checks cost buffer with 25% safety margin. ✅
- Detects single-cell overfit across neighboring grid cells. ✅

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

- ~~Verify the `makalu-integration-contract.md` command assumptions against real Makalu output during task 4.~~ **Done (2026-06-18).** Findings: `query --list`'s JSON shape was wrong in the original doc (corrected); `BookState`/`BookChange` are not currently queryable due to a catalog/storage layer mismatch (not a blocker — see Bronze-First Reliability Model). Folded into `makalu-integration-contract.md` and `data-contract.md`.
- ~~Confirm `sqlglot` compatibility with the exact DuckDB expressions needed by v1 scanner specs.~~ **Done (2026-06-18), implemented in task 6.** `sqlglot` is now a real project dependency (`pyproject.toml`) and `tools/sql_safety.py` does exactly the AST-walk validation the spike said would be needed — see task 6 above for the two additional corrections found while implementing it (`CASE WHEN`/`exp.If` are `exp.Func` subclasses needing exemption; window-aggregate functions need their own allowlist separate from scalar functions).
- Decide whether missing research-layer `build_id`/`generated_at` requires a Makalu enhancement before any research-label-backed hypothesis can be promoted.
- Confirm append-only `ledger/events.jsonl` is acceptable for concurrent agent sessions under repository lock or single-writer discipline.
