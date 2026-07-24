# Trade-aggression lead-lag (BINANCE ETH flow -> GMO ETH_JPY) — real but untradeable

Screen: `aggression_eth`, fixed_horizon mid-to-mid (pre-spread), 2025-07-01..2026-06-29,
train/holdout. 72 combos. --no-ledger (exploratory). First real use of the
corrected TradeAggression features.

**Result: 0 rank-stable. 8 regime_dependent, 64 noise. There is a weak, real
signal that is ~10x too small to trade and decays out of sample.**

## The signal is real (weak) and directionally coherent

- **Follow (invert=False) is positive gross; fade is negative** — consistent sign
  across every expression. BINANCE aggressive buying does weakly lead GMO up.
- Strongest: **`imb5_z`** (5s aggressive-volume imbalance, z-scored): train gross
  **+1.1 bps**, holdout gross **+0.8 bps**. Shorter window > longer (`imb30_z`
  weaker) — order-flow information decays fast, as expected.

## Why it's a no

1. **Too small.** ~0.8 bps gross is below even the 1 bps flat cost (net t-stats
   go negative), and nowhere near the ~7 bps GMO spread. Dead as a taker.
2. **Decays out of sample.** train gross +1.1 -> holdout +0.8, and monthly
   consistency collapses (`pos_month_share` train ~0.6 -> holdout 0.0-0.4). The 8
   imb5_z follow combos are regime_dependent, not stable.
3. **Higher threshold doesn't help** (thr=2.5 holdout gross ~0.44 < thr=1.5 ~0.81)
   — extreme imbalance spikes don't predict better, so there's no "big dislocation"
   subset to isolate.

## Contrast with basis

- basis: real drift, *stable* (every month positive), but < spread -> dead taker,
  alive only via maker execution.
- aggression: real lead-lag but *tiny and decaying* -> weaker candidate; maker
  execution wouldn't rescue a 0.8 bps edge that doesn't even hold up OOS.

## Diagnostic worth running (cheap)

Re-run with **target = BINANCE ETH_USDT** (predict the source venue's own mid).
If the same-market gross is much larger (e.g. 3-5 bps), the edge is real but lost
crossing to GMO — informs whether aggression is worth using as a *filter/confirm*
on a larger signal (flip, OBI) rather than standalone. If same-market is also
~1 bps, aggression simply carries little short-horizon price information here.
