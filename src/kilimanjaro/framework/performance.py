"""Path-aware performance evaluation for a strategy/param equity curve.

Why this exists
---------------
`compute_stats` (stats.py) scores the *trade distribution*: mean net edge,
t-stat, hit rate. Those answer "is there a real per-trade edge?" — but they are
**path-blind**. Two params with the same mean edge can have completely
different equity curves: one grinds up steadily, the other makes all its money
in four months and sits in a 73%-of-profit drawdown the rest of the time. A
trader would never treat those as equal, yet the trade-distribution metrics do.

To rank parameters realistically, we also score the **path**: especially the
drawdown endured to earn the return.

What it measures
----------------
Everything is computed from the daily equity curve = cumulative sum of per-trade
net returns (bps) bucketed into JST calendar days and reindexed to a gap-free
daily grid (non-trading days are flat, so drawdown *durations* are real
calendar time). Metrics group into three families:

Drawdown distribution — the anchor. Not just the max: the whole shape of the
pain, because in live trading you endure the *distribution*, not one number.
    max_dd_bps        deepest peak-to-trough
    avg_dd_bps        mean underwater depth over all time (the "pain index")
    std_dd_bps        dispersion of underwater depth
    ulcer_index       RMS of the drawdown series — depth AND duration in one
                      number (a shallow-but-permanent drawdown scores as bad as
                      a deep-but-brief one)
    dd_episodes       count of distinct underwater stretches
    dd_avg_depth_bps  mean depth across episodes
    dd_std_depth_bps  std of episode depths
    dd_max_days       longest underwater stretch (calendar days)
    dd_avg_days       mean episode length
    pct_time_underwater  fraction of days spent below the prior peak

Risk-adjusted return — pain-normalized, so they compare across params/markets:
    sharpe_daily      annualized Sharpe of daily returns
    sortino_daily     Sharpe using downside deviation only
    calmar            annualized return / max drawdown
    martin            annualized return / ulcer index  ← the headline: return
                      per unit of drawdown pain (a.k.a. Ulcer Performance Index)
    recovery_factor   total profit / max drawdown

Return / consistency:
    total_net_bps, ann_return_bps, pct_pos_days, n_days

Ratios (calmar/martin/recovery/sharpe/sortino) are self-normalizing, so they are
the right fields to *rank* on; the raw bps drawdowns are diagnostics that scale
with trade frequency. `martin` is the recommended default rank metric — it is
exactly "how much return do I get per unit of drawdown pain I must endure."

Cost policy (framework/costs.py): the per-trade returns fed here are already
bid/ask (spread paid) and net of the flat round-trip latency slippage. Do not
re-charge spread or cost inside these metrics. The 0.5 bps/order is the realized
1-year average (spikes included) and matches the flat cost Himalaya applies, so
these path metrics are meant to track Himalaya's curve — not sit below it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_JST_OFFSET_S = 9 * 3600
_TOL = 1e-9

# Metric keys this module emits, in output order. Exported so callers
# (runner, evaluate) can register them without hardcoding the list twice.
PATH_FIELDS = [
    "total_net_bps",
    "ann_return_bps",
    "n_days",
    "pct_pos_days",
    "sharpe_daily",
    "sortino_daily",
    "calmar",
    "martin",
    "recovery_factor",
    "max_dd_bps",
    "avg_dd_bps",
    "std_dd_bps",
    "ulcer_index",
    "dd_episodes",
    "dd_avg_depth_bps",
    "dd_std_depth_bps",
    "dd_max_days",
    "dd_avg_days",
    "pct_time_underwater",
]

# JPY per-fill variant (Himalaya-faithful): same ideas, but the equity curve is
# actual JPY PnL (net_bps × notional, notional = execution_size × entry_price)
# cumulated at TRADE resolution, not equal-weight bps on a daily grid. This is
# the curve Grafana/Himalaya draw, so its drawdown/Martin/Calmar track Himalaya
# where the bps/daily curve does not (see doc/calibration.md). Use for scan↔
# Himalaya fidelity and a survivor's real drawdown; keep bps for cross-market
# ranking (scale-free).
JPY_FIELDS = [
    "jpy_total",
    "jpy_ann_return",
    "sharpe_jpy_daily",
    "calmar_jpy",
    "martin_jpy",
    "recovery_jpy",
    "jpy_max_dd",
    "jpy_avg_dd",
    "jpy_ulcer",
    "jpy_dd_max_days",
    "jpy_pct_time_underwater",
]


def _nan_metrics() -> dict:
    return {k: float("nan") for k in PATH_FIELDS}


def _nan_jpy() -> dict:
    return {k: float("nan") for k in JPY_FIELDS}


def daily_equity(net_bps: np.ndarray, ts_seconds: np.ndarray) -> pd.Series:
    """Cumulative net-return curve on a gap-free JST daily grid.

    net_bps    : per-trade NET returns in bps (costs already subtracted)
    ts_seconds : per-trade unix timestamps (seconds), same length/order-agnostic

    Returns the cumulative-sum equity indexed by JST date, with missing calendar
    days filled forward (flat) so drawdown durations are measured in real days.
    """
    s = pd.Series(np.asarray(net_bps, dtype=float), copy=False)
    days = (np.asarray(ts_seconds, dtype="int64") + _JST_OFFSET_S) // 86400
    daily = s.groupby(days).sum()
    daily.index = pd.to_datetime(daily.index * 86400, unit="s")
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full, fill_value=0.0)
    return daily.cumsum()


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    return np.maximum.accumulate(equity) - equity


def _episodes(dd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-underwater-episode (depth, duration_days) from a daily dd series."""
    under = dd > _TOL
    if not under.any():
        return np.array([]), np.array([])
    # episode boundaries where under flips
    edges = np.diff(under.astype(np.int8))
    starts = list(np.where(edges == 1)[0] + 1)
    ends = list(np.where(edges == -1)[0] + 1)
    if under[0]:
        starts = [0] + starts
    if under[-1]:
        ends = ends + [len(dd)]
    depths, durs = [], []
    for a, b in zip(starts, ends):
        depths.append(float(dd[a:b].max()))
        durs.append(int(b - a))
    return np.array(depths), np.array(durs)


