# Kilimanjaro

Kilimanjaro is a disciplined, self-feeding research machine that discovers and
tests trading-signal formulas — the built system, not a planning workspace.

## The loop

1. **generate** a formula → 2. **scan** it over a parameter grid → 3.
**verify/report** → 4. **filter** survivors → 5. build a qate strategy → 6.
Himalaya backtest → 7. prod. Steps **1–4 run autonomously**; **5–7 are
human-gated** (the driver attaches evidence to the ledger and parks survivors at
a review packet — it never promotes).

The moat is the reward function — a judge that says *no* cheaply and honestly
(train/holdout rank stability, block-bootstrap + FDR, a bid/ask cost policy,
path-aware Martin/Calmar metrics, and per-mechanism scan↔Himalaya calibration).

Start here: **`CLAUDE.md`** (architecture, CLI, data paths, cost/verification
policy) and **`doc/direction/alpha-factory-roadmap.md`** (what's built and what's
next). `src/cli.py` is the single entry point.

## Existing Tools

Kilimanjaro should reuse existing systems instead of rebuilding them.

### potential-panda-core

Path: `/home/lulurun/workspace/potential-panda-core`

Role:

- Core trading abstractions.
- Strategy base classes and variant contracts.
- Exchange and simulator abstractions.
- Shared utilities and storage primitives.

Use it when a generated strategy needs to become executable code.

### Makalu

Path: `/home/lulurun/workspace/Makalu`

Role:

- Market data lake.
- Data pipeline from raw tick data into layered Parquet datasets.
- Bronze data: raw trades and order book snapshots.
- Silver/gold data: derived microstructure metrics and signals.
- SQL/query interface for data inspection.

Makalu is currently the most important source of research edge because the
bronze data contains order book snapshots and trades.

### Himalaya

Path: `/home/lulurun/workspace/Himalaya`

Role:

- Backtest engine.
- Replays stored market data through a simulator gateway.
- Runs strategies and parameter grids.
- Writes results to InfluxDB, Parquet, and run logs.

Use it for candidate strategy evaluation, optimization, retesting, and
cross-period validation.

## Product Thesis

The valuable asset is not an individual strategy idea. The valuable asset is a
closed-loop workflow that can repeatedly search a large hypothesis space while
remaining skeptical about false positives.

The workflow should treat bronze data as the raw material:

- order book imbalance;
- queue/depth changes;
- spread and microprice behavior;
- trade aggression;
- burst/withdrawal/refill behavior;
- lead-lag and volatility/spread regimes;
- liquidity shocks around short horizons.

Potential edge is likely in microstructure patterns that are hard to see from
bar data alone.

## Non-Goals

- Do not rebuild Makalu, Himalaya, or potential-panda-core here.
- Do not turn AI output directly into a live strategy.
- Do not accept PnL-only evidence.
- Do not optimize parameters on one period and call it discovery.
- Do not ignore fees, spread, slippage, queue position, latency, or liquidity.
- Do not let the agent repeatedly rediscover rejected ideas without new
  evidence.

## Running the loop

Everything runs through `python src/cli.py <command>`:

| command | what it does |
|--|--|
| `generate` | the generator — propose new formula specs into the queue (deduped, data-gated) |
| `grid {flip,expr,obi}` | run a parameter-grid scan (train/holdout split + rank stability) |
| `loop` | the scanner loop — one pass over the spec queue (validate → dedup → scan → verdict → ledger → review packet) |
| `factory` | the cadence — bounded or daemon generate+scan, never promotes |
| `ledger {append,search}` | the append-only research memory |
| `verify-scan` | scan↔Himalaya-backtest fidelity check (ORDER + EVAL) |
| `populate` / `verify` | populate a dataset; verify a dataset vs backtest InfluxDB |

Promotion (steps 5–7) stays human: the loop parks survivors at a `REVIEW_PACKET.md`
stamped CALIBRATED / TREND-ONLY; a human records the `human_approval` and drafts a
handoff (`cycle/18_handoffs/`). See `doc/requirements/agent-loop-design.md` for the state
machine.

## Artifacts

All research-cycle data lives under `cycle/`, numbered in pipeline order (see
`cycle/README.md`). The v1 file layout (canonical formats in
`doc/requirements/data-contract.md`):

- `cycle/11_hypotheses/` — hypothesis specs (`hyp-YYYYMMDD-NNN.yaml`)
- `cycle/12_specs/` — the loop's working scan specs (expression formulas + sweeps)
- `cycle/15_return_eval/grid_scan/{flip,expr,horizon}/` — grid-scan outputs (`output.csv`, `output_stable.csv`, `params.json`, …)
- `cycle/17_ledger/` — append-only research memory (`events.jsonl`)
- `cycle/18_handoffs/` — human-approved backtest handoff packages (`hyp-YYYYMMDD-NNN.yaml`/`.md`)
- `cycle/03_inventory/` — Makalu data inventory snapshots (`inventory-YYYYMMDDTHHMMSSZ.yaml`)
- `cycle/01_schemas/` — JSON Schemas (hypothesis spec + ledger event)

Each directory has its own `README.md` with the exact naming convention and
a pointer to the authoritative schema in `doc/requirements/`. Canonical formats
and path patterns are defined once, in `doc/requirements/data-contract.md`
("Canonical v1 Artifact Formats") — the per-directory READMEs link there
rather than restating it.

## Market universe

