# Continuous discovery operations

The opt-in `research` workflow uses the existing feature, correlation, blend and
return engines. Its SQLite state, input snapshots and reports live under the
campaign's explicit `research_root`. Provider jobs write into isolated attempt
directories. They do not modify the shared datalake or its configuration.

The initial campaign is [discovery-btc-pilot.yaml](../cycle/04_campaigns/discovery-btc-pilot.yaml).
Its July 10 window was chosen from verified Binance/GMO MarketPrice and Binance
OBIFeature coverage. It is a historical smoke test, not an out-of-sample claim.
The declared zero fee is a pilot assumption; set actual venue fees before using
economic results. Bid/ask execution and one additional round-trip slippage bps
are included. Neither a correlation nor a positive development result certifies alpha.

## Start and inspect

Run from the repository with its Python environment and installed Hase/Makalu:

```sh
python src/cli.py research plan --campaign cycle/04_campaigns/discovery-btc-pilot.yaml
python src/cli.py research enqueue --campaign cycle/04_campaigns/discovery-btc-pilot.yaml
python src/cli.py research run --database cycle/discovery-pilot/17_ledger/research.sqlite --max-jobs 10 --max-minutes 10
python src/cli.py research status --database cycle/discovery-pilot/17_ledger/research.sqlite
python src/cli.py research summary --database cycle/discovery-pilot/17_ledger/research.sqlite
```

`plan` is read-only. `enqueue` creates immutable campaign/plan artifacts, registers
development exposure and all feature-direction/horizon/blend trials, and creates
the job DAG. `run` admits at most the requested number of jobs. `explain --job-id`
shows prerequisites, attempts, errors, lease and artifact information. `retry
--job-id` explicitly retries a failed or waiting job; it does not refund admissions.

Budget admissions, including failed attempts, accumulate across all campaigns in
the same program. Daily limits use UTC. Replanning, a new seed, a corrected
partition and a new proposal do not reset them. `max_jobs` is the program's total
admission ceiling; `daily_jobs` is its daily ceiling. Raising these fields is an
explicit operator policy change. A new program is a new statistical family and
must not be used to evade an existing program's bounds.

The read-only web viewer adds a Research tab. Set
`KILIMANJARO_RESEARCH_DB` to the absolute SQLite path when starting `src/api.py`.
`GET /api/research` provides job state, admissions, freshness, candidate states and
proposal lineage; `/api/research/<run-id>` includes development reports and
relationships. Open confirmation outcomes are not exposed by these endpoints.

## Continuous operation

```sh
python src/cli.py research serve --config cycle/04_campaigns/discovery-service.yaml --once
python src/cli.py research serve --config cycle/04_campaigns/discovery-soak.yaml
```

The smoke configuration executes one fixed campaign. The soak configuration enables
at most five deterministic proposals, with a 40/40/20 exploration/refinement/
replication schedule while those lanes have eligible work. All retain program
admission limits. The service stops after 72 hours; its deadline survives restart.
The unchanged service configuration will remain expired afterward. To start a new
authorized pilot, supply a new bounded configuration. No default production daemon
or legacy `factory` behavior is changed.

`rolling: true` moves the configured development spans to the last closed JST
day, preserving their durations. Enable it only when that market universe has
the required coverage. A missing raw partition waits; a supported missing derived
partition becomes a prerequisite. The April 2–9 known raw gap has a separate
structural-gap status. Current JST days are not treated as complete.

Idle polls inspect only stat information for the campaign's known dependency
paths. A changed path or source implementation causes a new resolved plan.
`audit_every_polls` also forces periodic content rehashing. Every run retains its
input checksums and source/package versions, including editable provider sources.
Recursive feature state is recomputed from the declared warmup; v1 conservatively
recomputes the affected campaign rather than splicing recursive state across runs.
Shared derived datasets may be reused; outputs are not cached across run identities.

The worker uses one process lock per database, one active lease, process groups,
bounded attempts, timeouts, per-file limits, a DuckDB memory setting and sampled
resident memory across the child process tree. A killed parent cannot admit an
overlapping worker while its surviving child holds the inherited lock. Resource
events record elapsed seconds and peak sampled RSS. Sampling can briefly overshoot;
use the supplied systemd unit's cgroup memory limit for a host-enforced bound.

[tools/kilimanjaro-research.service.example](../tools/kilimanjaro-research.service.example)
is a user-service template with process-group cleanup and restart on failure.
Check its Python and checkout paths before installing it. SIGTERM/SIGINT stops
admission and reaps an active child. An interrupted attempt remains spent and is
retried under its configured attempt limit. Recovery of a dead lease waits for its
expiry (normally at most 60 seconds). Incompatible old code becomes `needs_tooling`;
resolve a new plan rather than overriding provenance.

## Evidence lifecycle

