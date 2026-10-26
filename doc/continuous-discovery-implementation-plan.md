# Continuous Alpha Discovery: Implementation Plan

Status: core implementation and short real-data validation are available; long-running
and live-integration gates remain explicit. See [implementation status](discovery-implementation-status.md)
and the [operating runbook](continuous-discovery-runbook.md).
Created: 2026-10-25.

## 1. Objective and completion criteria

Build a persistent research service that observes available market data, plans
experiments, requests missing derived data through Hase/Makalu, measures predictive
relationships, constructs blends, evaluates executable returns, and follows frozen
candidates on fresh data. Every result must be traceable to its inputs, implementation,
and evaluation policy. Negative and inconclusive findings are durable outputs too.

The service is successful when it can complete that lifecycle repeatedly, recover
from interruption, and explain its decisions. Discovering a profitable strategy is
an empirical outcome, not an implementation acceptance criterion. A correct run that
rejects every candidate is a successful run.

The milestones below remain the acceptance criteria. Implementation and pilot
evidence are tracked separately so code availability is not confused with completion
of time-dependent or external-integration gates.

### Scope and boundaries

- Kilimanjaro owns experiment planning, search, evaluation, scheduling, research
  artifacts, and evidence. Reuse its existing numerical engines.
- Hase/Makalu own their supported market-data derivations and storage conventions.
  Komachi resolves shared data locations; bronze acquisition remains upstream.
- Missing derived data should trigger a dependency plan. Missing raw observations,
  unsupported data resolution, and collector outages must remain explicit limitations.
- Automatically execute configured research and derivation jobs within budgets.
  Keep existing human approval for Himalaya backtest spend and promotion. An already
  approved, matching backtest job can run without asking again. This plan adds no
  live-order or automatic production-deployment path.
- An unknown feature may produce an engineering task. The daemon must not edit
  upstream packages or execute arbitrary model-generated Python/shell commands.
- Start on one host with SQLite, Parquet, and the existing Python stack. Distributed
  orchestration, a graph database, and unrestricted symbolic search are deferred.

### Document precedence

This is the implementation sequence for the next version. Keep the stage meanings in
[research-pipeline.md](research-pipeline.md), the artifact history in
[data-contract.md](requirements/data-contract.md), and the approval transitions in
[agent-loop-design.md](requirements/agent-loop-design.md). Update affected
contracts alongside each milestone; do not leave
two conflicting definitions of the new behavior.

Older documents contain historical paths, CLI commands, data-availability assumptions,
and calibration claims. Verify them against installed packages and current code. In
particular, never infer that raw data covers a window merely because it is bronze,
or that a mechanism is calibrated merely because its execution prices match another.

## 2. Starting point: reuse and gaps

The following observations come from source inspection, not a fresh runtime audit.

| Area | Existing implementation | Work needed |
|---|---|---|
| Repeating execution | `driver/cadence.py`, `command/factory.py`: generate + scan, fixed supplied dates, budgets, stop handling | Durable dependency jobs, rolling data updates, resumable work, resource reservation |
| Search | `driver/generate.py`: 14 families, fixed market pairs, signature dedup, family feedback; `driver/op_sweep.py`: wider operator search | Dynamic eligible universe, prerequisite planning, measured exploration/replication allocation |
| Correlation and blending | `driver/signal_corr.py`, `driver/blend_corr.py` | Connect stages to jobs; persist comparable relationship records and incremental contribution |
| Return evaluation | `driver/return_eval.py`, `driver/scan_runner.py`, `framework/` | Explicit development/validation/confirmation roles; full campaign trial accounting |
| Data | `data/root.py`, `data/feature_frame.py`, `tools/inventory.py`, `tools/data_readiness.py` | One root resolver, provider adapters, coverage-aware dependency graph, availability timestamps |
| Memory | `ledger/`, `driver/ledger_io.py`, `driver/registry.py` | Versioned run identity, transactional job state, reliable publication into existing history |
| Calibration | `framework/calibration.py` plus `calibration/registry.py`, `calibration/trades.py`, `command/calibrate.py` | Reconcile mechanism registry with newer factor/trade calibration support; version certificates |
| Viewer | `src/api.py`, `web/` | Job health, campaign state, findings, confirmation provenance, and resource usage |

