# The research pipeline — stages, metrics, and module map

This is the authoritative design for the discovery loop's stages. It supersedes the
older three-stage `13_results → 14_ledger → 15_handoffs` layout. The repo is young and
under heavy construction: **there is no backward-compatibility obligation** — the ideal
structure wins, old regenerable artifacts are dropped (git history keeps them).

## The funnel

The loop is a **cost-shaped funnel**: cheap wide screening first, expensive narrow
verdict last. Each stage answers one question and only passes survivors down.

```
11 hypotheses   what idea?                         (generate)
12 specs        runnable config                    (queue)
13 signal_corr  does the single signal predict?    CORRELATION (IC + cross-factor corr)   cheap, wide
14 blend_corr   does a weighted combo predict?     CORRELATION (blend IC)                 medium
15 return_eval  does trading the top blends pay?   RETURN / trades (non-overlapping)      expensive, narrow
16 calibration  can we trust the fast scan?        scan ↔ Himalaya fidelity               cheap if mechanism certified
17 ledger       remember                           (append-only events.jsonl)
18 handoffs     promote                            (human-approved packages)
```

The tens digit is the phase: `0x` = foundation inputs (`01_schemas`, `03_inventory`),
`1x` = the loop, in order.

## Why this shape

The full-year calibrated re-scan (2025-08..2026-06) showed **every individual factor is
net-negative; only the *blend* reaches breakeven**. So the blend, not the factor, is the
unit of edge — and the expensive tiers must operate on blends, not on every factor × param.

- **13 → 14**: screen each factor by correlation, then *construct* weighted blends from
  the promising ones. A strong standalone factor enters stage 14 as a **blend of one**, so
  it competes head-to-head with multi-factor blends and flows down the same path — no
  special-casing.
- **14 → 15**: episode re-test (the expensive non-overlapping return verdict) runs on the
  **top blends only**.
- **15 → 16**: calibrate the survivor. Fidelity lives in the **mechanism** (entry style +
  return calc + execution), not the formula (see `doc/calibration.md`), so this is a cheap
  "is this survivor's mechanism already certified?" gate — a full scan↔Himalaya run only
  when the mechanism is new.

## Screen by correlation, evaluate by return

The `_corr` vs `_eval` naming is deliberate. Stages **13 and 14 screen by CORRELATION/IC**
(cheap, robust, computable on all data); stage **15 evaluates by RETURN** on non-overlapping
episodes (expensive, the real verdict). Reading top-to-bottom you see **corr → corr →
return** = screen → screen → verdict.

Two rules that follow from this:

1. **`signal_corr` emits the correlation *structure*, not just per-factor scores.** Building
   good blends needs (a) each factor's IC (signal ↔ forward return) *and* (b) the pairwise
   **factor-correlation matrix** — the composite's edge came from orthogonality. Both are
   first-class outputs of stage 13 that stage 14 consumes.
2. **Stage 13 ranks; it does not hard-filter.** The screening metric (IC) and the trade
   metric (episode return) genuinely diverge (the two-tier scan once caught a sign flip). A
   factor with mediocre IC but good conditional return must still be *eligible* for a blend,
   so 13 ranks and 14 owns the include/exclude + weight decision.

## The hard stage: 14 blend_corr

Everything else is plumbing; blend construction is where the research difficulty lives.
Baseline = IC-weighted + decorrelation (the existing `cli.py blend`). Open design space:
subset search, weight optimization, an LGBM capacity layer on top. This is the stage to
invest in.

## Module map (Python module name == cycle step, where practical)

| stage | cycle dir | driver / package module | command |
|-------|-----------|-------------------------|---------|
| 11 hypotheses | `cycle/11_hypotheses/` | `driver/registry.py` | `generate` |
| 12 specs | `cycle/12_specs/` | `driver/generate.py` | `generate` |
| 13 signal_corr | `cycle/13_signal_corr/` | `driver/signal_corr.py` | (per-factor scan) |
| 14 blend_corr | `cycle/14_blend_corr/` | `driver/blend_corr.py` | `blend` |
| 15 return_eval | `cycle/15_return_eval/` | `driver/return_eval.py` | `grid`, `loop` |
| 16 calibration | `cycle/16_calibration/` | `calibration/` (package) | `calibrate` |
| 17 ledger | `cycle/17_ledger/` | `ledger/` (package) | `ledger` |
| 18 handoffs | `cycle/18_handoffs/` | — (data) | — |

Shared infrastructure that is *not* a stage keeps its own name: `driver/scan_runner.py`
(the grid engine every scan runs through), `framework/` (run_grid_split, gates,
performance, verify), `driver/loop.py` / `driver/cadence.py` (orchestration).

## Implementation status

- **Structural** (folders, numbering, module names, path refs, docs): implemented.
- **Stage logic**:
  - `signal_corr` (13): **implemented** — `cli.py signal-corr` / `driver/signal_corr.py`.
    Per-factor IC (Pearson + rank, across horizons) vs the target's forward return, plus the
    cross-factor correlation matrix; `factors_from_blend_expr` splits any composite into its
    terms. Writes `cycle/13_signal_corr/<spec>/{ic.csv,corr_matrix.csv}`.
  - `blend_corr` (14): baseline `cli.py blend` present; ranking-by-blend-IC + construction
    search (subset/weight optimization, LGBM capacity layer) is the build-out.
  - `return_eval` (15): implemented — episode re-test on the shared scan engine.
  - `calibration` (16): implemented (`cli.py calibrate`, factor + trade layers).