def compute_path_metrics(
    net_bps: np.ndarray,
    ts_seconds: np.ndarray,
    periods_per_year: int = 365,
    min_days: int = 5,
) -> dict:
    """Path/drawdown/risk-adjusted metrics for one strategy's daily equity curve.

    net_bps    : per-trade NET returns (bps), costs already applied.
    ts_seconds : per-trade unix timestamps (seconds).
    Returns a dict keyed by PATH_FIELDS; all-NaN if too few days to be meaningful.
    """
    net_bps = np.asarray(net_bps, dtype=float)
    if net_bps.size == 0:
        return _nan_metrics()

    eq = daily_equity(net_bps, ts_seconds)
    n_days = len(eq)
    if n_days < min_days:
        return _nan_metrics()

    equity = eq.to_numpy()
    daily_ret = np.diff(equity, prepend=0.0)
    total = float(equity[-1])
    ann_return = total / n_days * periods_per_year

    # daily risk-adjusted (all calendar days, so weekends count as flat 0)
    mean_d = float(daily_ret.mean())
    std_d = float(daily_ret.std(ddof=1)) if n_days > 1 else 0.0
    ann = np.sqrt(periods_per_year)
    sharpe = mean_d / std_d * ann if std_d > 0 else float("nan")
    downside = daily_ret[daily_ret < 0]
    dstd = float(np.sqrt(np.mean(downside**2))) if downside.size else 0.0
    sortino = mean_d / dstd * ann if dstd > 0 else float("nan")

    dd = _drawdown_series(equity)
    max_dd = float(dd.max())
    avg_dd = float(dd.mean())
    std_dd = float(dd.std(ddof=0))
    ulcer = float(np.sqrt(np.mean(dd**2)))
    depths, durs = _episodes(dd)

    # ratios: return per unit of pain. Guard tiny/zero denominators.
    calmar = ann_return / max_dd if max_dd > _TOL else float("nan")
    martin = ann_return / ulcer if ulcer > _TOL else float("nan")
    recovery = total / max_dd if max_dd > _TOL else float("nan")

    return {
        "total_net_bps": total,
        "ann_return_bps": ann_return,
        "n_days": n_days,
        "pct_pos_days": float((daily_ret > 0).mean()),
        "sharpe_daily": sharpe,
        "sortino_daily": sortino,
        "calmar": calmar,
        "martin": martin,
        "recovery_factor": recovery,
        "max_dd_bps": max_dd,
        "avg_dd_bps": avg_dd,
        "std_dd_bps": std_dd,
        "ulcer_index": ulcer,
        "dd_episodes": int(depths.size),
        "dd_avg_depth_bps": float(depths.mean()) if depths.size else 0.0,
        "dd_std_depth_bps": float(depths.std(ddof=0)) if depths.size else 0.0,
        "dd_max_days": int(durs.max()) if durs.size else 0,
        "dd_avg_days": float(durs.mean()) if durs.size else 0.0,
        "pct_time_underwater": float((dd > _TOL).mean()),
    }