Concrete issues to resolve early:

1. `scan_fingerprint()` covers spec content and dates, but not source revisions,
   implementation versions, or the full evaluation configuration.
2. `already_processed()` accepts any matching ledger event. `process_spec()` records
   a fingerprint at `scan-ready`, before execution. Audit and regression-test retry
   behavior so a failed or interrupted attempt is not treated as completed.
3. `--max-scans` is checked between cadence cycles and can overshoot inside a cycle.
   Failed computations also consume resources and must count toward the new budget.
4. The generator skips families without existing features. The integration contract
   describes a populate/derive route, but it is not wired into this cadence.
5. Data-root constants and old Makalu ownership assumptions remain across modules.
6. Holdout results influence selection and generator feedback. Such periods are
   validation data for the search, not untouched final confirmation data.
7. Do not repeat the old claim that no expression replay counterpart exists without
   auditing the newer factor/trade calibration code and its external strategy support.

## 3. Target architecture

```text
completed/revised data partitions       research proposals
                  \                     /
                   catalog + experiment planner
                              |
                   durable dependency job queue
                              |
     reuse / Hase / Makalu / registered research derivation
                              |
        causal features + separate matured return labels
                              |
       stage 13: relationships -> stage 14: candidate blends
                              |
             stage 15: executable return evaluation
                              |
           freeze candidate + confirmation protocol
                              |
           fresh-data confirmation + forward tracking
                              |
        review packet / approved replay / calibration

Every stage -> immutable artifacts + research ledger + queryable index
Development evidence -> proposal prioritization and follow-up experiments
Confirmation data -> evaluator only, until its cohort is closed
```

Calibration is an independent evidence axis: it establishes simulator/scan fidelity,
not statistical edge. Approved replay can happen before or after confirmation, but
a final recommendation must state both statuses. Any replay-driven strategy change
creates a new candidate and requires new confirmation.

### Proposed code boundaries

Paths below are targets to add, not claims that these modules already exist. Small
helpers may share a file; preserve responsibility boundaries rather than maximizing
the number of files. Library paths are relative to `src/kilimanjaro/`;
`command/research.py` is relative to `src/`.

| Package or module | Responsibility |
|---|---|
| `research/contracts.py`, `identity.py` | Versioned records, canonical hashing, immutable run manifests |
| `research/store.py`, `migrations/` | SQLite job state, searchable indexes, trial allocations, event outbox |
| `research/planner.py`, `scheduler.py`, `worker.py` | Build dependency graph, reserve resources, lease and execute jobs |
| `research/artifacts.py`, `events.py` | Validate/publish outputs and bridge durable events to the existing ledger |
| `data/catalog.py`, `data/planner.py`, `data/providers/` | Discover coverage; adapt Hase, Makalu, and registered research derivations |
| `research/stages.py` | Thin adapters around existing correlation, blend, and return functions |
| `research/relationships.py` | Queryable feature/return and feature/feature observations |
| `research/validation.py`, `research/confirmation.py` | Time folds, freeze records, sealed evaluation, trial policy |
| `research/search.py`, `research/monitor.py` | Proposal selection and incremental forward/drift measurement |
| `command/research.py` | Explicit planning, bounded execution, status, and service commands |

Do not replace the existing `driver/`, `framework/`, expression evaluator, or causal
operator library with a second implementation. Extract reusable seams where needed.
Keep existing commands usable during the migration; switch the default factory path
only after the acceptance gates pass.

### Storage and ownership

Retain the existing numbered research stages. Proposed additions:

```text
cycle/01_schemas/                         new versioned schemas
cycle/03_inventory/                      data snapshots and capability reports
cycle/04_campaigns/<campaign_id>/         policy, split plan, exposure/freeze records
cycle/05_experiments/<experiment_id>/     immutable specifications and run manifests
cycle/13_signal_corr/<run_id>/            relationship tables and diagnostics
cycle/14_blend_corr/<run_id>/             fitted candidates and comparisons
cycle/15_return_eval/<run_id>/            trades, returns, confirmation/forward reports
cycle/16_calibration/<certificate_id>/    calibration evidence
cycle/17_ledger/events.jsonl              existing append-only audit history
cycle/17_ledger/research.sqlite           local durable state and indexes
cycle/18_handoffs/                        existing approved handoffs
```

