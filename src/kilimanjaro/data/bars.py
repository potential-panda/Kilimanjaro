"""Bar clocks — the neutral event stream for the continuous-barrier framework.

A "bar" here is not a candle to look at; it is a DECISION POINT. The continuous
framing is: a clock says when to re-decide, a direction rule says which side, and
you hold through consecutive same-direction decisions — so execution cost is paid
per direction CHANGE, not per bar.

Why the clock is a first-class choice, and not just "range bars":

    A RANGE bar closes exactly when price has moved +/-B, so every decision point is
    a local extreme and you transact at the worst instant by construction. Measured
    on the SAR flip clock: an execution toll of 3.49-4.91 bps against a 2.78 median-
    conditions cost on COINCHECK, and — the tell — 1.71-2.28 bps against a 1.00 floor
    on BINANCE, where the spread is ~0. That ~1-2 bps excess is pure adverse TIMING,
    not spread width.

    TIME, VOLUME and DOLLAR clocks close at moments uncorrelated with price movement,
    so they should not pay that penalty. Volume/dollar additionally normalise for
    activity: bars form faster when the market is busy, which is when the cross-venue
    lead-lag is strongest, so observations concentrate where the edge lives.

All clocks return INDICES into the 1s feature-frame grid at which a bar closes.
"""

from __future__ import annotations

import numpy as np

_KINDS = ("time", "volume", "dollar", "tick", "range")


def bar_closes(kind: str, param: float, *, ts: np.ndarray,
               mid: np.ndarray | None = None,
               qty: np.ndarray | None = None) -> np.ndarray:
    """Indices where a bar closes, for the given clock.

    kind:
      time    param = seconds per bar
      volume  param = base-asset units per bar
      dollar  param = notional (qty*mid) per bar
      tick    param = grid rows per bar (1s grid -> same as time, kept for symmetry)
      range   param = fractional move from the bar's open (0.003 = 30 bps) — the
              price-anchored clock, included as the control to measure against.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown clock '{kind}' — expected one of {_KINDS}")
    n = len(ts)
    if n == 0:
        return np.empty(0, dtype=np.int64)

    if kind in ("time", "tick"):
        # Close on a fixed grid so bar boundaries are independent of price and of
        # activity — the cleanest control for adverse-timing.
        step = max(int(param), 1)
        idx = np.arange(step - 1, n, step, dtype=np.int64)
        return idx

    if kind in ("volume", "dollar"):
        if qty is None:
            raise ValueError(f"clock '{kind}' needs qty")
        w = np.nan_to_num(qty, nan=0.0)
        if kind == "dollar":
            if mid is None:
                raise ValueError("clock 'dollar' needs mid")
            w = w * np.nan_to_num(mid, nan=0.0)
        c = np.cumsum(w)
        if c[-1] <= 0 or param <= 0:
            return np.empty(0, dtype=np.int64)
        # A bar closes each time cumulative activity crosses another multiple of param.
        bucket = np.floor(c / float(param)).astype(np.int64)
        return np.flatnonzero(np.diff(bucket, prepend=bucket[0]) > 0).astype(np.int64)

    # range: walk forward, close when mid deviates by +/-param from the bar's open.
    if mid is None:
        raise ValueError("clock 'range' needs mid")
    return _range_closes(np.asarray(mid, dtype=float), float(param))


def _range_closes(mid: np.ndarray, rate: float) -> np.ndarray:
    """Close indices for a range bar of fractional size `rate` (loop: path-dependent)."""
    out: list[int] = []
    open_px = np.nan
    for i in range(len(mid)):
        m = mid[i]
        if not np.isfinite(m):
            continue
        if not np.isfinite(open_px):
            open_px = m
            continue
        if abs(m - open_px) >= rate * open_px:
            out.append(i)
            open_px = m
    return np.asarray(out, dtype=np.int64)


def runs_to_signals(close_idx: np.ndarray, direction: np.ndarray,
                    ts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compress consecutive same-direction bar decisions into held positions.

    THIS is what makes the framework continuous rather than a series of bets: a run of
    same-direction bars is ONE position, so execution cost is charged once per direction
    change. Returns (entry_ts, direction, exit_ts) with exit_ts = the next change (the
    final open run is dropped — it has no exit).
    """
    ok = np.isfinite(direction) & (direction != 0)
    ci, d = close_idx[ok], np.sign(direction[ok])
    if len(ci) == 0:
        return (np.empty(0), np.empty(0), np.empty(0))
    change = np.flatnonzero(np.diff(d, prepend=-d[0] if d[0] != 0 else 0) != 0)
    entry_i = ci[change]
    if len(entry_i) < 2:
        return (np.empty(0), np.empty(0), np.empty(0))
    return ts[entry_i[:-1]], d[change][:-1], ts[entry_i[1:]]


def hysteresis(values: np.ndarray, threshold: float) -> np.ndarray:
    """Direction with a dead band: flip only on an OPPOSITE-side threshold crossing.

    sign(z) flips every time z wobbles across zero, which in the continuous framework
    means paying a round trip per wobble — measured at ~2 bars per trade on the blend,
    i.e. a cost every other decision. Hysteresis holds the current side until the signal
    reaches `threshold` on the OTHER side, so the position persists and cost is paid only
    on genuine reversals. threshold=0 reduces to plain sign().
    """
    v = np.asarray(values, dtype=float)
    raw = np.where(np.isfinite(v) & (v >= threshold), 1.0,
                   np.where(np.isfinite(v) & (v <= -threshold), -1.0, 0.0))
    out = np.zeros(len(raw))
    cur = 0.0
    for i, r in enumerate(raw):          # carry the last decisive side forward
        if r != 0.0:
            cur = r
        out[i] = cur
    return out
