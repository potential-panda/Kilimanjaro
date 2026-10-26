# Continuous discovery implementation status

Updated 2026-10-26 (Asia/Tokyo). The core research workflow is implemented. The
72-hour pilot is running; fresh-data confirmation and live mechanism integration
have not been demonstrated. This is not a claim of profitable alpha or production
readiness.

## Milestones

| Milestone | Implemented evidence | Remaining acceptance |
|---|---|---|
| M0 baseline | CLI repaired, retry dedup corrected, packets publish before terminal status, explicit data roots resolved at call time | No known blocker in the baseline suite |
| M1 contracts and state | Campaign JSON Schema plus semantic validation; content/source/package identities; immutable artifacts; WAL SQLite jobs, attempts, exposures, trials, outbox, freezes and certificates | Single-host trusted-process boundary; no distributed coordination |
| M2 providers | Recursive plans, schema/footer/timestamp/checksum validation, JST partitions, isolated Hase/Makalu/OBI adapters, explicit missing/structural gaps | Hase MarketPrice and Makalu BookState exercised with real installed providers on fixtures; other adapters require further market-specific integration coverage |
| M3 worker | Leases and fencing, inherited process lock, process-group cleanup, durable program budgets, bounded retries, disk/time/RSS controls, outbox replay and torn-tail recovery | Long-duration resource behavior is part of the running soak |
| M4 pipeline | Actual existing feature/correlation/blend/EpisodeReturn engines; 1-second availability shift; frozen train normalization; bid/ask plus costs; complete reports | Short real-data gate passed; larger windows need appropriate budgets |
| M5 confirmation | Exposure isolation, actual-label boundary purging, prospective fixed-window freeze, summable program alpha allocation, coverage/evidence gates, daily block test and interval | No eligible real candidate or 20-day fresh result; test validity beyond the declared null fixture needs further research |
| M6 calibration/observation | Existing comparison engines, scoped immutable certificates, matching-approval readiness, prospective ingest and matured quote diagnostics | General EpisodeReturn Himalaya adapter and event-time feed connection are external integration tasks; pilot mechanisms remain unverified |
| M7 proposals/relationships | Deterministic bounded exploration/refinement/replication, semantic dedup and lineage, signed effects, redundancy, incremental and causal-state profiles; all five pilot follow-ups completed | Wider-market validation remains; wide catalogs and LLM proposals are deferred |
| M8 operations | Cheap dependency polls, periodic checksum audits, rolling-window option, read-only API/viewer, resource records, backup/restore test, persistent service deadline | Full 72-hour acceptance report is pending; legacy factory remains unchanged |

Implementation lives in `src/kilimanjaro/research/`, with data adapters under
`src/kilimanjaro/data/`, CLI registration in `src/command/research.py`, and the
campaign contract in `cycle/01_schemas/research_campaign.schema.json`.

## Verification completed

- Full repository suite: **248 passed, 13 skipped, 24 subtests passed**. The only
  warning is an upstream reactivex deprecated datetime call in the Makalu test.
- Final focused research suite after service/proposal refinements: **20 passed**.
- Ruff passes for all changed Python files; `node --check web/app.js` passes.
- Actual synthetic delayed-effect pipeline recovers the positive direction. The
  same fills lose after adding 100 bps in fees, with the cost charged exactly once.
- Changing future quote observations leaves prior features unchanged. Frozen
  windows reject overlapping search, including through a renamed program.
- Multi-seed null fixture: **3/80** confirmations for AR(1), rho=0.3, 240 days,
  allocated alpha=0.025 and 499 resamples. Seeds 0–79 and tolerance <=9 were
  declared in the test. Positive/negative controls and insufficient coverage pass.
  This finite regression diagnostic does not validate arbitrary dependence.
- Equal-PnL replay artifacts with altered fill timing fail calibration. Predictions
  cannot be rewritten or labeled before maturity; changed certificate scopes fail reuse.
- Idle service ticks avoid replanning unchanged lake inputs. Corrected partitions
  create new runs and retain old artifacts. A SQLite backup resumes pending fixture
  work without redoing completed jobs. Failed attempts retain their admissions.
- Installed Hase and Makalu ran actual derivations against isolated synthetic bronze.

## Real-data smoke result

