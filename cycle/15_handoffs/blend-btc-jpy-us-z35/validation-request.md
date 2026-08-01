# Himalaya validation request — BTC_JPY blend composite (US hours, z≥3.5)

**Status: DRAFT / not yet human-approved.** This is a *validation request*, not an
approved flip handoff. The mechanism is novel (IC-weighted composite expression +
triple-barrier exit) and does **not** map onto the existing flip-based
`build_backtest_env.py` combo flow — a human implementer must decide how to render
it in Himalaya/qate. The goal of the run is **calibration**: convert an
*uncalibrated* scan edge into a trusted number, per `doc/calibration.md`.

Machine-readable signal: `composite.yaml` (the composite as a single-expression
scan spec — the exact formula + weights are baked into `expressions[0].expr`).
Implementing it in qate: see `qate-implementation-guide.md` in this folder.

---

## The config to validate

| item | value |
|---|---|
| Target (execution) | **GMO / BTC_JPY**, order_size 0.01 (0-fee taker) |
| Signal source | BINANCE / BTC_USDT (leader) + GMO / BTC_JPY (basis leg) |
| Entry signal | **composite** = IC-weighted sum of 19 factors, z-scored (see yaml) |
| Entry rule | fire when **\|composite z\| ≥ 3.5**; direction = sign(composite) |
| Exit rule | **triple-barrier**: first of +10 bps / −10 bps (on mid) / **300 s** cap. (Also validate the plain **episode** exit — hold while \|z\|>threshold, cap 300 s.) |
| Time filter | **JST hours 22, 23, 0, 1, 2 only** (US session); block all other hours; block GMO Saturday maintenance 09–11 JST |
| Execution | entry aggressive (ask BUY / bid SELL), exit passive; **+1.0 bp round-trip** latency slippage (0.5 bp/order) — matches Himalaya |
| Windows | train 2025-07-01 … 2026-02-28, **holdout 2026-03-01 … 2026-06-30** (2/3 split) |

The composite weights were fit on **train** only (each factor's weight = its train
IC); nothing is fitted to holdout. The `X=10 bps` barrier and `z≥3.5` threshold are
the robust middle of a sweep (see below), not tuned to a single point.

Top factors by weight (full list in the yaml): `wimb_k1/k5` (volume-weighted book
imbalance, BookState) +0.053/+0.055, `vwapdev_10s` +0.041, `aggcount_10s` +0.037,
`imb_5s` (trade imbalance) +0.034, `basis_900` +0.028, `mom_30s` +0.023.

---

## Expected numbers (SCAN — uncalibrated, trend-only)

Holdout, GMO/BTC_JPY, US hours. **These are indicative, not promotion-grade** —
expression + triple_barrier/episode is uncalibrated (`doc/calibration.md`); only
flip + between-signal (ETH) is calibrated today. What Himalaya is being asked to
confirm is the **sign, the magnitude order, and the monthly consistency** — not
the third decimal.

| exit | z=3.5 | z=4.5 | trades/day (US) |
|---|---|---|---|
| episode | +1.38 bps/trade (9/12 months +) | +1.98 (11/12 months +) | ~6–10 / ~2–3 |
| **triple-barrier X=10** | **+1.99** (hit 0.67) | **+3.06** (hit 0.71) | ~4 / ~1.5 |
| triple-barrier X=20 | +3.27 (hit 0.58) | +3.89 (hit 0.60) | ~4 / ~1.5 |

Supporting signal-quality evidence (mid-to-mid, no cost — `doc/return-distribution-verification.md`):
the underlying directional signal is broad and calibrated — **+1.8 bps/trade at
z≥1.5, 99% of days positive, 12/12 months positive**, and the biggest single day is
≤1.7% of the yearly total (not event-driven; the 2025-10-11 / 2026-02-08 event days
were the *best* days).

---

## Honest limitations (please read before acting on the number)

1. **Uncalibrated** — the magnitude may move materially under Himalaya. Trust the
   sign and the ordering, calibrate the size.