Before testing any signal: which markets are we targeting, and at a given time of
day, what horizon can even move price more than the spread? Testing a signal
against a horizon that can't clear the spread can't be profitable regardless of
what it predicts.

The tradeable universe is defined in code: `PAIRS` in
`src/kilimanjaro/driver/generate.py` (a deliberately small set of BINANCE→GMO /
BITBANK pairs), and each generated spec declares its own `markets`/`target`.
Proposals are **data-gated** — a (family, market) is only emitted if the signal
source actually has the feature and the target has `MarketPrice`, checked against
Makalu bronze via `available_features()`; execution sizes are read from the data,
never guessed.

The volatility-vs-spread "wave" characterization (probability of clearing 1–3×
the spread at several horizons, plus a BINANCE→GMO lead-lag wave and, on
limit-only venues, a resting-limit fill proxy) is **not a standing artifact** —
regenerate it on demand from bronze `OrderBook` with
`tools/{volatility_wave,lead_lag_wave,fill_probability,regime_window}.py`.

## Docs

- **[`doc/continuous-discovery-implementation-plan.md`](doc/continuous-discovery-implementation-plan.md)** — next-version implementation plan: data planning, durable jobs, connected research stages, fresh-data confirmation, adaptive search, and unattended operation; milestone acceptance gates for coding agents.
- **[`CLAUDE.md`](CLAUDE.md)** — architecture, CLI, data paths, cost + verification policy (start here).
- **[`doc/direction/alpha-factory-roadmap.md`](doc/direction/alpha-factory-roadmap.md)** — the tracked roadmap to a full alpha discovery factory.
- [`doc/scan-evaluation.md`](doc/scan-evaluation.md) — how every grid scan is judged: train/holdout split, rank-stability selection, verdict taxonomy.
- [`doc/calibration.md`](doc/calibration.md) — calibrating the scan against Himalaya, per mechanism; the calibration registry (CALIBRATED / trend-only).
- [`doc/overlapping-returns.md`](doc/overlapping-returns.md) — why overlapping-horizon returns inflate significance, the defenses, and how an episode is defined.
- Design/contract reference: `doc/direction/{workflow-strategy,evaluation-framework,hypothesis-taxonomy}.md` and `doc/requirements/*`.

## Design & contracts

`doc/direction/` holds the approved design (workflow, evaluation framework,
hypothesis taxonomy); `doc/requirements/` holds the contracts (data contract,
hypothesis contract, agent-loop state machine, Makalu integration, test plan).

Governing principle (`makalu-integration-contract.md`): **bronze (`Trade`,
`OrderBook`) is the only dataset treated as guaranteed; every other Makalu layer
is a candidate** — used if already populated, populated via Makalu's CLI if its
pipeline supports it, or derived fresh from bronze otherwise. No hypothesis is
blocked merely because a non-bronze dataset is missing — that is a routing
decision, not a blocker.

## Viewer

`src/api.py` (Flask, port 9777) + `web/` (vanilla HTML/CSS/JS, no build
step) -- the same file layout convention as Makalu's own `src/api.py`/`web/`.
Read-only, two views:

- **Scans** (`#/scans`) -- every grid-scan run under `cycle/15_return_eval/grid_scan/`
  (flip / horizon / expr), newest first, with verdict counts. A run's
  detail view renders each CSV (`output.csv`, `output_stable.csv`,
  `output_monthly.csv`, ...) as a table sortable by any column (click a
  header to sort, click again to flip direction), with a text filter,
  verdict badges, and the run's `params.json`.
- **Hypotheses** (`#/hyps`) -- the spec+ledger join: one row per
  hypothesis with its current status, and a detail view with the full
  spec and event history (event headlines shown inline).

Nothing here writes to any of those directories -- mutating the research
loop stays in the CLI, on purpose.

```bash
python src/api.py   # serves http://localhost:9777
```

## Makalu Population

Kilimanjaro never recomputes something Makalu already implements as a
`populate` pipeline -- it checks Makalu's coverage for the needed
exchange/symbol/date range first, calls Makalu's `populate` CLI if missing,
then reads the result. Two real additions made to Makalu's own pipeline to
support this (in `/home/lulurun/workspace/Makalu`, not this repo):

- **`populate Return`** -- forward mid-price returns at fixed horizons
  (e.g. `return_30s`, `return_120s`, `return_300s`) from `MarketPrice`, the
  same shared, reusable quantity many different signal-scanning purposes
  would otherwise each recompute independently via their own ASOF join.
- **`populate LeadLag --lead-market/--lag-market`** -- an explicit-market
  path alongside the original `--pair` shorthand (`btc`/`eth`/`xrp`/`sol`,
  fixed to GMO as the lag market), for any other pair, e.g.
  `BINANCE:BTC_USDT` &rarr; `GMO:BTC_SPOT`.
- **Gap-skip fix in `pipeline/framework.py`/`stages.py`** -- Makalu's
  pipeline used to crash outright (`KeyError: 'ts'`) on any chunk with no
  underlying data; confirmed for real that several markets have scattered
  raw-data gaps (e.g. `BINANCE/ETH_USDT` is missing an entire month). Fixed
  to log a warning and skip the gap instead of crashing the whole populate
  command.

## Continuous discovery

The opt-in `python src/cli.py research` workflow provides persistent jobs, isolated
Hase/Makalu prerequisites, development experiments, frozen confirmation protocols,
bounded proposals, and a read-only Research viewer. See the
[runbook](doc/continuous-discovery-runbook.md) and
[implementation/validation status](doc/discovery-implementation-status.md).