Campaign: [discovery-btc-pilot.yaml](../cycle/04_campaigns/discovery-btc-pilot.yaml).
It uses Binance BTC_USDT and GMO BTC_JPY at execution size 0.002, with Binance
momentum and imbalance. Training and validation are consecutive one-hour windows
on 2026-07-10, with horizons 5, 10 and 30 seconds.

The final implementation's initial run is
`run-31d7b0069023a603c314c1f5d650575d`. Its nine logical jobs completed, consuming
29,377,558 bytes of input snapshots and registering 24 feature-direction/blend
trials. All 12 evaluated blends had negative validation means after costs. The
highest mean was approximately **-2.6182 bps/trade** over 335 validation trades;
coverage was 100%. No candidate was frozen. The one-hour sample does not support
annualized risk statistics; those remain unavailable in the report.

Earlier smoke runs remain preserved under their source versions. They are not
independent statistical replications. Peak sampled worker RSS in the first smoke
was approximately 347 MB, below the 2,048 MB worker budget.

The final packet is under:

```text
cycle/discovery-pilot/05_experiments/run-31d7b0069023a603c314c1f5d650575d/
  attempts/job-cfb2073515dd6b1764dfed325a63d681/
    6fc104ec71f84a50a47926eb2495d9a1/artifacts/REVIEW_PACKET.md
```

## Running 72-hour pilot

- Unit: `kilimanjaro-research-pilot.service` in the current user's systemd manager.
- Configuration: [discovery-soak.yaml](../cycle/04_campaigns/discovery-soak.yaml).
- Start: **2026-10-26 07:28:50 JST** / 2026-10-25 22:28:50 UTC.
- Scheduled end: **2026-10-29 07:28:50 JST** / 2026-10-28 22:28:50 UTC.
- At most five adaptive proposals, 120 program admissions total, 40 admissions
  per UTC day, 2,048 MB artifact budget, and 2.5 GB cgroup memory cap.
- State: `cycle/discovery-pilot/17_ledger/research.sqlite`.
- Summaries: `cycle/discovery-pilot/05_experiments/service/`.

The initial systemd task limit of 64 was too small for the combined native-library
thread pools. A correlation child exited with `std::system_error`/thread creation
failure. Its attempt was recorded and charged. The task limit was corrected to
256, the service was deliberately restarted, and the same logical job succeeded
on retry. All nine jobs then completed. The restart preserved the service deadline,
prior successful artifacts and admissions. Cgroup peak memory after restart was
approximately 355 MB at that checkpoint. This observed failure/recovery is retained
in the ledger and attempt logs.

Inspect or stop the pilot:

```sh
systemctl --user status kilimanjaro-research-pilot.service
journalctl --user -u kilimanjaro-research-pilot.service
python src/cli.py research summary --database cycle/discovery-pilot/17_ledger/research.sqlite
systemctl --user stop kilimanjaro-research-pilot.service
```

The transient service restarts on failure and exits normally at its persisted
deadline. It is not installed as a permanent boot service. Repeated fixed-window
work is deduplicated; exhausted daily admissions wait for the next UTC day.
The next service tick created and enqueued all five allowed proposals and completed
ten additional jobs without errors. At that checkpoint, 37 jobs had succeeded,
38 admissions had been spent across preserved smoke runs and follow-ups, and the
outbox had no unpublished events. The remaining queue is subject to the same daily
ceiling; it is not permission to increase the pilot's budget.

Checkpoint before committing, **2026-10-26 11:58 JST**: all five follow-up
experiments have completed. Across the preserved smoke versions and follow-ups,
72 logical jobs succeeded in 73 charged attempts. The daily budget admitted
40 attempts on October 25 UTC and 33 on October 26 UTC, demonstrating that pending
work resumed after the UTC day changed. There are no pending/failed jobs or
unpublished events at this checkpoint. All eight reports contain 12 blends each;
every validation mean remains negative, and no candidate has been frozen.
The service is still active after approximately 4.5 hours and 272 ticks, with no
controller errors and approximately 510 MB peak cgroup memory since the controlled
restart. Its remaining job is observation through the persisted 72-hour deadline;
this checkpoint does not complete the soak gate.

After the deadline, inspect every tick and attempt, confirm no duplicate successful
job IDs, no unpublished events or leftover running leases, review resource peaks,
and back up the research root. Write the final soak acceptance report here. Until
then, M8's 72-hour gate is **pending**. Do not substitute historical results for
fresh confirmation or treat an idle fixed-window pilot as evidence of a live feed.