SQLite lives on a local filesystem with transactions, schema migrations, a busy
timeout, and WAL mode. One service instance owns scheduling; initially use one
compute worker. Add concurrency only with measured resource limits. Do not put the
SQLite database on a shared network filesystem.

Specs, policies, and manifests are canonical JSON/YAML. Large result tables are
Parquet with optional CSV exports for compatibility. SQLite indexes reference those
artifacts; they do not become a second authoritative copy of their numeric contents.
Job/attempt state and trial-budget allocations are authoritative in SQLite.
Continue publishing research decisions to the JSONL ledger through a single writer.

Publication needs a transactional outbox: persist the state transition and pending
event together; publish a stable event id; reconcile publication after restart.
Make all ledger writers honor the same locking/idempotency protocol. Preserve old
events and migrate schemas additively. Do not claim cross-file atomic transactions.

Exclude database/WAL files and bulk outputs from git, but back them up with the
SQLite backup API and artifact manifests. Source-data hashes detect changes; they
do not preserve old bytes. A campaign needs provider-supported retention/pinning or
a scoped snapshot of required inputs. If inputs disappear, report the run as not
reproducible; never silently regenerate different data under the same identity.

## 4. Contracts to establish before broad search

### Records and identity

| Record | Required information |
|---|---|
| Campaign | Purpose, universe, data roles/windows, exposure history, resource and trial budgets, selection rules, execution policy, seed, policy version |
| Experiment | Hypothesis and falsification test, parent/variant links, feature dependencies, formula/operator parameters, target/label definitions, proposed evaluation protocol |
| Dataset snapshot | Provider/version, root reference, partitions, schema, units, execution size/parameters, checksums or trustworthy immutable revisions, coverage/gaps, timestamp semantics |
| Feature definition | Inputs, transformation/version, lookback and warmup, decision-time availability rule, missing/stale handling, allowed use as a feature |
| Label definition | Source price, horizon/exit rule, entry/exit timing, maturity timestamp, censoring, quote freshness, execution/cost policy; explicitly label-only |
| Run | Experiment revision, resolved snapshots, split/policy hashes, implementation/dependency versions, seed, artifacts, completion/rejection/inconclusive reason |
| Job and attempt | Stage, dependency ids, input hashes, resource estimate/reservation, state, lease owner/token/expiry, heartbeat, attempt number, error/retry schedule |
| Candidate freeze | Exact feature/model parameters, normalization state, trading rules, source run, freeze time, confirmation cohort, primary endpoint and decision rule |
| Relationship observation | Feature(s), target, horizon, regime, market, data role, effect/uncertainty, sample/coverage measures, source run |
| Calibration certificate | Mechanism, strategy/simulator versions, feature timing, venue/size, execution/cost policy, tested cases/tolerances, evidence and validity status |

Keep an experiment's logical identity separate from its execution identity. A run
hash includes the canonical specification, resolved data versions, split policy,
cost model, code/dependency versions, and random seed. A job key includes its stage,
implementation version, and input artifact hashes. Attempts are retries of that job.
Canonicalization must normalize key order and numeric representations, reject NaN
in identity fields, and avoid dependence on a checkout's absolute path.

### Time, data quality, and costs

- Use UTC instants internally and declare partition timezone explicitly. Preserve
  existing JST partition semantics at provider boundaries.
- Distinguish event time, receipt time where available, feature availability time,
  decision time, and label maturity. If receipt time is unavailable, record the
  latency assumption instead of claiming measured availability.
- A last-tick-per-second statistic is available after its bucket closes. Enforce
  backward/as-of joins on availability time and a configured freshness bound.
- Changes after decision time must not alter earlier feature values. Changing future
  prices may change labels, but must never change features or fitted past parameters.
- Missing partitions, known outages, stale quotes, insufficient depth, immature
  labels, and censored exits have different reason codes. Never fill a long outage
  into tradable prices or treat a missing label as zero return.
