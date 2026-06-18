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

- ~~Verify the `makalu-integration-contract.md` command assumptions against real Makalu output during task 4.~~ **Done (2026-06-18).** Findings: `query --list`'s JSON shape was wrong in the original doc (corrected); `BookState`/`BookChange` are not currently queryable due to a catalog/storage layer mismatch (not a blocker — see Bronze-First Reliability Model). Folded into `makalu-integration-contract.md` and `data-contract.md`.
- ~~Confirm `sqlglot` compatibility with the exact DuckDB expressions needed by v1 scanner specs.~~ **Done (2026-06-18).** `sqlglot` (not yet a project dependency — installed ad hoc for the spike) parses every expression shape the SQL Safety Contract needs, but the validator cannot rely on "parses without error" alone: subqueries and multi-statement injection (`"1; DROP TABLE x;"`) both parse successfully into inspectable AST nodes rather than raising, so the validator must explicitly (a) reject anything that isn't a single bare expression/`Block`, (b) walk for disallowed node classes (`Subquery`, `Select`, `Join`, `Drop`, etc.), (c) check literal function-name strings on generic `Anonymous` nodes against the allowlist (e.g. `now()` has no distinct typed node in this dialect/version), and (d) inspect `WindowSpec` frame bounds directly rather than the presence of a `Window` node, since bounded and unbounded windows share the same top-level node type. `requirements.md`'s SQL Safety Contract section now flags the library choice itself as an open decision pending this validation working out in practice.
- Decide whether missing research-layer `build_id`/`generated_at` requires a Makalu enhancement before any research-label-backed hypothesis can be promoted.
- Confirm append-only `ledger/events.jsonl` is acceptable for concurrent agent sessions under repository lock or single-writer discipline.
