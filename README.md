# Kilimanjaro

Kilimanjaro is the workspace for designing and operating an AI-assisted
workflow that repeatedly discovers, tests, and improves trading strategies.

The old Kilimanjaro strategy code has been moved to other repositories. This
repo should now stay lightweight: briefs, workflow specs, generated requirements,
and eventually orchestration glue only.

## Goal

Use agent dialogues to design a repeatable strategy-discovery loop:

1. Inspect available data and tools.
2. Propose trading hypotheses.
3. Convert hypotheses into testable strategy variants.
4. Run backtests through existing infrastructure.
5. Analyze results with strict anti-overfitting checks.
6. Promote useful findings into a strategy library or reject them with memory.
7. Repeat.

The output should be a disciplined research machine, not a one-off idea
generator.

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

## Agent Dialogue System

The "agentop dialogue" referenced below is a real tool, not a metaphor.

Path: `/home/lulurun/workspace/agentop`

Role:

- Orchestrates two agent CLI sessions (Claude Code, Codex, or Antigravity)
  collaborating on a shared task through a structured turn-taking protocol.
- Each agent runs in its own tmux session; a background orchestrator relays
  messages between them and stops when an agent signals `status:complete`.
- A scenario TOML file (under
  `agentop/src/agentop/dialogue/scenarios/`) defines both agents' role
  prompts. Useful built-ins for this project: `strategist-challenger.toml`,
  `researcher-skeptic.toml`, `trading-researcher-reviewer.toml`.
- All dialogue state persists to `~/.agent-dashboard/dialogues/<id>/`
  (`brief.md`, `scenario.toml`, `dialogue.log`, `progress.md`), so a dialogue
  can be inspected or resumed even if the orchestrating session is lost.

Starting a dialogue:

```bash
agentop dialogue start \
  --brief briefs/strategy-discovery-workflow.md \
  --scenario /home/lulurun/workspace/agentop/src/agentop/dialogue/scenarios/researcher-skeptic.toml \
  [--agent-a claude] [--agent-b claude] [--max-turns N]
```

Checking on it:

```bash
agentop dialogue list                # status of all dialogues
agentop dialogue stop <id>           # stop the orchestrator
agentop dialogue stop <id> --close   # also kill both agent tmux sessions
```

Full reference: `/home/lulurun/workspace/agentop/docs/dialogue.md` and
`/home/lulurun/workspace/agentop/docs/cli.md`.

## Proposed Workflow

### Phase 1: Workflow Direction Dialogue

Use an agentop dialogue to decide:

- what the strategy-discovery workflow should be;
- whether existing tools are enough;
- what additional tools are needed;
- how bronze order book/trade data should be used;
- how to control overfitting and false positives;
- what artifacts the workflow should produce.

Brief:

- `briefs/strategy-discovery-workflow.md`

Expected outputs (the canonical, approved set lives in `direction/`; see the
Direction section below):

- `workflow-strategy.md`
- `tool-gap-analysis.md`
- `hypothesis-taxonomy.md`
- `evaluation-framework.md`
- `autonomous-readiness-plan.md`

### Phase 2: Implementation Requirements Dialogue

Use another dialogue to translate the approved workflow into buildable
requirements.

Brief:

- `briefs/implementation-requirements.md`

Expected outputs (the canonical, approved set lives in `requirements/`; see
the Requirements section below):

- `requirements.md`
- `data-contract.md`
- `hypothesis-contract.md`
- `agent-loop-design.md`
- `implementation-tasks.md`
- `test-plan.md`
- `makalu-integration-contract.md`

### Phase 3: Code Agent

No dialogue unless the requirements are unclear.

Give the phase-2 docs to a code agent and implement the smallest useful slice.

Phase 3 task 1 (v1 artifact layout) is done — see Artifacts below.

## Artifacts

The v1 file layout from `requirements/implementation-tasks.md` task 1:

- `specs/` — hypothesis specs (`hyp-YYYYMMDD-NNN.yaml`)
- `ledger/` — append-only research memory (`events.jsonl`)
- `scan-reports/` — signal scan outputs (`scan-YYYYMMDD-NNN.json`)
- `handoffs/` — human-approved backtest handoff packages (`hyp-YYYYMMDD-NNN.yaml`/`.md`)
- `inventory/` — Makalu data inventory snapshots (`inventory-YYYYMMDDTHHMMSSZ.yaml`)

Each directory has its own `README.md` with the exact naming convention and
a pointer to the authoritative schema in `requirements/`. Canonical formats
and path patterns are defined once, in `requirements/data-contract.md`
("Canonical v1 Artifact Formats") — the per-directory READMEs link there
rather than restating it.

## Market Characterization (precedes the scanner loop)

Before testing any signal, two questions have to be answered first: which
markets are we actually targeting, and at any given time of day, what
horizon X is even capable of moving price more than the spread? Testing a
signal against a horizon that can't reliably clear the spread can't be
profitable regardless of what the signal predicts.

- `markets/registry.yaml` — the fixed, deliberately small tradeable
  universe: 5 venue/instrument groups across COINCHECK, GMO, and BITBANK,
  with fee and order-type constraints. Every entry is confirmed to exist as
  bronze data in Makalu.
- `markets/waves/`, `markets/graphs/` — the volatility-vs-spread "daily
  wave": for each market, at each 30-minute JST bucket, the probability of
  clearing 1x/2x/3x the spread at several tested horizons, computed
  directly from bronze `OrderBook` via `tools/volatility_wave.py`. Answers
  "what's the spread, volatility, and viable minimum horizon at this time
  of day" for a market, plus a graph of the daily shape. See
  `markets/README.md` for usage and a real data-coverage caveat (some
  markets only have 7 days of history so far, not enough to call the daily
  pattern stable yet).
- BINANCE isn't traded but is the industry-leading exchange; a prior
  strategy generated signal there and traded on GMO. `tools/lead_lag_wave.py`
  builds an equivalent daily wave for how strongly/stably one market leads
  another, computed directly from bronze `OrderBook` (1-second resampled
  cross-correlation) -- Makalu's own gold-layer `LeadLag` dataset only
  covered a window that didn't match any period this project actually
  scanned, so Kilimanjaro recomputes it directly per the Bronze-First
  principle, and Makalu's `populate LeadLag` CLI was separately extended
  (see "Makalu Population" below) to reach any market pair, not just the
  original fixed BINANCE-to-GMO-JPY set. Confirmed on real data: BINANCE
  leads GMO/BTC_JPY 49-82% of the time depending on time of day (peak
  23:30-01:00 JST), not a flat 81-97%. See `markets/README.md`.
- On the negative-fee, limit-only markets (GMO spot, BITBANK ETH/XRP spot),
  only limit orders are ever explored, so `tools/fill_probability.py`
  answers a different question: at distance X (bps) from the best bid/ask,
  what's the probability a resting limit order fills within Y seconds?
  Computed from bronze `Trade`+`OrderBook` as a documented upper-bound proxy
  (ignores queue position ahead of the order). Confirmed on real data
  (`GMO/BTC_SPOT`): fill probability decreases monotonically with distance,
  and dips during the same low-activity window seen in the volatility wave.

## First Slice Recommendation

Start with a workflow that does not generate strategy code yet.

Suggested v1:

1. Build a hypothesis registry.
2. Build a data-availability and feature-discovery report over Makalu bronze and
   existing silver/gold datasets.
3. Let agents propose hypotheses in a structured format.
4. Require each hypothesis to define:
   - market/instrument;
   - data required;
   - entry condition;
   - exit condition;
   - execution assumptions;
   - expected edge mechanism;
   - falsification tests;
   - backtest plan.
5. Convert only approved hypotheses into Himalaya-compatible implementation
   tasks.

This avoids premature code generation and forces the workflow to learn what
ideas are testable.

## Briefs