- Resolve execution size from explicit campaign configuration and supported data;
  do not silently switch to another partition or infer affordability from coverage.
- Keep research mid-price labels separate from executable bid/ask returns. Record
  applicable fees and slippage once; do not add spread again to bid/ask fills.
  Maker evaluation additionally requires a documented fill/queue assumption.

### Job state is separate from research outcome

```text
pending -> ready -> running -> succeeded
                     |          (validated, published artifacts)
                     +-> retry_wait -> ready
                     +-> failed
pending/ready -> waiting_data | waiting_approval | needs_tooling
pending/ready/running -> cancelled
```

Dependency completion/readiness can release waiting jobs. Expired running leases
are recoverable attempts. A rejected hypothesis is a successfully completed
evaluation job; a timeout is an infrastructure failure, not evidence against an
idea. Negative completed results deduplicate just as positive completed results do.

## 5. Recommended milestones

Work in this order. Each milestone should be a reviewable change or small sequence
of changes. M2 and M3 can use fixture providers independently after M1; do not start
broad search until M5 is complete.

| Milestone | Depends on | Deliverable |
|---|---|---|
| M0 | — | Verified baseline and corrected reliability seams |
| M1 | M0 | Contracts, run identity, durable metadata |
| M2 | M1 | Coverage-aware Hase/Makalu dependency planner |
| M3 | M1 | Recoverable, bounded job execution |
| M4 | M2, M3 | One complete development experiment through existing stages |
| M5 | M4 | Chronological validation, frozen confirmation, campaign trial accounting |
| M6 | M5 | Versioned calibration integration and forward tracking |
| M7 | M5 | Adaptive proposals and richer relationship exploration |
| M8 | M6, M7 | Incremental service, operations view, unattended acceptance run |

### M0 — Establish the baseline and repair reliability seams

Implementation:

- Record the checkout revision, package versions/import locations, CLI capabilities,
  root resolution, and test baseline. Do not expose credentials in the report.
- Audit both calibration registries and existing replay adapters. Separate stale
  documentation from unavailable functionality before designing external work.
- Audit dedup/retry behavior described in section 2. Make only completed outcomes
  suppress reruns; recover incomplete attempts without losing prior evidence.
- Identify and consolidate data-root resolution through `data/root.py`, preserving
  explicit test overrides and existing partition conventions.
- Document the baseline in `doc/discovery-baseline.md`; reconcile affected old
  contracts as code changes land. Record unrelated failing tests separately.

Likely changes: `driver/ledger_io.py`, `driver/loop.py`, `data/root.py`, callers with
hardcoded roots, and focused driver/data tests.

Acceptance:

- A fixture scan that fails after `scan-ready` can be retried and complete. A
  completed positive or negative scan is skipped on an identical rerun.
- Tests can use an isolated temporary lake without falling back to `/panda`.
- CLI help/read-only probes work without launching data derivation or replay.

### M1 — Add contracts, identity, and durable metadata

Implementation:

- Add the records in section 4, versioned schemas, canonical serialization, and
  SQLite migrations. Validate inputs at the CLI and job-handler boundary.
- Implement immutable run manifests, artifact references/checksums, and an indexed
  link to existing hypotheses, scan specs, and ledger events.
- Implement event outbox publication and schema-compatible stable event ids.
- Add explicit import of legacy specs/results as `legacy-exploratory`; do not
  retroactively label historical holdouts untouched or alter historical verdicts.
- Define storage layout/gitignore/backup policy; avoid automatically copying the
  entire lake or rehashing its full history on every cycle.

Likely changes: `research/contracts.py`, `identity.py`, `store.py`, `artifacts.py`,
`events.py`, `cycle/01_schemas/`, ledger schema/writer, and `.gitignore`.

Acceptance:

- Reordered YAML keys produce the same identity. Changes to inputs, code, split,
  costs, or seed produce a new run identity. A retry retains the original job id.
- Migrations preserve fixture legacy events and artifacts. Restarting around each
  outbox publication boundary yields one logically identifiable decision.
- Corrupt or incomplete artifacts cannot mark a job successful.

