# qate implementation guide — COINCHECK BTC_SPOT blend composite

Companion to `validation-request.md`. This is the **how** for a code agent (or
human) building the strategy in qate (`potential-panda-core`,
`qate.trading.strategy`). It exists because the composite is defined in
Kilimanjaro's **batch / 1-second-grid** semantics, while qate is an **event-driven
tick** framework — porting is a real reimplementation, and the edge lives in the
`z≥3.5` tail, so **feature parity is pass/fail, not best-effort**.

> **PRIMARY TARGET: COINCHECK / BTC_SPOT (0-fee, confirmed).** The signal is
> unchanged from the original BTC_JPY work — **all 19 factors are on the BINANCE
> leader (`a`)**; only the execution/basis leg `b` and the target are
> COINCHECK/BTC_SPOT/0.002. Use `composite_coincheck.yaml`. Where this guide says
> "GMO/BTC_JPY" below, read **COINCHECK/BTC_SPOT**, with these venue deltas:
> **no GMO Saturday-maintenance block** (instead confirm and block COINCHECK's own
> maintenance windows), and **taker fee = 0**. Everything about the BINANCE-side
> features, ops, grid, and parity harness is identical.

> **Do the cheap thing first.** Don't port 19 features until the edge is confirmed.
> Section 1 (precomputed-signal replay) validates the edge in Enoshima without any
> feature port. Only if that passes do Section 2 (full streaming port for prod) and
> Section 3 (parity harness). This mirrors the repo's calibrate-then-build order.

---

## 1. Enoshima validation shortcut — precomputed-signal replay (do this first)

Goal: confirm the edge in Enoshima **without** reimplementing features.

1. In Kilimanjaro, evaluate the composite over the validation window and export a
   fired-signal table:
   ```python
   from kilimanjaro.signal.expression import ExpressionSignal, eval_expr
   import yaml
   spec = yaml.safe_load(open("composite_coincheck.yaml"))
   expr = spec["expressions"][0]["expr"]
   sig  = ExpressionSignal.create(markets=spec["markets"], expr=expr, threshold=3.5)
   frame = sig.load_data("2025-07-01", "2026-06-30")
   fired = sig.generate(frame)   # ts, direction, strength(=composite z), episode
   # keep US hours only, then write [ts, direction] to a file qate can replay
   ```
2. Build a **thin** qate strategy that carries no features — it just holds the
   `{ts → direction}` table and, in `handle_order_book`, opens/closes per the
   triple-barrier rule (Section 2.4) when `now_ts` matches a fired entry. All the
   intelligence is precomputed; qate only executes.
3. Run it through Enoshima on the holdout window. This measures the **edge** under
   the real execution/backtest engine. If the sign / monthly consistency don't
   survive here, stop — no port needed.

Limitation: this validates but is **not deployable** — prod must compute the
signal live from the tick stream (Section 2). Precompute is replay-only.

---

## 2. Full streaming port (for prod)

### Reference strategies to follow

Two existing strategies are the patterns to copy — read them before writing code:

1. **corvus** — `potential-panda-core/src/qate/strategy/corvus/` (`config.py`,
   `v1.py`). The **minimal complete `Strategy`**: the smallest working shape to
   start from (config + a single version).
2. **pisces** — `potential-panda-core/src/qate/strategy/pisces/` (`source.py`,
   `schedule.py`, `adjust_inventory.py`, `v1.py`/`v2.py`, `DESIGN.md`). A **fuller
   example**: how to source features (`source.py`), how to do **time-of-day
   scheduling** (`schedule.py` — directly relevant to our JST-hours filter),
   inventory handling, and multi-version layout with a design doc.
Suggested mapping: start from **corvus** for the skeleton, take **pisces**'s
`source.py`/`schedule.py` patterns for feature sourcing + the JST-hours filter, and
implement execution and risk from the approved runtime contracts.

### 2.1 Strategy skeleton

