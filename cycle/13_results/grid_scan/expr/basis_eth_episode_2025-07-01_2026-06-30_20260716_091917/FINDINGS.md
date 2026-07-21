# basis-follow, episode (spread-aware) — REJECTED as taker

Scan: `basis_eth_episode` (hyp-20260714-002), EpisodeReturn, 2025-07-01..2026-06-30,
train/holdout split. Ledger: evt-20260716-001.

**Result: 0/24 rank-stable, all 24 = noise, every combo strongly negative.**
Best (least-bad) holdout net = **-5.74 bps** (basis_z_15m, follow, thr=2.5);
worst = -9.31 bps (fade, thr=2.5). t-stats -300 to -800 — reliably losing, not noisy.

## Why this is the definitive answer

EpisodeReturn pays the **real bid/ask spread** (enter at ask, exit at bid), so the
spread cost is already inside the *gross* number — and even gross is negative
(-4.65 to -8.36 bps). Compare batch-1, which measured mid-to-mid and saw
**+2.34 bps gross**:

    mid edge (+2.34)  -  GMO ETH_JPY spread (~7 bps)  ≈  -4.7 bps   ✓ matches episode gross

So EpisodeReturn confirms batch-1 exactly: the cross-market drift is **real but
smaller than the spread**. As a spread-crossing taker you lose ~5-6 bps/episode.
This is the spread-aware test batch-1 said was needed; it came back a clean no.

## Detail

- **Follow (invert=False)** loses the spread, keeps the +2.3 drift → ~-4.7 gross.
- **Fade (invert=True)** loses the spread AND the drift → ~-7.5 to -8.4 gross.
- **max_hold (300 / 900 / none) barely matters** — episodes are short, the cap
  rarely binds. Basis dislocations clear fast.
- **Higher threshold (2.5 vs 1.5) doesn't help** — bigger dislocations don't
  drift proportionally more, so they don't out-earn the spread either.

## The only remaining life for basis-follow

Maker execution: **post** at bid/ask (BITBANK, or GMO post-only) to *earn* the
spread instead of paying it — flipping the sign of the ~7 bps spread term. That
needs a passive-fill execution model (EpisodeReturn is aggressive-only), which is
a separate build. Absent that, basis-follow as a taker is closed.