### M2 — Resolve missing data through provider adapters

Implementation:

- Add provider operations for capability discovery, inspection, derivation planning,
  execution, and output verification. Prefer supported Python APIs; otherwise use
  checked argument arrays with timeouts and captured logs, never shell strings.
- Route Hase-owned MarketPrice/Spread/Volatility and Makalu-owned supported metrics
  according to current capabilities. Verify actual outputs, including exact columns,
  execution size, parameter ids, and requested dates. Catalog existence is insufficient.
- Turn feature dependencies into a DAG, including warmup and label-horizon padding.
  Deduplicate shared derivations by provider, inputs, parameters, and partition.
- Reuse a compatible partition; derive missing partitions; use registered research
  transforms for new features; otherwise emit `needs_tooling` with an engineering spec.
- Centralize known outages and availability watermarks. Retry transient upstream
  incompleteness; do not endlessly repopulate a known structural gap.
- Publish experimental data under versioned research paths using provider conventions.
  Do not invoke destructive eviction or use `--recreate` as a routine missing-data fix.
  A stale canonical output requiring replacement must become an explicit rebuild plan.

Acceptance:

- A fake provider missing one day schedules exactly that partition plus required
  dependencies. A second request reuses it; changed parameters do not collide.
- Schema mismatch, partial files, unknown coverage, and structural outages produce
  distinct outcomes. No readiness decision relies only on a file's existence.
- Run one bounded, explicit market/date smoke test through available real providers
  in an isolated output scope; verify the manifest and loaded schema afterwards.
  If a provider cannot redirect output, use fixture verification until a scoped
  additive shared-lake test is included in the implementation task.

### M3 — Execute durable jobs within budgets

Implementation:

- Implement transactional job claims, dependency readiness, leases/heartbeats,
  attempt logs, retry backoff, and a bounded terminal-failure queue.
- Reserve CPU/memory/task slots and disk estimates before admission. Persist daily
  and campaign budgets; charge failed attempts too. No new job starts after a cap
  is exhausted. State how an in-flight job reaches its checkpoint or timeout.
- Publish outputs through validated temporary artifacts and atomic rename on the
  same filesystem. Require a current lease token before accepting completion.
- Initially serialize writes to shared provider partitions. Do not reclaim a lease
  into a second writer while an old child can still run; supervise/reap children and
  verify artifacts before releasing the partition lock.
- Implement graceful stop, restart, read-only status, and an explicit retry command.
  Idle queues and waiting data use backoff; no zero-interval hot loop.

Acceptance:

- Two workers cannot claim one job. Kill/restart fixtures before output publication,
  after publication, and before ledger delivery; each recovers without a duplicate
  logical result or a permanently skipped experiment.
- Crash during derivation cannot expose an incomplete partition as ready.
- A budget of two admissions starts at most two jobs, including failed attempts;
  restarting the process does not reset the budget.
- Normal tests use a fake clock/executor; no multi-minute sleeps or live market data.

### M4 — Connect one complete development experiment

Implementation:

- Choose one market pair from verified coverage and an explicit execution size.
  Start with the existing order-flow/imbalance and momentum families, a small fixed
  horizon set, and a small operator budget. Capture the resolved choices in a campaign.
- Execute: data plan -> features/labels -> `signal_corr` -> `blend_corr` ->
  `return_eval` -> evidence packet. Treat single factors as blends of one.
- Fit normalization, feature selection, signs, weights, regime cutoffs, and any
  learned exit settings using training data. Use validation for development ranking.
  Until M5, all packets are explicitly exploratory.
- Emit feature/return and feature/feature relationship records: Pearson/rank IC,
  quantile effects, horizon profile, time stability, coverage, and uncertainty where
  supported. Store missing estimates as unavailable, not invented zeroes.
- Preserve complete attempted grids and unsuccessful blends. Compare against a
  single-factor baseline and include a controlled null/lagged-label diagnostic.
- Reuse narrow frame loading and existing price caches. Stream feature batches;
  do not materialize every factor for every second simultaneously.

Likely changes: `research/stages.py`, `relationships.py`, existing driver function
interfaces, and `driver/review_packet.py`.

Acceptance:

- A synthetic delayed-effect dataset goes through the actual stage adapters and
  yields the expected direction; a contemporaneous-only relationship does not get
  presented as a verified future edge.
- Synthetic quotes show a predictive feature losing after costs when appropriate.
  Bid/ask spread is not charged twice.
- Changing only future observations leaves prior features unchanged. Check bucket
  close, as-of alignment, stale quotes, warmup, midnight, and data-gap boundaries.
- One small real-data campaign completes to a reproducible exploratory packet.
  Restarting the same campaign reuses completed jobs and artifacts.

### M5 — Separate search from confirmation

Implementation:

- Add chronological training/validation folds and a persisted data-exposure register
  shared across the research program's campaigns. A window inspected by selection
  is development data; starting another campaign does not erase that exposure.
  Historical data already used in this project cannot be declared fresh by relabeling it.
- Purge training labels whose information interval overlaps evaluation intervals;
  declare any extra embargo based on target duration/execution rules. Causal feature
  warmup from earlier observations is allowed; future normalization is not.
- Freeze each candidate and its primary endpoint before confirmation begins. Prefer
  newly arriving data after the freeze. Pin minimum evidence, maximum observation
  window, missing-data treatment, and pass/fail/inconclusive rules in advance.
  The initial economic endpoint should be a predeclared net-return measure with
  uncertainty, plus explicit drawdown, turnover, concentration, and liquidity checks.
  Bind all thresholds and sensitivity scenarios to the policy; never choose the
  best horizon, fee assumption, or endpoint after opening confirmation results.
- Route confirmation through a separate evaluator with restricted read interfaces.
  The generator must not receive intermediate confirmation metrics or raw labels.
  Post-confirmation changes create a new candidate and require later fresh data.
- Record all searched formulas, signs, horizons, regimes, blends, and exit variants
  before evaluating them. A proposal/attempt id links engineering failures and retries
  without pretending a retry is an independent statistical trial.
- Keep within-grid FDR as development evidence. For initial confirmation use a
  conservative, explicit multiple-testing policy: freeze each candidate cohort and
  allocate a family-wise error budget before opening results (for example, weighted
  Bonferroni). Persist allocations across campaigns under one research program;
  creating a new campaign cannot silently reset the program's budget.
- Across repeated cohorts use a predefined summable allocation schedule. Such control
  still requires valid individual tests: use time-aware block resampling or another
  justified method and record assumptions. Non-overlapping trades alone do not prove
  independence. Do not add an unvalidated online-FDR claim.
- Fix analysis dates or evidence thresholds before observing outcomes. Dashboard
  peeking must not become optional stopping; sequential decisions require a separately
  implemented and tested sequential protocol.

Acceptance:

- No job accessible to search can read the confirmation partition or interim scores.
  Modifying confirmation data cannot change trained weights or proposed formulas.
- A frozen candidate cannot mutate in place. Repeating a test does not reclaim its
  error-budget allocation. Inconclusive tests also retain their allocation in v1.
- Test purging with overlapping labels across fold boundaries and near outages.
- Use a predeclared multi-seed autocorrelated null simulation with a documented
  tolerance to check the confirmation procedure's false-positive behavior. Include
  a known-effect control. Do not require every random null realization to fail.
- A full fixture campaign produces distinct exploratory, frozen, confirmed,
  rejected, and insufficient-evidence states, with all tested variants counted.

### M6 — Integrate calibration and forward observation

Implementation:

- Reconcile mechanism-level trust with existing factor and trade comparison tools.
  A factor match does not certify trade timing, and a trade match does not certify alpha.
- Store versioned certificates with scope and evidence. Invalidate or require review
  after relevant feature, cost, schedule, simulator, strategy, or venue/size changes.
- Use existing approved replay adapters first. Where a mechanism lacks support,
  emit a concrete external implementation task and keep its status unverified.
  Replay jobs check the matching existing approval record and configured budget.
- Add forward observation for frozen candidates: persist decisions/predictions before
  outcomes mature, join later realized labels, and report net returns, coverage,
  drift, drawdown, turnover, and cost sensitivity under the declared execution model.