Subclass `qate.trading.strategy.Strategy` (skeleton: **corvus**; fuller patterns:
**pisces** — see above). You receive **both markets'** streams — BINANCE
BTC_USDT (leader, alias `a`, carries all 19 factors) and COINCHECK BTC_SPOT
(target/execution, alias `b` — used only for `b_mid` in the basis terms):

```python
class Variant(Strategy[Config]):
    def handle_order_book(self, ob):   # update best bid/ask, OBI, microprice per market
        self.now_ts = ob.get_ts()
        self._update_book_features(ob)          # a_*/b_* book features
        self._maybe_trade()                     # entry/exit decision
    def handle_trade(self, tr):        # update trade-aggression 1s buckets
        self._update_trade_features(tr)
```

Maintain **online state** for every feature (rolling accumulators), the 1-second
grid, and the open position.

### 2.2 Feature / op reference — reproduce these EXACTLY

Reference implementation: `src/kilimanjaro/data/feature_frame.py` (grid + sources),
`src/kilimanjaro/op/series.py` (ops), `src/kilimanjaro/op/age.py`. The composite's
19 factors and their inputs (full formula in `composite_coincheck.yaml`):

| factor group | Kilimanjaro column | source & semantics to match |
|---|---|---|
| basis | `a_mid / b_mid` | mid = (best_bid+best_ask)/2 per market, on the 1s grid |
| momentum | `ret(a_mid, {30,120,300})` | **log** return over N 1s-steps: `ln(mid_t) − ln(mid_{t−N})` |
| obi | `a_obi_ema_k{1,5}_{30,120}s` | OBIFeature `vol_imb_k{d}` (depth-d book imbalance) then EMA (see below); the feed must expose depth-k volume imbalance |
| orderflow | `a_ta_imbalance_qty_{5,30}s`, `a_ta_net_flow_10s` | TradeAggression: buy/sell qty imbalance & net signed flow over trailing N-sec, **1s buckets** |
| vwap_dev | `a_mid / a_ta_vwap_{10,60}s − 1` | trade VWAP over trailing N-sec vs mid |
| aggression | `a_ta_imbalance_count_{10,60}s`, `a_ta_buy_sell_ratio_30s` | count-based aggressor imbalance |
| microprice | `a_bs_microprice_k{1,5}`, `a_bs_weighted_imb_k{1,5}`, `a_bs_best_mid` | BookState: size-weighted microprice & weighted book imbalance at depth k |

**Ops (exact, from `op/series.py`) — parity depends on these:**

- `ema(x, halflife)` = pandas `x.ewm(halflife=halflife, min_periods=1).mean()`,
  halflife in **seconds** (= 1s grid steps).
- `roll_mean/std/sum/max/min(x, window)` = `x.rolling(window, min_periods=max(2,
  window//2)).*()` — **trailing** window of `window` 1s steps.
- `zscore(x, window)` = `(x − roll_mean(x,window)) / roll_std(x,window)`, std==0 →
  NaN. **The window INCLUDES the current point** (pandas rolling default); do NOT
  use a `closed="left"` window. Every factor is `zscore(..., 1800)` and basis uses
  a 900/3600 window inside — so you need trailing rolling mean+std over up to 3600
  points of online state.
- `ret` is **log** return; `lag(x,n)=shift(n)`; `diff=x−lag`.

**Grid & fill semantics (`feature_frame.py`):**
- 1-second grid: bucket by `floor(ts)`, take the **last** value in each 1s bucket
  (`last(col ORDER BY ts)`).
- Forward-fill onto the grid with a **300 s** staleness cap (`_DEFAULT_FFILL_LIMIT_S
  = 300`); beyond 300 s stale → NaN → the factor (and composite) goes quiet.
- Both markets are aligned on one shared 1s grid (intersection of their spans).

### 2.3 Composite assembly

Per 1s step, once features are computed:
1. `z_i = zscore(factor_i, 1800)` for each of the 19 factors.
2. `raw = Σ w_i · z_i` with the weights `w_i` from `composite_coincheck.yaml`
   (`expressions[0].expr` has them inline, e.g. `0.0547*(zscore(...))`).
