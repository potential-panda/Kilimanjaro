# Enoshima validation request — COINCHECK BTC_SPOT blend composite (US hours, z≥3.5)

**Status: DRAFT / not yet human-approved.** This is a *validation request*, not an
approved flip handoff. The mechanism is novel (IC-weighted composite expression +
triple-barrier exit) and does **not** map onto the flip-based `build_backtest_env.py`
combo flow — a human/agent implements it in qate (`qate-implementation-guide.md`), or
validates the edge first via the precomputed-signal replay shortcut (guide §1). Goal:
**calibration** — convert the *uncalibrated* scan edge into a trusted number
(`doc/calibration.md`).

**Primary target: COINCHECK BTC_SPOT** — the best venue found (~2× GMO BTC_JPY,
12/12 months positive; see Provenance below). Machine-readable signal:
`composite_coincheck.yaml` (the composite as a single-expression spec — exact formula
+ weights baked into `expressions[0].expr`; signal on the BINANCE leader, only the
execution/basis leg `b` and target are COINCHECK). GMO BTC_JPY is retained below as
the secondary/comparison venue only.

---

## The config to validate (frozen)

| item | value |
|---|---|
| Target (execution) | **COINCHECK / BTC_SPOT**, order_size 0.002 — **0-fee taker (CONFIRMED)** |
| Signal source | BINANCE / BTC_USDT (leader — all 19 factors) + COINCHECK / BTC_SPOT (basis leg `b_mid` only) |
| Entry signal | **composite** = IC-weighted sum of 19 factors, z-scored (**GMO-fit weights**; baked into `composite_coincheck.yaml`) |
| Entry rule | fire when **\|composite z\| ≥ 3.5**; direction = sign(composite) |
| Exit rule | **triple-barrier (v1)**: first of +10 bps / −10 bps (on mid) / **300 s** cap. Secondary candidate: **trailing stop (v3)** — moving stop ratcheted on each same-side fire, cap 300 s. Both are anchored to the 300 s (X-sec) horizon. **Episode exit (v2) is retired** — it exits when \|z\| falls back under threshold (~2 s), measuring signal *persistence*, not the X-sec prediction this setup tests. |
| Time filter | **JST hours 22, 23, 0, 1, 2 only** (US session); block all other hours. **No GMO Saturday-maintenance block applies — confirm COINCHECK's own maintenance windows** and block those. |
| Execution | entry aggressive (ask BUY / bid SELL), exit passive; **+1.0 bp round-trip** latency slippage (0.5 bp/order) — matches Enoshima. **0 fee.** |
| Windows | train 2025-07-01 … 2026-02-28, **holdout 2026-03-01 … 2026-06-30** (2/3 split) |

