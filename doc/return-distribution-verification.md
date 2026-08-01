# Return-distribution verification — is the edge broad or event-driven?

**Question (2026-08-01).** When a signal's IC is low, are the weak/negative
returns a *pervasive* property, or do they come from a handful of market events
(e.g. the 2025-10-11 and 2026-02-08 crypto moves)? Equivalently: how are the
signal's directional returns distributed over calendar time?

This is a **signal-quality** check, deliberately separate from tradeability:
it uses **mid-to-mid** returns with **no cost and no spread**, so it isolates
"does the signal point the right way, and consistently?" from "does the move
beat the spread?" (the latter is the step-2 / taker question).

## What we found (BTC composite, GMO/BTC_JPY, mid-to-mid 300s, non-overlapping)

The directional edge is **broad, consistent, and calibrated** — not event-driven.

| z-threshold | trades | mean bps/trade | positive days | biggest single day | top-10 days | positive months |
|---|---|---|---|---|---|---|
| 0.5 | 99,335 | +0.64 | 279/352 (79%) | 1.7% of total | 14% | 12/12 |
| 1.0 | 96,283 | +1.13 | 328/351 (93%) | 1.4% | 10% | 12/12 |
| 1.5 | 87,895 | +1.81 | 347/351 (99%) | 1.2% | 9% | 12/12 |
| 2.0 | 67,988 | +2.23 | 347/351 (99%) | 1.0% | 8% | 12/12 |

Reading:

1. **No single day or event dominates.** The biggest single day is ≤1.7% of the
   yearly total at every threshold; the top-10 days are 8–14%. Spread across
   ~350 days, that is healthy, mild concentration — not a fat tail.
2. **The named event days were the signal's *best* days**, not its problem:
   2025-10-11 mean **+6.97 bps**, 2026-02-07 **+6.74**, 2026-02-09 **+5.18**
   (vs a +1.8 baseline). Big moves are the *most* directionally predictable.
3. **Positive-day share is high even nearly unfiltered** (79% at z>0.5) and
   **12/12 months are positive at every threshold.**
4. **The edge scales with conviction** (mean +0.64 → +2.23 as z 0.5 → 2.0) —
   textbook `E[return | z] ∝ z`. The signal is calibrated, a strong sign of
   real structure rather than noise.

## The conclusion this supports

The signal is **good**. It loses as a *taker* only because the per-trade
mid-to-mid move (+0.6 to +2.2 bps by conviction) is smaller than the round-trip
spread+cost (GMO BTC_JPY ≈ 2.33 + 1.0 ≈ 3.3 bps): +1.8 − 3.3 ≈ −1.5, which is
exactly the observed step-2 taker loss. **It is a pure execution/spread problem,
not signal quality and not fat-tail events.** Low IC at low z means
small-but-positive returns, not negative ones.

Corollary: the levers that make it profitable all *reduce the spread paid*
relative to the move captured — tighter market (COINCHECK BTC_SPOT ≈ 1.8 bps),
US-session hours (bigger moves), high z-threshold (bigger moves), and a
prospective instantaneous-spread gate (trade only when the quote is momentarily
tight).

## How to reproduce / re-check

`cli.py learn --op return_dist` runs this on **any** spec (per expression), and
`blend --step2` now writes the blended **composite** as a spec you can feed it:

```bash
# 1) any single-expression spec
python src/cli.py learn --op return_dist \
    --spec cycle/12_specs/hyp-20260731-006_microprice_BTC_JPY.yaml \
    --thresholds 0.5,1.0,1.5,2.0 --horizon 300 \
    --start 2025-07-01 --end 2026-06-30

# 2) the IC-weighted composite of a whole family — blend writes composite_spec.yaml
python src/cli.py blend --horizon 300 --step2 --start 2025-07-01 --end 2026-06-30 \
    --out cycle/13_results/blend/BTC_JPY $(ls cycle/12_specs/*BTC_JPY.yaml)
python src/cli.py learn --op return_dist \
    --spec cycle/13_results/blend/BTC_JPY/composite_spec.yaml \
    --thresholds 0.5,1.0,1.5,2.0 --start 2025-07-01 --end 2026-06-30
```

Output columns (`cycle/13_results/learn/return_dist/.../return_dist.csv`):
`n_trades, trades_per_day, mean_bps, pos_day_share, max_day_share_pct,
top10_day_share_pct, pos_month_share`. A healthy signal shows a high
`pos_day_share` / `pos_month_share` and a small `max_day_share_pct` /
`top10_day_share_pct` (no day or few days carrying the result). Use a full-year
(or multi-month) window — the concentration metrics are meaningless on a few
weeks (top-10 days can exceed the total when the total is small).

Implementation: `driver/learn_metrics.py::return_distribution` /
`learn_return_dist`; mid-to-mid, non-overlapping (horizon-spaced) sampling, JST
day/month buckets. Mirrors the honest-inference sampling used everywhere else
(see `doc/overlapping-returns.md`).