3. `composite = zscore(raw, 1800)`.
4. Entry when `|composite| ≥ 3.5`; `direction = sign(composite)`.

### 2.4 Execution model — triple-barrier (from `return_calc/triple_barrier.py`)

- **Entry** (taker): BUY → lift the ask; SELL → hit the bid. Record `entry_mid`.
- **Exit** (taker), first of:
  - take-profit: mid ≥ `entry_mid·(1 + 10bps)` (BUY) / ≤ `entry_mid·(1 − 10bps)` (SELL);
  - stop: the opposite ±10 bps;
  - time cap: **300 s** elapsed.
  Close aggressively on the passive side (BUY exits at bid; SELL at ask).
- **One position at a time**; the non-overlap rule is: do not open a new trade until
  the previous exited, then re-enter on the **first fire after that exit** (sequential
  re-entry — the scan now matches this exactly; an early ±10 bps exit at +50 s frees a
  re-entry at +51 s, not after a fixed time_cap).
- **Cost**: +0.5 bp/order latency slippage (matches Enoshima / the scan's 1 bp
  round-trip). Bid/ask already carries the spread.

### 2.5 Filters & warmup

- **Time filter**: trade only JST hours 22,23,0,1,2. **COINCHECK, not GMO** — the GMO
  Saturday-maintenance block does NOT apply; instead confirm COINCHECK's own
  maintenance windows and block those. (`framework/schedule.py` for the hour filter;
  the scan used `ScheduleFilter(block_gmo_maintenance=False)` for COINCHECK.)
- **Warmup**: the 1800 s z-scores and the 3600 s basis window need ≥ 1 h of grid
  history before signals are valid — suppress entries until warm.

---

## 3. Parity harness — the pass/fail gate

**A port is not trustworthy until this passes.** It is the replay-parity check
for this mechanism (`doc/calibration.md`, `doc/verification`).

1. Pick a fixed replay window (e.g. one active US-session day of BTC).
2. Reference: `eval_expr(composite_expr, frame)` in Kilimanjaro → per-second
   `composite` z series (and each intermediate `z_i`).
3. Candidate: run the qate strategy in replay over the **same** ticks, logging its
   per-second `composite` z and each `z_i`.
4. Assert, on the overlapping 1s grid (after warmup):
   - each **factor** `z_i`: correlation ≥ 0.999 and max abs diff small — localizes
     drift to a single feature when it fails;
   - the **composite** z: correlation ≥ 0.999;
   - the **fired set** (`|z|≥3.5`, per direction): ≥ 95% overlap (Jaccard) — this is
     what actually trades, so it is the strictest check.
5. Only then trust the qate PnL. Re-run parity whenever a feature or the feed changes.

---

## 4. Known parity risks (where ports drift)

- **1s bucketing**: use the *last* tick in each second, not first/mean.
- **ffill 300 s cap**: a gap > 300 s must produce NaN (quiet), not a stale carry.
- **z-score window includes the current point** (not `closed="left"`); off-by-one
  here silently shifts the whole tail.
- **log vs simple return**: `ret` is `ln(mid_t) − ln(mid_{t−n})`.
- **halflife vs span** for EMA: `ewm(halflife=…)`, not `span`.
- **timezone**: hour filter is **JST** (UTC+9).
- **execution sides**: entry aggressive (ask BUY / bid SELL), exit on the closing
  aggressive side (bid BUY / ask SELL) — the spread is paid, as designed.
- **two-market alignment**: BINANCE leader and COINCHECK target on one shared 1s clock;
  a lead/lag or clock skew between feeds will not show in a single-market replay —
  test parity on the real two-feed replay.

---

## Feasibility note for an autonomous agent

With this guide + the referenced Kilimanjaro source + a qate strategy example, an
agent can produce a credible first-draft strategy. The **sign-off is Section 3**,
not the draft: the 19-feature port is where drift hides, and the thin `z≥3.5` edge
is unforgiving. Budget most of the effort on parity, not on the skeleton. And run
Section 1 (precomputed replay) before any of this — confirm the edge is real before
paying to port it.