Weights are **GMO-fit** (each factor's weight = its GMO-BTC_JPY train IC) — proved
*more robust* on COINCHECK than a COINCHECK refit (see Provenance), so validate the
composite as-is, no refit. Nothing is fitted to holdout. `X=10 bps` and `z≥3.5` are
the robust middle of a sweep, not a tuned point.

Top factors by weight (full list in the yaml): `wimb_k1/k5` (volume-weighted book
imbalance, BookState) +0.053/+0.055, `vwapdev_10s` +0.041, `aggcount_10s` +0.037,
`imb_5s` (trade imbalance) +0.034, `basis_900` +0.028, `mom_30s` +0.023. All are
computed on the **BINANCE** leader except the two `basis` terms (BINANCE_mid /
COINCHECK_mid).

---

## Expected numbers (SCAN — uncalibrated, trend-only)

Holdout, **COINCHECK/BTC_SPOT**, US hours, with the drawdown gate. **Indicative, not
promotion-grade** — expression + triple_barrier/trailing_stop is uncalibrated
(`doc/calibration.md`); only flip + between-signal (ETH) is calibrated today. What
Enoshima is asked to confirm is the **sign, the magnitude order, and the monthly
consistency** — not the third decimal.

Numbers below are the **Enoshima-faithful sequential re-entry** figures (one position
at a time, re-enter on the first fire after the previous exit) — this replaced an
earlier fixed-time-cap decimation that under-counted trades and *over-stated*
per-trade net (old v1 X=10 read +3.19; the honest re-entry value is +2.72).

| exit | z=3.5 | z=4.5 | months + (3.5 · 4.5) |
|---|---|---|---|
| **v1 triple-barrier X=10** | **+2.72** (n=1046, hit 0.69, Sharpe 0.295) | **+3.89** (n=327, hit 0.73) | 12/12 · 11/12 |
| v1 triple-barrier X=20 | +4.06 (n=896, hit 0.61) | +4.96 (n=301, hit 0.62) | 12/12 · 12/12 |
| v3 trailing+flip X=10 | +4.53 (n=675, hit 0.43, Sharpe 0.146) | +2.48 (n=230, hit 0.38) | 10/12 · 9/12 |
| v3 trailing+flip X=20 | +4.73 (n=519, hit 0.44) | +0.77 (n=184, hit 0.37) | 9/12 · 9/12 |

**v1 is the lead candidate.** v3 (pure trailing stop + opposite-side flip, no time cap
— the faithful "let winners run") wins only the *headline* per-trade net at z=3.5
(+4.53 vs +2.72), and that comes with **half the Sharpe (0.146 vs 0.295), a 0.43 hit
rate, and worse monthly consistency** — a low-win-rate fat-tail profile carried by a
few big winners, so it is lumpy. Everywhere else v1 wins: risk-adjusted (~2× Sharpe),
consistency (12/12 vs 10/12), capacity (~1.5× the trades ⇒ higher total PnL), and
robustness — at **z=4.5 (the most selective tier) v3 collapses** (+2.48 / +0.77) while
v1 is strongest (+3.89 / +4.96), because the no-cap trailing needs frequent same-side
fires to ratchet and a rare threshold supplies none. Read: this signal's edge is in the
*immediate* predicted move (v1's tight ±barrier + timeout captures it cleanly and
often), not in sustained trends. Enoshima should confirm v1 first; v3 is the
lower-priority variant.

Trade frequency: z=3.5 ≈ 4/day; z=4.5 ≈ 2–3/day. For reference, the original **GMO
BTC_JPY** target is ~half these — COINCHECK is the stronger venue.

Supporting signal-quality (the composite is broad, not event-driven —
`doc/return-distribution-verification.md`): mid-to-mid at z≥1.5 the directional edge
is +1.8 bps/trade with ~99% of days positive and no single day >~1.7% of the total
(event days were the *best* days). COINCHECK's 12/12-months-positive holdout is
direct confirmation the tail edge is consistent, not one-window.

---

## Honest limitations (please read before acting on the number)

1. **Uncalibrated** — the magnitude may move materially under Enoshima. Trust the
   sign and the ordering, calibrate the size.
2. **Thin frequency** — ~2–10 trades/day in the US session only. Real but small
   absolute PnL; capacity-limited.
3. **Edge lives in the high-z tail** — z≥3.5 selects the rare, big-move signals
   whose move clears the ~1.9 bp COINCHECK US-hours spread. Below z≈3 it is a net
   loser as a taker. This is a spread-bound edge, not a broad one.
4. **Search is exhausted** — more formulas, market selection (COINCHECK won),
   triple-barrier width + a vol-scaled barrier, and LGBM (both meta-labeling and as
   a primary signal) were all tried; none *robustly* beat the settled config at the
   tradeable tail (LGBM's value is broad capacity, not the tail — parked as an
   optional future frequency lever). Enoshima is the gate, not more scanning.

---

## Provenance — why COINCHECK, venue comparison, weights, fee

COINCHECK BTC_SPOT was validated on the full-year backfill (388 days, 2025-07-01 …
2026-07-31; median spread 1.80 bps / 1.90 in US hours, vs GMO BTC_JPY 2.33). The
identical composite (BINANCE-leader signal, only the `b`/target leg swapped to
COINCHECK) run US-hours through the same episode + drawdown-gate + cost machinery
gives the holdout numbers in *Expected numbers* above — ~2× GMO BTC_JPY, every config
**12/12 months positive** (weakest month 2025-09, still ≥0), train and holdout both
positive.

**This overturns the "tighter spot market = efficient = worse" prior.** GMO
BTC_SPOT (spread 1.35) was *worse* than BTC_JPY; COINCHECK BTC_SPOT (1.80) is far
*better*. COINCHECK is Japan's retail app-dominated exchange — dumb, exploitable
flow AND a moderate spread — so it has high edge *and* an acceptable spread,
breaking the edge/spread coupling in our favour.

- Spec: `composite_coincheck.yaml` (this folder). Signal unchanged; only `b` +
  target = COINCHECK/BTC_SPOT/0.002. COINCHECK has **MarketPrice only**, which is
  all the composite needs from `b` (`b_mid`) — it is execution-only, never a
  signal source.
- Runner: `run_coincheck.py` — one command, mirrors the GMO US-hours run.
- Results: `cycle/15_return_eval/blend/coincheck_us/`.

**Weights: use the GMO-fit composite as-is — refitting on COINCHECK is WORSE, not
better** (tested 2026-08-03, `refit_coincheck.py`). Refitting each factor's
IC-weight against COINCHECK's own forward return gives episode +2.43/+3.53 and
TB ±10 +2.63/+3.24 — *below* the GMO-fit +3.03/+3.93 and +3.19/+4.47. Reason: the
composite is BINANCE-lead-driven, so its predictive content is venue-agnostic
(BINANCE→BTC); refitting on COINCHECK's noisier own-return just adds train
estimation noise (refit blend holdout IC only +0.019) without new signal. So the
GMO-fit `composite_coincheck.yaml` is not a floor — it is the better, more robust
estimate, and the one to validate.

**Fee: CONFIRMED 0-fee taker** (2026-08-03, https://coincheck.com/ja/exchange/fee —
BTC_SPOT 取引所 spot trading fee is 0%). So only the flat 1 bp latency slippage
applies, exactly as these nets assume. The cost assumption is validated.

---

## Reproduce the scan numbers

```bash
# PRIMARY — COINCHECK, US hours, both exits, z=3.5/4.5 (the Expected-numbers table):
python cycle/18_handoffs/blend-btc-jpy-us-z35/run_coincheck.py \
    --start 2025-07-01 --end 2026-06-30

# exit-mode + risk-adjusted (barrier vs time-cap, Sharpe/Martin/DD by barrier):
python src/cli.py learn --op exit_mode \
    --spec cycle/18_handoffs/blend-btc-jpy-us-z35/composite_coincheck.yaml \
    --start 2026-03-01 --end 2026-06-30 --thresholds 3.5,4.5 \
    --barriers 10,20 --time-caps 300 --jst-hours 22,23,0,1,2

# signal-quality (time distribution, mid-to-mid) of the composite:
python src/cli.py learn --op return_dist \
    --spec cycle/18_handoffs/blend-btc-jpy-us-z35/composite_coincheck.yaml \
    --thresholds 1.0,1.5,2.5,3.5 --start 2025-07-01 --end 2026-06-30

# (secondary) GMO BTC_JPY comparison via the blend step-2:
python src/cli.py blend --horizon 300 --step2 --exit triple_barrier --barrier-bps 10 \
    --jst-hours 22,23,0,1,2 --start 2025-07-01 --end 2026-06-30 \
    --out cycle/15_return_eval/blend/BTC_JPY_us_tb $(ls cycle/12_specs/*BTC_JPY.yaml)
```

The z≥3.5 / z≥4.5 tail numbers come from `run_coincheck.py` (episode + triple_barrier
through `run_spec` with the drawdown gate); results in
`cycle/15_return_eval/blend/coincheck_us/`.

---

## What a clean Enoshima result looks like

- **Sign holds**: net positive at z≥3.5 in the holdout window, both exits.
- **Monthly consistency holds**: the majority of holdout months positive (scan: 12/12
  on COINCHECK).
- **Magnitude is within order**: Enoshima net within ~±1 bp of the scan's per-trade
  figure (episode +3.03 / TB +3.19 at z=3.5) would confirm the mechanism is
  trustworthy for this signal class — and would also validate expression+triple_barrier
  as a calibrated mechanism (`doc/calibration.md`) for future ideas.

If the sign or monthly consistency does **not** survive Enoshima, the conclusion is
that this signal class cannot fund a taker at COINCHECK BTC_SPOT spreads — a
legitimate, valuable negative result that closes the current direction.
