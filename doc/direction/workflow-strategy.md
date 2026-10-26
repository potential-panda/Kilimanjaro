# Workflow Strategy — Strategy Discovery Loop (v1)

## Job-to-be-done

Give an AI agent a repeatable, auditable loop to turn a microstructure
intuition into one of three end states — **rejected with a recorded reason**,
**parked pending more data**, or **promoted to a Himalaya backtest candidate**
— without ever trusting a single profitable backtest or a single PnL number
as proof.

The product is the loop and its memory, not any strategy it finds.

## Stages

```
1. Data inventory        -> what exists, what's missing
2. Hypothesis generation  -> named edge mechanism, not "find patterns"
3. Hypothesis spec        -> structured, falsifiable, machine-readable
4. Signal scan            -> cheap statistical test of the feature alone
5. Feasibility review     -> gate: scan strong enough? data sufficient?
6. Ledger update           -> always, regardless of outcome
7. Backtest handoff        -> spec -> Himalaya strategy variant
8. Backtest & evaluation  -> train/test/forward, multi-regime
9. Decision + iteration    -> promote / refine / retire, pick next experiment
```

Stages 1–6 are the **scanner loop** — cheap, agent-autonomous, runs many
times per day. Stages 7–9 are the **backtest loop** — expensive, rate-limited,
human-reviewed at the gate into stage 7.

### 1. Data inventory
Agent or scheduled job queries Makalu's dataset catalog (`doc/catalog.yaml`)
plus live `cli.py query --list` / date-range probes to produce a refreshed
view of: which exchange/symbol/date combinations have bronze Trade and
OrderBook data, which silver/gold features exist, and where gaps are. This is
read-only against existing Makalu infrastructure — no new pipeline required
for v1.

### 2. Hypothesis generation
Agent proposes a hypothesis from the taxonomy (see
`hypothesis-taxonomy.md`). Every hypothesis must name a mechanism ("liquidity
withdrawal ahead of price moves because passive providers detect informed
flow") — mechanism-free hypotheses ("ML will find a pattern") are rejected
before they reach a spec.

### 3. Hypothesis specification
Agent fills a structured spec (data inputs, feature expression, horizon,
mechanism, entry/exit sketch, cost assumptions, falsification test). This is
the unit of work that flows through the rest of the pipeline and the unit
that gets recorded in the ledger. No spec, no scan.

### 4. Signal scan
The scanner (see `evaluation-framework.md`) computes the feature against a
target return/label over the available bronze/silver data and reports IC,
hit rate, decay, sample count, significance, and stability — without placing
any orders or invoking Himalaya.

### 5. Feasibility review
Automated gate first (does the scan clear the documented thresholds; is
the data sufficient), human review only at the **promotion-to-Himalaya**
boundary in v1. Agents can autonomously reject; only humans approve spending
a Himalaya run.

### 6. Ledger update
Every hypothesis that completes a spec gets a ledger row, regardless of
outcome. This is what prevents re-litigating the same idea and is the thing
that makes the loop cumulative across sessions.

### 7. Backtest implementation handoff
Only specs that passed the scan gate and got human sign-off become a
Himalaya strategy variant / parameter grid. The agent drafts the
implementation task (entry/exit rules, parameter ranges, cost model); a human
or a separate engineering workflow (out of scope here) turns it into
`potential-panda-core` strategy code.

### 8. Backtest & evaluation
Himalaya runs train/test/forward splits across multiple periods/regimes.
Results are evaluated against the full metric set in
`evaluation-framework.md`, not PnL alone.

### 9. Decision memory and iteration
Outcome (promote and continue parameterizing, request a different
horizon/instrument, or retire) is written back to the ledger along with the
next suggested experiment, closing the loop.

## Agent responsibilities

- Run the data inventory query and summarize gaps.
- Generate and spec hypotheses against the taxonomy.
- Run the signal scanner and interpret output against documented thresholds.
- Write ledger entries for every hypothesis, including rejections.
- Search the ledger before proposing a hypothesis to avoid duplicate work.
- Draft (not submit) the Himalaya backtest task for specs that pass the scan
  gate.
- Summarize backtest diagnostics against the evaluation framework and
  propose promote/refine/retire.

## Human review points

| Gate | What the human checks | Why it isn't automated in v1 |
|---|---|---|
| Scan -> Backtest promotion | Spec quality, scan thresholds, whether a Himalaya run is worth the cost | Himalaya runs are the most expensive resource in the loop; a bad gate here burns compute and erodes trust fast |
| Backtest -> Strategy library promotion | Full evaluation framework, regime robustness, capacity/liquidity realism | This is the point where false positives become live-trading risk |
| Taxonomy/threshold changes | Any change to scan pass/fail thresholds or to the hypothesis taxonomy itself | Threshold tuning is a meta-decision about how skeptical the whole system is; should not drift silently |

Everything before the first gate (inventory, generation, spec, scan, ledger
write) is agent-autonomous and human-reviewable after the fact, not
before — that's what makes the scanner loop fast enough to run repeatedly.
"Reviewable after the fact" is backed by a concrete audit cadence, not just
availability: without a defined sampling rate, an unsupervised scanner bug or stale-label
issue could poison the ledger indefinitely before anyone notices.

## Non-goals (v1)

- No automatic strategy code generation. A spec that passes scanning becomes
  a backtest *task*, not code, until a human or a downstream workflow
  implements it.
- No live trading or paper trading hookup.
- No new data pipeline, exchange connector, or venue. v1 works entirely off
  data Makalu already has.
- No fully autonomous promotion to backtest or to a live strategy library —
  both remain human gates in v1.
- No general-purpose ML feature search / auto-ML. Hypotheses come from the
  named taxonomy with an explicit mechanism, not from undirected search.
- No replacement for Himalaya. The scanner is a pre-filter, not a backtest
  engine.
