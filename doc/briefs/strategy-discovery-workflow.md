# Strategy Discovery Workflow Brief

## Purpose

Use this brief with an agentop dialogue to decide the high-level autonomous
research workflow for AI-assisted trading strategy discovery.

Stay at the direction/design level. Do not implement code during this dialogue.

## Goal

Design a repeatable workflow where AI agents can discover, pre-screen, reject,
improve, remember, and eventually backtest trading strategy hypotheses using the
existing potential panda ecosystem.

The workflow should answer:

> How can an AI agent repeatedly find plausible, testable trading strategies
> from our existing data and tools without falling into overfitting or PnL-only
> thinking?

The dialogue should focus on the research machine, not on one trading strategy.
The most important question is:

> What workflow and minimum tools are required for an AI agent to repeatedly
> discover, pre-screen, remember, and escalate trading strategy hypotheses using
> Makalu, Himalaya, and potential-panda-core?

Two concepts should be treated as first-class design topics:

- Signal scanner: the fast feedback loop that tests whether a feature or signal
  has predictive value before running a full Himalaya backtest.
- Research ledger: the persistent memory that records hypotheses, scan results,
  backtest results, decisions, failures, and next experiments.

## Existing Tooling

### potential-panda-core

Path: `/home/lulurun/workspace/potential-panda-core`

Known role:

- trading abstractions;
- strategy base and variant contract;
- simulator/exchange abstractions;
- shared models and utilities.

Question for the dialogue:

- Is this enough to express generated strategy ideas?
- What contract should a discovered strategy eventually satisfy?

### Makalu

Path: `/home/lulurun/workspace/Makalu`

Known role:

- data lake and data pipeline;
- bronze raw trade and order book snapshot data;
- silver/gold derived microstructure metrics;
- SQL/query interface;
- backtest orchestration hooks.

Most valuable current asset:

- bronze order book snapshots and trades.

Question for the dialogue:

- How should agents use bronze data to discover strategy hypotheses?
- Is existing silver/gold sufficient, or do we need more feature extraction?
- Do we need a feature catalog, data availability report, or query helper?
- Can Makalu support a fast signal scanner over bronze/silver/gold datasets?
- What data access pattern lets an agent scan many ideas cheaply?

### Himalaya

Path: `/home/lulurun/workspace/Himalaya`

Known role:

- backtest engine;
- replays stored market data through simulator gateway;
- runs strategy variants and parameter grids;
- writes results to run logs, InfluxDB, and Parquet.

Question for the dialogue:

- Is Himalaya enough for strategy evaluation?
- What backtest protocol is required to prevent overfitting?
- What result fields and diagnostics should the agent read?
- What evidence should be required before a hypothesis is promoted from signal
  scan to full Himalaya backtest?

## Desired Workflow Design

The dialogue should design a loop with clear stages. It should explicitly
separate cheap signal-quality screening from expensive strategy backtesting.

Possible stages:

1. Data inventory
   - What markets, periods, exchanges, symbols, and datasets are available?
   - What bronze/silver/gold features exist?
   - What data gaps or quality issues matter?

2. Hypothesis generation
   - Generate microstructure hypotheses from order book/trade behavior.
   - Each hypothesis must explain the expected edge mechanism.
   - Avoid vague "use ML" or "find patterns" ideas.

3. Hypothesis specification
   - Define data inputs.
   - Define feature or signal expression.
   - Define intended prediction horizon.
   - Define expected edge mechanism.
   - Define possible entry/exit interpretation if the signal survives scanning.
   - Define execution assumptions.
   - Define costs, spread, slippage, latency, and liquidity constraints.
   - Define falsification tests.

4. Signal scan
   - Evaluate the feature or signal before building a strategy.
   - Measure predictive value against future returns or existing labels.
   - Report hit rate, IC, decay, sample count, significance, and regime
     stability.
   - Reject weak signals before spending time on full backtests.

5. Feasibility review
   - Can existing tools test it?
   - Is required data available?
   - Does it require new metrics or infrastructure?
   - Is it too likely to be overfit?
   - Is the signal scan strong enough to justify a full backtest?

6. Research ledger update
   - Record the hypothesis, feature definition, scan metrics, decision, and next
     action.
   - Record rejected ideas and why they failed.
   - Prevent rediscovering the same idea unless new evidence appears.

7. Backtest implementation handoff
   - Convert approved hypotheses into implementation tasks.
   - Do not generate strategy code until the hypothesis is specific enough.
   - Do not run Himalaya for every idea; use it for candidates that pass
     pre-screening.

8. Backtest and evaluation
   - Run with train/test/forward splits.
   - Require multiple periods and regimes.
   - Evaluate beyond PnL.

9. Decision memory and iteration
   - Refine hypotheses based on diagnostics.
   - Promote only robust strategy families.
   - Use the research ledger to choose the next experiment.

## Signal Scanner Design Questions

The dialogue should decide what a useful signal scanner must do.

Questions to answer:

- What input format should the scanner accept?
  - SQL expression;
  - Makalu feature name;
  - short Python feature transform;
  - structured hypothesis spec.
- Which datasets should it read first?
  - bronze trades;
  - bronze order book snapshots;
  - existing silver/gold microstructure metrics;
  - existing labels such as triple-barrier or buy labels, if available.
- What target should it compare against?
  - next-N-second return;
  - mid-price movement;
  - spread-adjusted return;
  - existing label columns;
  - execution-aware approximation.
- What metrics should it output?
  - information coefficient;
  - hit rate or sign accuracy;
  - return by signal bucket;
  - signal decay by horizon;
  - autocorrelation;
  - sample count;
  - p-value or other significance estimate;
  - stability by day, market, regime, and spread/volatility bucket.
- What thresholds or decision rules should promote a signal to backtest?
- What failure modes should cause immediate rejection?
- What scanner output is enough for a human or agent to understand why a signal
  passed or failed?

The scanner is not a replacement for Himalaya. It is the fast filter before
Himalaya.

## Research Ledger Design Questions

The dialogue should decide what persistent research memory is needed.

Questions to answer:

- What should be recorded for every hypothesis?
  - id;
  - title;
  - hypothesis family;
  - edge mechanism;
  - feature definition;
  - data used;
  - scan configuration;
  - scan metrics;
  - backtest links, if any;
  - decision;
  - rejection reason or promotion reason;
  - next suggested experiments.
- Where should the ledger live?
  - Kilimanjaro repository;
  - Makalu research dataset;
  - JSON/Markdown files;
  - Parquet/SQLite/DuckDB table.
- What schema is enough for v1?
- How should agents search prior work before proposing new ideas?
- How should the ledger distinguish rejected, needs-more-data, scan-passed,
  backtest-passed, and retired hypotheses?
- How should repeated experiments, parameter variations, and regime-specific
  results be grouped?

The ledger should make the workflow cumulative across sessions.

## Hypothesis Space To Consider

Because bronze data is the most valuable asset, prioritize microstructure
families such as:

- order book imbalance;
- weighted imbalance and microprice;
- top-of-book reset behavior;
- depth pull/refill asymmetry;
- spread widening/narrowing regimes;
- trade aggression bursts;
- buy/sell pressure exhaustion;
- queue depletion before price movement;
- lead-lag between venues or execution sizes;
- volatility/spread regime filters;
- liquidity shock mean reversion;
- adverse selection avoidance.

The dialogue may propose other families, but should justify why existing data
can test them.

## Evaluation Principles

Require a workflow that avoids weak evidence.

The workflow should reject ideas that rely only on:

- one profitable backtest;
- one market regime;
- unbounded parameter search;
- unrealistic fills;
- ignored fees/spread/slippage;
- post-hoc labels;
- unstable microstructure assumptions.

Evaluation should include:

- net PnL after fees/slippage;
- trade count;
- win rate;
- Sharpe or similar risk-adjusted metric;
- max drawdown;
- turnover;
- capacity/liquidity;
- sensitivity to parameter changes;
- train/test/forward split stability;
- regime robustness;
- failure-case diagnostics.

## Deliverables

Write these files in the dialogue folder:

1. `workflow-strategy.md`
   - workflow stages;
   - agent responsibilities;
   - human review points;
   - non-goals.

2. `tool-gap-analysis.md`
   - what existing tools already provide;
   - what is missing;
   - which gaps block v1;
   - which gaps can wait.
   - whether a signal scanner is required before autonomy is realistic.
   - whether a research ledger is required before autonomy is realistic.

3. `hypothesis-taxonomy.md`
   - strategy families worth exploring;
   - required data/features;
   - expected edge mechanisms;
   - examples and rejection criteria.

4. `evaluation-framework.md`
   - signal-scan protocol;
   - backtest protocol;
   - validation splits;
   - metrics;
   - anti-overfitting rules;
   - decision memory.

5. `autonomous-readiness-plan.md`
   - what an agent can execute end-to-end today;
   - where the loop is too slow, expensive, or ambiguous;
   - minimum signal scanner requirements;
   - minimum research ledger requirements;
   - human review points;
   - smallest v1 workflow that can run repeatedly and learn.

## Acceptance Bar

The final docs should let a second dialogue or product owner answer:

- What is the first useful workflow slice?
- Are existing tools enough for that slice?
- What additional tools should be built?
- How will an agent propose a hypothesis?
- How will a hypothesis become a signal scan?
- How will a signal scan become a backtest candidate?
- How will the system decide whether an idea is accepted, rejected, or revised?
- What should be recorded so the next agent session can build on prior work?

Prefer a practical v1 loop over a grand autonomous-research platform.

## Initial Opinion To Challenge

The first version should not generate strategy code automatically.

It should first build:

- a data inventory;
- a hypothesis registry;
- a signal scanner for fast pre-backtest feedback;
- a hypothesis spec format;
- a scan and evaluation protocol;
- a decision-memory system.

Only then should approved hypotheses become code-generation or implementation
tasks.