def compute_path_metrics_jpy(
    net_bps: np.ndarray,
    ts_seconds: np.ndarray,
    notional: np.ndarray,
    periods_per_year: int = 365,
    min_points: int = 5,
) -> dict:
    """Himalaya-faithful path metrics on the JPY per-fill equity curve.

    net_bps  : per-trade NET returns (bps), costs already applied.
    notional : per-trade JPY notional = execution_size × entry_price.
    Equity = cumulative JPY PnL (net_bps/1e4 × notional) at trade resolution;
    drawdown is measured per fill (catches intraday dips the daily bps curve
    hides). Durations come from the fill timestamps, in real days. Keys are
    JPY_FIELDS. All-NaN if too few trades.
    """
    net_bps = np.asarray(net_bps, dtype=float)
    ts = np.asarray(ts_seconds, dtype=float)
    notional = np.asarray(notional, dtype=float)
    if net_bps.size == 0 or net_bps.size < min_points:
        return _nan_jpy()

    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    pnl = (net_bps[order] / 1e4) * notional[order]  # per-fill JPY PnL
    equity = np.cumsum(pnl)
    total = float(equity[-1])
    span_days = max((ts[-1] - ts[0]) / 86400.0, 1.0)
    ann_return = total / span_days * periods_per_year

    dd = _drawdown_series(equity)  # per-fill drawdown, JPY
    max_dd = float(dd.max())
    avg_dd = float(dd.mean())
    ulcer = float(np.sqrt(np.mean(dd**2)))
    pct_uw = float((dd > _TOL).mean())

    # longest underwater stretch in real days (episode boundaries via ts)
    under = dd > _TOL
    dd_max_days = 0.0
    if under.any():
        edges = np.diff(under.astype(np.int8))
        starts = list(np.where(edges == 1)[0] + 1)
        ends = list(np.where(edges == -1)[0] + 1)
        if under[0]:
            starts = [0] + starts
        if under[-1]:
            ends = ends + [len(dd) - 1]
        durs = [
            (ts[min(e, len(ts) - 1)] - ts[s]) / 86400.0 for s, e in zip(starts, ends)
        ]
        dd_max_days = float(max(durs)) if durs else 0.0

    # daily Sharpe (JST day buckets of per-fill PnL)
    day = (ts.astype("int64") + 9 * 3600) // 86400
    daily = pd.Series(pnl).groupby(day).sum()
    dstd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    sharpe = (
        float(daily.mean() / dstd * np.sqrt(periods_per_year))
        if dstd > 0
        else float("nan")
    )

    return {
        "jpy_total": total,
        "jpy_ann_return": ann_return,
        "sharpe_jpy_daily": sharpe,
        "calmar_jpy": ann_return / max_dd if max_dd > _TOL else float("nan"),
        "martin_jpy": ann_return / ulcer if ulcer > _TOL else float("nan"),
        "recovery_jpy": total / max_dd if max_dd > _TOL else float("nan"),
        "jpy_max_dd": max_dd,
        "jpy_avg_dd": avg_dd,
        "jpy_ulcer": ulcer,
        "jpy_dd_max_days": dd_max_days,
        "jpy_pct_time_underwater": pct_uw,
    }
