# 15_return_eval — return / trade evaluation

**Stage 15 of the loop** (see `doc/research-pipeline.md`). Expensive, narrow.

The real verdict: re-measure the **top blends** (from 14) on **non-overlapping episode
returns** — the metric that overlapping per-second screening can't fake. Reports return,
trades, and path-aware risk (Sharpe/Calmar/Martin).

Grid-scan outputs live under `grid_scan/{flip,expr,horizon}/…` (`output*.csv`, `params.json`,
review packets); large regenerable diagnostics are git-ignored — the ledger is the durable
record. Module: `driver/return_eval.py` (commands: `grid`, `loop`).