Briefs live under `briefs/`.

Current briefs:

- `briefs/strategy-discovery-workflow.md`
- `briefs/implementation-requirements.md`

## Direction

`direction/` holds the canonical, approved Phase 1 output — the source of
truth for Phase 2 and for restarting work later. It was produced by merging
two independent Phase 1 dialogue runs (claude/claude and codex/codex): the
claude/claude run was more grounded in the actual Makalu catalog/codebase and
was used as the base, with the codex/codex run's concrete numeric promotion
thresholds folded into `evaluation-framework.md`. The unmerged originals are
kept for reference (not committed) at `deliverables-a/` and `deliverables-b/`.

`briefs/implementation-requirements.md` points at `direction/` as its
Phase-1 source of truth.

## Requirements

`requirements/` holds the canonical, approved Phase 2 output. It was
produced by merging two independent Phase 2 dialogue runs (claude/claude
and codex/codex): the codex/codex run went further into concrete,
implementation-ready detail (exact file/schema formats, a full SQL-safety
AST allowlist, deterministic statistical methods, a CLI surface) and was
used as the base. Two things were folded in from the claude/claude run:

- the SQL-safety-library choice (`sqlglot`) and the autocorrelation-method
  choice (block bootstrap over Newey-West/HAC) are flagged in
  `requirements.md` as open decisions pending architect sign-off, not
  settled requirements, since codex's version presented them with the same
  confidence as requirements actually derived from phase-1 direction;
- `data-contract.md`'s inventory recipe now requires confirming feature-layer
  column existence per exchange/symbol, not just partition/date coverage —
  `Feature_Mid`/`Feature_NearHFT` column sets can differ by exchange, and a
  spec that passes a date-coverage check while referencing a nonexistent
  column for that exchange should fail at discovery, not silently fail later
  at scan time.

The unmerged originals remain at their original dialogue locations under
`~/.agent-dashboard/dialogues/` (not copied into this repo).

After merging, two implementation-feasibility spikes were run against the
real Makalu repo/data and `sqlglot`, surfacing a real gap: `sqlglot` parsing
needs explicit AST-walk checks beyond "no parse error" (subqueries and
multi-statement injection both parse silently), and `BookState`/`BookChange`
are not currently queryable due to a `catalog.yaml`/on-disk layer mismatch.
The second finding prompted a governing principle now in
`makalu-integration-contract.md` and reflected throughout `requirements/`:
**bronze (`Trade`, `OrderBook`) is the only dataset Kilimanjaro treats as
guaranteed; every other Makalu layer is a candidate** — used if already
populated and queryable, populated via Makalu's own CLI if its pipeline
supports it, derived fresh from bronze if not, or implemented as a new
dataset following Makalu's existing layer/pipeline conventions if no
pipeline produces the needed signal. No hypothesis is blocked merely because
a specific non-bronze dataset is missing, unqueryable, or unproven —
that's a routing decision, not a blocker.

## Viewer

`src/api.py` (Flask, port 9777) + `web/` (vanilla HTML/CSS/JS, no build
step) -- the same file layout convention as Makalu's own `src/api.py`/`web/`.
Read-only: lists every hypothesis (joining `specs/`, `ledger/`, and the
latest `scan-reports/` entry), and a detail view per hypothesis with its
full spec, ledger event history, and scan report metrics. Nothing here
writes to any of those directories -- mutating the research loop stays in
`tools/*.py`/the CLI, on purpose.

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

## Open Design Questions

- Is Makalu bronze data queryable efficiently enough for repeated agent-driven
  exploration?
- Do we need a feature catalog over existing Makalu datasets?
- Do we need a hypothesis registry with rejection memory?
- Should the agent propose pure rules, feature transforms, or full strategy
  variants?
- How do we force train/test/forward-split discipline?
- What result metrics should matter beyond PnL?
- How do we prevent strategy proliferation without evidence quality?
- What is the minimum loop that produces learning even when a strategy fails?