2. **Thin frequency** — ~2–10 trades/day in the US session only. Real but small
   absolute PnL; capacity-limited.
3. **Edge lives in the high-z tail** — z≥3.5 selects the rare, big-move signals
   whose move clears the ~2.33 bp spread. Below z≈3 it is a net loser as a taker.
   This is a spread-bound edge, not a broad one.
4. **Search is exhausted** — more formulas, tighter markets (GMO spot, COINCHECK
   data-blocked), and this price-barrier exit were all tried; none widened the edge
   or raised its frequency. Himalaya is the gate, not more scanning.

---

## Second execution venue — COINCHECK BTC_SPOT (blocked on data backfill)

COINCHECK BTC_SPOT is the tightest JPY-BTC market available (median spread **1.81
bps**, vs GMO BTC_JPY 2.33). It is wired as a drop-in *execution/basis* variant of
the same edge — the signal is unchanged (all 19 factors on the BINANCE leader); only
the `b` leg and target are swapped. COINCHECK has **MarketPrice only** (no OBI /
TradeAggression / BookState), so it can never be a signal source, only execution —
which is all this composite needs from `b` (`b_mid`).

- Spec: `composite_coincheck.yaml` (this folder).
- Runner: `run_coincheck.py` (this folder) — one command, mirrors the GMO US-hours
  run (JST 22,23,0,1,2), both exits, z=3.5/4.5.

**Status: BLOCKED ON DATA.** As of 2026-08-02 only 7 days of COINCHECK MarketPrice
exist (2026-05-25 … 05-31) — a smoke test with no statistical power. The 7-day
smoke run reproduces the *shape* (only the z≥3.5 tail is net-positive; the tighter
spread does **not** rescue the low-z zone — consistent with edge/spread coupling)
but nothing more. **To validate: backfill BTC_SPOT MarketPrice across train
(2025-07-01 … 2026-02-28) and holdout (2026-03-01 … 2026-06-30)**, then run
`run_coincheck.py`. OBI/TradeAggression/BookState are NOT needed for COINCHECK.

---

## Reproduce the scan numbers

```bash
# blend + step-2 (episode) at US hours, prints blend IC + net at z=1.5/2.5,
# and writes composite_spec.yaml:
python src/cli.py blend --horizon 300 --step2 --jst-hours 22,23,0,1,2 \
    --start 2025-07-01 --end 2026-06-30 \
    --out cycle/13_results/blend/BTC_JPY_us $(ls cycle/12_specs/*BTC_JPY.yaml)

# triple-barrier exit instead of episode:
python src/cli.py blend --horizon 300 --step2 --exit triple_barrier --barrier-bps 10 \
    --jst-hours 22,23,0,1,2 --start 2025-07-01 --end 2026-06-30 \
    --out cycle/13_results/blend/BTC_JPY_us_tb $(ls cycle/12_specs/*BTC_JPY.yaml)

# signal-quality (time distribution, mid-to-mid) of the composite:
python src/cli.py learn --op return_dist \
    --spec cycle/15_handoffs/blend-btc-jpy-us-z35/composite.yaml \
    --thresholds 1.0,1.5,2.5,3.5 --start 2025-07-01 --end 2026-06-30
```

The z≥3.5 / z≥4.5 tail numbers in the table come from running the saved composite
through `run_spec` at those thresholds (episode and triple_barrier); see
`project_signal_combination` memory / this session's scratchpad for the exact sweep.

---

## What a clean Himalaya result looks like

- **Sign holds**: net positive at z≥3.5 in the holdout window, both exits.
- **Monthly consistency holds**: the majority of holdout months positive (scan: 9–11/12).
- **Magnitude is within order**: Himalaya net within ~±1 bp of the scan's per-trade
  figure would confirm the mechanism is trustworthy for this signal class (it would
  then also validate expression+triple_barrier as a calibrated mechanism, per
  `doc/calibration.md`, for future ideas).

If the sign or monthly consistency does **not** survive Himalaya, the conclusion is
that this signal class cannot fund a taker at GMO BTC_JPY spreads — a legitimate and
valuable negative result that closes the current direction.