1. Development fits feature normalization, signs, blends and score scaling on
   training only. Actual first eligible quotes produce labels and returns; labels
   whose actual exit crosses the evaluation boundary are excluded. Bucket-derived
   inputs become available at bucket end, with explicitly bounded forward fill.
2. Validation ranks development candidates. Reports retain all blends, failures,
   missing estimates, coverage and fixed additional-cost sensitivities. Correlation
   profiles, redundancy clusters, residual contribution and causal-state effects
   remain descriptive; they are not additional confirmation tests.
3. Freeze an eligible candidate with a future, fixed, whole-day window:

   ```sh
   python src/cli.py research freeze --database <sqlite> --campaign-id <run-id> --candidate-id <candidate-id> --start <timezone-aware-start> --end <timezone-aware-end>
   ```

   Freeze requires positive train/validation means, enough development trades and
   coverage. Confirmation lasts at least 20 days and the policy minimum. It pins
   the candidate recipe, feature code, costs, endpoint and evidence thresholds.
   Test number `n` receives `program_alpha / (n * (n + 1))`; allocations persist
   across cohorts, retries and inconclusive results. An open confirmation interval
   blocks overlapping development in this database, including another program.
4. At the fixed end date, the service or `research confirm --freeze-id <id>`
   schedules isolated feature evaluation and the frozen recipe, without fitting.
   Missing data waits. Incomplete coverage, too few trades/days or inadequate
   resampling resolution returns `insufficient_evidence`, retaining its allocation.
   Code changes require a new freeze and later fresh window.

The endpoint is mean daily net bps, including zero-return non-trading days. The
one-sided circular block-bootstrap test and interval use a deterministic seed and
blocks of approximately `n_days ** (1/3)`. Drawdown, turnover and positive-day
concentration are additional declared checks. The null regression test covers one
autocorrelation model; it does not establish validity under arbitrary dependence,
nonstationarity or market changes. No optional stopping or online-FDR claim is made.

The role barrier is enforced by the registered workflow and read interfaces. This
is a trusted local process, not an OS sandbox for arbitrary plugins with access
to the same lake. Use one shared research database for a program. Creating another
database bypasses its exposure/accounting history and is not a fresh-data protocol.

## Calibration and forward observation

`research calibrate` compares supplied Parquet exports using existing factor/trade
comparators and stores an immutable, version-scoped certificate. A factor export
has `ts,value`; trade exports use the existing scan and Enoshima column contracts.
Create the scope with `research.calibration.scope_for`, then pass its JSON through
`--scope`, alongside `--offline`, `--online`, `--kind` and a declared `--tolerance`.
Certificates include checksums, the exact feature/market/size/cost/implementation
scope and the online implementation identifier. Factor, trade and statistical
evidence remain separate. Changed scopes cannot reuse a certificate.

The existing online aquila adapters do not implement the general frozen
EpisodeReturn recipe introduced here. `replay_readiness` emits a concrete adapter
task and recognizes a matching human approval by hypothesis and calibration scope.
It returns `waiting_approval` without that record and `needs_tooling` with approval
but no registered adapter. No remote replay spend or upstream code changes occur.

`research observe --freeze-id <id> --input <json>` ingests causal feature values
from an event-time feed. JSON contains `decision`, `available_at`, and `features`
keyed by the frozen recipe's F-columns. It requires recording within one second
of the decision and refuses rewrites. `research label` accepts `decision` and
observed `quotes` (`ts,bid,ask`) after maturity, producing an immutable fixed-horizon
shadow diagnostic. These diagnostics are not the EpisodeReturn confirmation test.
Daily parquet backfills cannot be presented as prospective decisions. Connecting
a real event-time feed and validating online feature parity remain deployment work;
there are no claimed live predictions or calibrated new mechanisms in this pilot.

## Recovery, backup and retention

```sh
python src/cli.py research backup --database <sqlite> --destination <new-backup.sqlite>
```

Use SQLite's backup API, not a copy of a live main file without its WAL. Back up
the complete research root and published ledger alongside it: the database holds
absolute artifact paths, and SQLite backup alone is not an artifact backup.
Stop the original worker before resuming a restored copy. Restore the root at the
same absolute path, or mount a copied tree there in an isolated recovery host.
Verify artifact manifests before resuming; `run` verifies dependencies again.

All successful and frozen artifacts are retained in v1. Disk pressure stops new
work; the daemon performs no eviction. Do not manually delete referenced attempts,
snapshots, manifests, certificates or frozen protocols. A torn final JSONL append
is archived as `.torn-*` before outbox replay repairs it; complete earlier events
are preserved. Corruption inside a complete line fails visibly.

Per-poll records under `05_experiments/service/` include admissions, attempts,
errors, candidate states, freshness and work duration. Use `research summary` for
the current picture. Before declaring unattended operation accepted, review the
entire 72-hour log, restart behavior, missing/revised data handling, RSS/cgroup
limits and backup restoration, including failures and inconclusive outcomes.