- Distinguish prospective shadow observation from delayed historical scoring. A
  next-day batch cannot claim it recorded a trading decision in real time.
- Preserve the frozen candidate while monitoring; any adaptation becomes a versioned
  challenger. Deterioration schedules research or changes its research status.

Acceptance:

- Changed simulator/cost/feature versions cannot reuse an incompatible certificate.
- Synthetic replay mismatches in event timing or fills are detected even if aggregate
  PnL happens to match. Reports separate calibration and statistical evidence.
- Future labels are unavailable at prediction recording time. Restarting observation
  neither duplicates predictions nor rewrites their prior values.
- An unapproved replay remains `waiting_approval`; approved replay can resume without
  repeated approval. Production promotion remains a separate human transition.

### M7 — Add adaptive search and conditional relationships

Implementation:

- Persist three observation types: feature -> return, feature <-> feature, and
  feature -> return conditional on a causal market-state definition.
- Add redundancy clusters, train-fitted residual/incremental contribution, and
  comparisons against current baseline blends. Avoid a full quadratic matrix for
  unbounded catalogs; use batched screening and capped representative sets.
- Extend proposals using registered causal operators and bounded AST mutations.
  Deduplicate canonical formulas and link close variants to their parents. Similar
  output series can be clustered for search efficiency but are not proof of identity.
- Allocate configurable portions of compute to exploration, refinement, and
  replication (initial example: 40/40/20). Use development evidence, novelty, cost,
  and uncertainty; do not rank solely by the count of historical survivors.
- Keep confirmation observations sealed during the cohort. Once closed, they may
  inform subsequent research, whose confirmation must use later fresh observations.
- Optional LLM proposals must conform to the experiment schema and registered tools.
  Record model/proposal provenance; deterministic code evaluates all numerical claims.
  Keep a deterministic generator available so the service works without an LLM.

Acceptance:

- Synthetic duplicate factors cluster; a complementary factor shows incremental
  value on the declared development protocol; a predefined conditional effect is found.
- Negative-direction effects remain eligible with inversion counted as a tested choice.
- Replay a fixed proposal history with the same seed and obtain the same next actions.
  Test that replication/exploration retain budget and new families are not starved.
- Exhausting the eligible search space causes an idle/wait state, not repeated
  duplicate scans. Every follow-up explains its parent evidence and expected question.

### M8 — Operate incrementally and prove unattended behavior

Implementation:

- Watch completed partition manifests/watermarks with a polling fallback. On new
  data, schedule only affected derivations, mature labels, monitoring, and planned
  development refreshes. Track structural gaps separately from transient lag.
- A revised partition invalidates dependent artifacts and creates new runs; preserve
  previous results. Changes propagate through warmup/state dependencies, potentially
  to the end of a run for recursive operators. Declare checkpoint/recompute semantics.
- Add a supervised service with restart handling and an operational runbook. Initially
  keep the new workflow opt-in; migrate `factory` to the scheduler after parity checks.
- Extend the read-only viewer with job/dependency state, freshness, budget use,
  failures, candidate lineage, relationship tables, and confirmation/calibration status.
- Daily summaries must report attempts, rejections and reasons, missing data,
  inconclusive tests, fresh evidence, and resource costs. Do not rank only winners.
- Add artifact retention/pinning checks and backup/restore instructions. Keep destructive
  shared-lake eviction outside the research daemon.

Acceptance:

- Run a 72-hour bounded pilot after a short real-data smoke run. Include a controlled
  service restart, transient provider failure, new partition, and corrected fixture
  partition. Persist the acceptance report and resource observations.
- No duplicate successful logical jobs, hidden budget reset, lost decision events,
  orphaned subprocesses, or partially published ready datasets.
- Idle time consumes negligible CPU; measured worker memory stays within the configured
  cap; growing catalogs do not cause full-history scans on every poll.
- Restore into a temporary directory from backup and resume pending fixture work.
- If insufficient fresh data has accumulated, report `insufficient_evidence` and keep
  the frozen protocol pending. Never substitute reused history to finish the pilot.

## 6. CLI and configuration targets

The core interfaces are implemented; consult the runbook for actual arguments.
In particular, freeze uses `--campaign-id`, `--candidate-id`, `--start` and `--end`.

