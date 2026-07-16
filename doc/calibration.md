# Calibrating the scan against Himalaya

The fast scan is a **cheap judge**; Himalaya is the **expensive judge**, much
closer to reality. The whole funnel only works if the cheap judge's verdicts
track the expensive one — otherwise a scan "pass" means nothing. Calibration is
how we earn the right to trust the cheap "no" (and "pass") without re-running
Himalaya on every idea.

This is the same rule as CLAUDE.md's Verification philosophy — *"confirm the same
signal direction and magnitude trend"*, not a byte-for-byte replica. What follows
is how to apply it as scans multiply.

## Calibrate per mechanism, not per formula

The formula only decides *when* to enter — it is an entry trigger. What can make
the scan diverge from Himalaya is the **return calculation + execution model**:
how the fill is priced, when the position exits, what cost is paid. So the unit
that needs a scan-vs-Himalaya check is the **mechanism**, not the signal:

| mechanism (entry style + return calc) | calibration status |
|---|---|
| flip + between-signal exit | **calibrated** (ETH: 0¥ entry, 0.000s, 98.3% PnL) |
| expression + fixed-horizon (mid-to-mid) | not yet |
| expression + episode (bid/ask, hold-while-condition) | not yet |

Calibrate a mechanism once, on a few param sets, and **every formula using that
mechanism inherits the trust** — you never re-verify per formula. That is what
keeps "run Himalaya too" from eating the speed advantage: a handful of
calibrations total, not one per idea.

Caveat: choose calibration cases with **varied entry conditions** (times of day,
volatility, markets), since a formula that only fires in odd conditions could
stress the mechanism in a way a narrow sample missed.

## The acceptance bar scales with how tightly the mechanism mirrors Himalaya

- **Tight mirror** (flip + between-signal reproduces the backtest's fills): demand
  near-exact agreement — entry price 0¥, timing 0.000s, PnL ≥95% exact.
- **Looser mechanism** (episode exits on its own condition-clear, which Himalaya
  will not reproduce trade-for-trade): demand **direction agrees, magnitude is the
  same order, and the ranking of param sets agrees** — the scan's best is
  Himalaya's best-ish.

Name the bar for a mechanism up front, so you neither over-demand (reject a fine
mechanism for failing an exactness it was never going to meet) nor under-demand
(trust a mechanism that only vaguely tracks).

## Procedure for a new mechanism

1. Pick several param sets spanning varied entry conditions.
2. Run each through both the scan and Himalaya over a shorter period.
3. Check agreement at the bar chosen for that mechanism's tightness.
4. Record the result (see registry below). If it agrees, the mechanism becomes
   scan-trusted; if not, scans on it are trend-only until the divergence is
   understood.

## Residual risk is bounded, not zero — and that is fine

Calibration shrinks model risk to a residual bounded by (a) how representative the
sample was and (b) drift: a mechanism verified today can diverge if Himalaya, the
execution model, or the data changes. So it is **not one-and-done** — re-check a
mechanism when it or the simulator changes. Everything left after a reasonable
check, we knowingly accept. Perfect fidelity is not the goal; being *as reasonable
as we can and owning the remainder* is.

## Make it structural: a calibration registry

Trust should live in the machine, not in memory. Keep a small registry of which
mechanisms are scan-trusted, with the evidence (the verify-scan run, the bar used,
the date). Then any scan running on an **un-calibrated** mechanism should surface a
flag — e.g. *"episode mechanism not yet Himalaya-calibrated — treat as
trend-only"* — so the funnel itself tells you when a "pass" rests on an unverified
judge, instead of you having to remember. Same principle as putting rigor in the
framework defaults: the machine refuses to quietly over-trust itself.

(Registry not built yet; `cli.py verify-scan` is the per-mechanism check, so far
run only for flip + between-signal on ETH.)
