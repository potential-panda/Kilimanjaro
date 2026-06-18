# Strategy Discovery Implementation Requirements Brief

## Purpose

Use this brief after the workflow-direction dialogue is complete.

The goal is to translate approved workflow/design docs into implementation-ready
requirements for Kilimanjaro.

Do not re-litigate the workflow direction unless the phase-1 docs are
contradictory or impossible to build.

Do not modify code during this dialogue.

## Inputs

Use the completed phase-1 dialogue deliverables as source of truth:

- `workflow-strategy.md`
- `tool-gap-analysis.md`
- `hypothesis-taxonomy.md`
- `evaluation-framework.md`

Phase-1 dialogue folder:

`/home/lulurun/workspace/Kilimanjaro/direction`

(Merged output of two independent Phase 1 dialogues — a claude/claude run
grounded in the actual Makalu catalog/codebase, merged with a codex/codex
run's concrete numeric promotion thresholds. The unmerged originals are kept
for reference at `deliverables-a/` and `deliverables-b/` in the same repo.)

Project paths:

- Kilimanjaro: `/home/lulurun/tmp/later/bot/kilimanjaro`
- potential-panda-core: `/home/lulurun/workspace/potential-panda-core`
- Makalu: `/home/lulurun/workspace/Makalu`
- Himalaya: `/home/lulurun/workspace/Himalaya`

## Product Intent To Preserve

Kilimanjaro is an orchestration workspace for AI-assisted strategy discovery.

It should:

- reuse Makalu for data;
- reuse Himalaya for backtests;
- reuse potential-panda-core for strategy/runtime abstractions;
- keep strategy hypotheses structured and auditable;
- record failed ideas and rejection reasons;
- prevent PnL-only or overfit promotion.

It should not:

- duplicate Makalu, Himalaya, or potential-panda-core;
- automatically deploy live strategies;
- accept a single profitable backtest as proof;
- hide execution assumptions;
- ignore costs, spread, slippage, latency, or liquidity.

## Translation Tasks

### 1. Workflow Requirements

Define:

- workflow stages;
- agent responsibilities;
- human approval points;
- state transitions;
- persisted artifacts;
- retry/revision loops.

### 2. Data Requirements

Define:

- how Kilimanjaro discovers Makalu data availability;
- required metadata for markets, symbols, dates, datasets, feature families;
- bronze/silver/gold feature contracts;
- data quality checks;
- query examples or helper APIs needed.

### 3. Hypothesis Registry

Define:

- hypothesis schema;
- strategy family taxonomy;
- expected edge mechanism field;
- data requirements;
- entry/exit/execution assumptions;
- falsification tests;
- lifecycle statuses;
- rejection memory;
- duplicate detection.

### 4. Backtest Handoff

Define:

- what makes a hypothesis ready for implementation;
- how to map a hypothesis to a potential-panda-core strategy/variant;
- how to create Himalaya run configs or parameter grids;
- train/test/forward split requirements;
- result ingestion and comparison.

### 5. Evaluation And Decision Rules

Define:

- metrics;
- thresholds;
- anti-overfitting checks;
- parameter sensitivity checks;
- regime robustness checks;
- acceptance/rejection/revision criteria;
- how failed ideas are remembered.

### 6. Implementation Plan

Define:

- smallest useful v1;
- likely files/modules to create;
- commands to run;
- tests to add;
- manual checks;
- explicit non-goals.

## Expected Deliverables

Write these files in the dialogue folder:

1. `requirements.md`
2. `data-contract.md`
3. `hypothesis-contract.md`
4. `agent-loop-design.md`
5. `implementation-tasks.md`
6. `test-plan.md`

## Acceptance Bar

The final docs should be detailed enough that a code agent can implement the
first slice without guessing the research workflow.

The first slice should be small. Prefer:

- data inventory;
- hypothesis registry;
- hypothesis specification format;
- evaluation protocol;
- manual backtest handoff;

over full autonomous code generation.
