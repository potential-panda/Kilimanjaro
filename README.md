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

Expected outputs:

- `requirements.md`
- `data-contract.md`
- `agent-loop-design.md`
- `implementation-tasks.md`
- `test-plan.md`

### Phase 3: Code Agent

No dialogue unless the requirements are unclear.

Give the phase-2 docs to a code agent and implement the smallest useful slice.

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
