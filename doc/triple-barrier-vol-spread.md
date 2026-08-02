# Triple-barrier params vs volatility & spread — how to set them, not guess them

**Question (2026-08-03).** We run the triple-barrier exit with *fixed bps* params
(e.g. take-profit/stop ±10 bps, time cap 300 s). Those absolute numbers are
arbitrary. Are they related to volatility and spread, and can we *derive* proper
values from the data instead of grid-searching bps?

**Answer: yes — barrier width, timeout, volatility and spread are one coupled
system.** The economically correct barrier is expressed in **units of volatility,
floored by spread**, with the timeout tied to the horizon over which that
volatility is measured. This doc records the relationships and the method.

See also: `return_calc/triple_barrier.py` (the current fixed-bps implementation),
`doc/return-distribution-verification.md` (the edge is in big moves), `doc/calibration.md`.

---

## 1. The three relationships

### barrier ↔ volatility
A ±b bps barrier means completely different things in different regimes. If the
realized move over the holding horizon is σ ≈ 5 bps, then ±10 is a rare ~2σ event;
if σ ≈ 30 bps, ±10 is hit by pure noise almost immediately. So a **fixed bps
barrier is implicitly a different strategy in every volatility regime** — its
"difficulty" drifts with the market.

Fix: set the barrier as a **multiple of a rolling volatility estimate**,
`b = k · σ`. Then "±2σ" is the same difficulty everywhere, and the *edge* (drift),
not the regime, decides whether +barrier is hit more than −barrier. This is the
standard López de Prado construction (horizontal barriers as vol multiples).

### barrier ↔ timeout ↔ volatility (they are not independent)
For a diffusion, the probability of touching ±b before time T is governed by the
single dimensionless ratio

    b / (σ_1s · √T)          (σ_1s = per-second volatility; σ over horizon T is σ_1s·√T)

So **barrier width, timeout and volatility form a triangle** — there are really
only **two free knobs**, not three:

1. the barrier in vol units, `k = b / σ_T`   (how many std-devs the target is)
2. the horizon `T` (the time cap)

Grid-searching *barrier bps* and *timeout* as if independent searches a redundant
space and invites overfitting. Parameterise by `(k, T)` instead.

### spread = the hard floor
The take-profit must clear the round-trip cost, or a "winner" still loses money:

    b_takeprofit  >  round_trip_cost = spread + fees + latency_slippage

(Under our cost policy the bid/ask spread is already inside the fill and we add a
flat 1 bp latency slippage — see `framework/costs.py` / execution-cost policy in
CLAUDE.md; for a 0-fee venue the floor is ≈ spread + 1 bp.) This is **not** something
to search — it is a constraint set analytically: the barrier's useful range *starts*
above the round-trip cost. Concretely: COINCHECK BTC_SPOT spread ≈ 1.8 bps → ±10
clears comfortably; GMO ETH_JPY ≈ 6.3 bps → ±10 barely clears, which is exactly why
the same fixed barrier behaves so differently across venues.

---

## 2. The method — derive (barrier, timeout) from (σ, spread)

1. **Pick a horizon T** (the vertical barrier / time cap). This is the one genuine
   choice — how long you are willing to wait for the move. Everything else follows.

2. **Estimate σ_T = expected favourable move over T**, at or before entry, causally:
   - rolling realized volatility of mid over a trailing window, scaled to T
     (`σ_T = σ_1s · √T`), **or**
   - directly, the trailing distribution of `|mid(t+T) − mid(t)|` in bps.
   - **Condition on regime.** We already know the move size roughly doubles in the
     US session (JST 22–02) vs the quiet afternoon (`project_signal_combination`
     memory / spread-by-hour study). A per-hour or rolling σ captures this
     automatically — a static σ does not.

3. **Set the barrier in vol units**, floored by cost:

       b = max( k · σ_T ,  round_trip_cost + margin )

   with `k` a small multiple (start k ≈ 1–2). The `max(...)` guarantees the target
   always clears the spread even in a low-vol window; in a high-vol window the
   `k·σ_T` term dominates and the barrier widens to match the regime.

4. **Timeout = T** (consistent with the σ_T you measured). Do not tune T and the
   vol window independently of each other.

Net effect: **two economically-meaningful knobs (k, T)** replace a 2-D grid of
arbitrary (barrier_bps, timeout_s), the barrier auto-adapts to regime and venue,
and it is guaranteed cost-clearing by construction.

---

## 3. Estimating σ and spread from the data

- **σ (volatility):** rolling realized vol of 1s mid returns (causal, e.g. the
  `zscore`/rolling ops in `op/series.py`), scaled by √T; or the trailing empirical
  distribution of the T-second signed/absolute mid move. Bucketed by JST hour to
  capture the session effect.
- **spread:** median (and an upper quantile, e.g. q75) of `spread_bps` from the
  venue's MarketPrice, ideally in the same JST-hour buckets you trade. We have this
  per venue (e.g. COINCHECK BTC_SPOT median 1.80 bps, 1.90 in US hours; GMO BTC_JPY
  2.33; GMO ETH_JPY 6.3). One DuckDB query over MarketPrice gives both.

---

## 4. Calibrating (k, T) without overfitting

The barrier/timeout are **exit** params and it is easy to fool yourself by picking
whatever tops the holdout. Guard against it:

1. **Cheapest, highest-information diagnostic first — exit-mode split.** Using the
   `holding_seconds` already logged per trade: what fraction of trades exit via the
   **barrier** vs the **time cap**? If ~80% time out, the barrier is rarely reached
   → you are effectively running a fixed-horizon exit and the barrier is inert (b is
   too wide for T, or σ too low). If ~80% hit the barrier, it is barrier-dominated.
   This one number tells you whether the current params are even in the right regime
   *before* any tuning.
2. **Robustness, not a peak.** A real edge is positive across a *range* of `k` and
   `T` and degrades gracefully; an edge that lives only at one magic (k, T) is an
   overfit. Report the surface, don't cherry-pick its max.
3. **Fix on train, confirm on holdout** — or, better, use the (σ, spread)-derived
   rule with `k` chosen on train (or a-priori), so nothing is tuned to the test
   window. The vol-scaled rule has fewer free parameters than a bps grid, which is
   itself overfit protection.

---

## 5. Implementation note

`TripleBarrierReturn` currently takes a **fixed** `barrier_bps`. Realising the
above needs a **volatility-adaptive** variant whose per-entry barrier is
`b_i = max(k · σ_T(t_i), cost_floor)` computed causally at each entry `t_i` (the
scan-runner already builds combos over barrier lists — this becomes a `k` list plus
a σ source instead of a bps list). The exit-mode diagnostic (§4.1) can be built
from the existing per-trade output with no new return_calc.

---

## 6. Priority / perspective

Keep the reshaping in perspective. Widening the barrier ±10→±20 bps changed total
net by ~+34% (COINCHECK holdout) — exit params **reshape** an existing edge, they
do not create it (for a driftless path E[return]=0 by optional stopping; the barrier
only harvests the signal's drift — `triple_barrier.py`). The far larger unknown is
the **uncalibrated → Himalaya** gap. So: do the *small principled study* (exit-mode
diagnostic → vol-scaled barrier → robustness surface on train), not an open-ended
grid search, and treat Himalaya as the real gate.
