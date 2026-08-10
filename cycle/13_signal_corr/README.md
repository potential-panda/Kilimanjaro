# 13_signal_corr — per-factor correlation screen

**Stage 13 of the loop** (see `doc/research-pipeline.md`). Cheap, wide.

Screens each single factor/formula by **correlation**, producing two durable artifacts:
- **IC** — each factor's information coefficient (correlation of the signal value with the
  forward return over a horizon).
- **correlation matrix** — the pairwise correlation across factors, so stage 14 can build
  *decorrelated* blends.

This stage **ranks; it does not hard-filter** — a mediocre-IC factor may still earn a place
in a blend (14) or as a blend-of-one. Module: `driver/signal_corr.py`.

Subdirs: `learn/` (signal-property diagnostics), `lead_lag/` (lead-lag correlation).