```text
python src/cli.py research plan --campaign <yaml>
python src/cli.py research enqueue --campaign <yaml>
python src/cli.py research run --campaign-id <id> --max-jobs 10 --max-minutes 30
python src/cli.py research status --campaign-id <id>
python src/cli.py research explain --job-id <id>
python src/cli.py research retry --job-id <id>
python src/cli.py research freeze --campaign-id <id> --candidate-id <id> --start <time> --end <time>
python src/cli.py research serve --config <yaml>
```

`plan` performs read-only inspection and prints dependency actions, coverage gaps,
estimated resources, resolved versions, and unresolved assumptions. `enqueue` writes
durable state. `run` is bounded execution; `serve` is the supervised continuous worker.
Status/explain must work without initializing optional replay or LLM dependencies.

Campaign schema must require explicit market universe/execution sizes, data and
research roots, training/validation roles, feature/target recipes, cost policy,
trial policy, and resource limits. Discover dates and freeze them in the resolved
plan; do not embed an arbitrary historical date range as a universal default.

Recommended first campaign limits: one market pair, one compute worker, two existing
feature families, three declared horizons, and a bounded number of variants. Choose
the market/window by verified coverage. Record host-calibrated memory/disk limits
before launching; example percentages and limits in this plan are engineering starting
points, not statistically validated trading thresholds.

## 7. Coding-agent workflow and definition of done

For each milestone:

1. Read this plan, the affected modules, and applicable repository instructions.
   Inspect the worktree and preserve unrelated changes and research artifacts.
2. Implement the smallest end-to-end slice for that milestone. New capabilities
   should call existing engines; only refactor numerical code with parity evidence.
3. Add focused tests for the acceptance conditions, using fixtures and fake providers.
   Run the relevant existing tests. For schema/scheduler/data-alignment changes,
   also run the broader suite and report environmental limitations separately.
4. Update this milestone's status with code references, commands actually run,
   results, and remaining limitations. Do not mark a milestone complete from mocks
   when its gate explicitly requires a real provider or unattended run.
5. Update affected contracts and CLI docs. Leave the next milestone a concise
   handoff naming the first module to change and any upstream dependency.

Start with M0. The first useful checkpoint is M4: a restartable development experiment
with real artifacts. The statistical release gate is M5. Enable adaptive broad search
only after that gate, and call the service ready for unattended operation only after M8.

Do not require a profitable result to close an engineering milestone. Do not weaken
evaluation rules to obtain a survivor. Current model-driven search, new upstream
metric implementation, and production deployment are separate capabilities and must
not be smuggled into routine job execution.

### Final acceptance checklist

- [x] Missing supported derived data becomes a verified prerequisite job (Hase/Makalu fixture integration).
- [x] Research features obey bucket-end availability; labels remain separate and mature explicitly.
- [x] Every attempted experiment, including negative and inconclusive work, is traceable.
- [x] Corrected data/code/policies produce new identities and dependency invalidation.
- [x] Worker failure and restart preserve work, budgets, and logically unique decisions.
- [x] Search, validation, confirmation, and forward observation have distinct data roles.
- [x] Confirmation candidates/protocols are frozen and trial budgets persist across runs (fixture evidence).
- [x] Relationships and blends show effect size, redundancy and costs; unsupported uncertainty is explicitly unavailable.
- [x] Calibration evidence is scoped/versioned and independent of edge confirmation (fixture evidence).
- [ ] Existing backtest/promotion approvals are honored without repeated prompting.
- [ ] Incremental operation, bounded resource usage, backups, and recovery are demonstrated.
- [x] No autonomous live-order or production-promotion path is introduced.

## 8. Methodology references

- [Generalization in Adaptive Data Analysis and Holdout Reuse](https://papers.nips.cc/paper_files/paper/2015/hash/bad5f33780c42f2588878a9d07405083-Abstract.html)
  explains why repeatedly adapting to holdout results can overfit that holdout.
- [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
  motivates recording search breadth and accounting for selection bias. It is not
  a substitute for valid timestamps, executable economics, or fresh confirmation.
