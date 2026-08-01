"""Learn descriptive metrics about a signal — not a scan.

A scan measures a signal's edge (signal -> return). This measures a *property of
the signal itself*, over a window, with no return calc, target, split, or cost
involved. Today it computes one metric ("age"); more ops slot in beside it (each
a function that turns an evaluated signal series into a stats table), exposed as
`cli.py learn --op <name>`.

Age: how long the signal's threshold-crossing episodes last. `threshold_age`
(op/age.py) is the same op the scan uses for min_age/max_age gating; here it is
turned into a distribution — the thing you consult to *choose* age_windows and
horizons before scanning. Signal-agnostic: it works on any expression, keyed by
(expr label, threshold), which is why it is no longer bundled into an OBI-only
scan flavour.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.op.age import threshold_age
from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

AGE_STAT_FIELDS = [
    "expr",
    "threshold",
    "n_episodes",
    "ep_per_day",
    "age_mean",
    "age_p25",
    "age_p50",
    "age_p75",
    "age_p90",
    "age_max",
]


def age_distribution(label: str, series: pd.Series, threshold: float, days: float) -> dict:
    """One row of age stats for a signal series at one threshold.

    An episode is a maximal run of seconds with |series| > threshold; age is the
    seconds since it began (0 at the crossing). n_episodes counts crossings
    (age == 0); the percentiles describe how long episodes persist.
    """
    ages = threshold_age(series, threshold)
    active = ages[ages >= 0]
    n_ep = int((ages == 0).sum())
    if len(active) == 0:
        return {
            "expr": label, "threshold": threshold,
            "n_episodes": 0, "ep_per_day": 0.0,
            "age_mean": None, "age_p25": None, "age_p50": None,
            "age_p75": None, "age_p90": None, "age_max": None,
        }
    return {
        "expr": label,
        "threshold": threshold,
        "n_episodes": n_ep,
        "ep_per_day": round(n_ep / days, 2) if days else 0.0,
        "age_mean": round(float(active.mean()), 1),
        "age_p25": round(float(active.quantile(0.25)), 1),
        "age_p50": round(float(active.quantile(0.50)), 1),
        "age_p75": round(float(active.quantile(0.75)), 1),
        "age_p90": round(float(active.quantile(0.90)), 1),
        "age_max": int(active.max()),
    }


def learn_age(
    spec: dict,
    start: str,
    end: str,
    *,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> list[dict]:
    """Age-distribution rows for every (expression, threshold) in a spec.

    Loads each expression's feature frame once, evaluates it, and applies every
    spec threshold — the frame depends on markets+columns, not the threshold, so
    the eval is done once per expression.
    """
    import datetime as _dt

    days = max((_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days, 1)
    markets = spec["markets"]
    thresholds = spec["thresholds"]

    rows: list[dict] = []
    for e in spec["expressions"]:
        expr = e["expr"]
        label = e.get("label") or expr
        sig = ExpressionSignal.create(markets=markets, expr=expr, threshold=0.0)
        frame = sig.load_data(start, end, data_root)
        series = eval_expr(expr, frame)
        for thr in thresholds:
            rows.append(age_distribution(label, series, thr, days))
    return rows


RETURN_DIST_FIELDS = [
    "expr",
    "threshold",
    "horizon",
    "n_trades",
    "trades_per_day",
    "mean_bps",
    "n_days",
    "pos_day_share",
    "max_day_share_pct",
    "top10_day_share_pct",
    "n_months",
    "pos_month_share",
]


def return_distribution(
    label: str,
    series: pd.Series,
    ts: np.ndarray,
    threshold: float,
    horizon: int,
    mp,
) -> dict:
    """Time distribution of a signal's mid-to-mid directional returns.

    The diagnostic behind the "is the edge broad or event-driven?" check: fire
    when |series| > threshold, decimate to a non-overlapping horizon-spaced
    subset, take the signed mid-to-mid forward return (NO cost — this measures
    signal quality, not tradeability), and describe how those returns spread
    over calendar days and months. A healthy signal has a high positive-day
    share and no single day / handful of days dominating the total.

    `mp` is a loaded TargetPrices (bid/ask/mid + first_at_or_after) for the target.
    """
    from kilimanjaro.framework.schedule import add_jst_columns
    from kilimanjaro.return_calc.fixed_horizon import _nonoverlap_indices

    s = series.to_numpy(dtype=float)
    fired = np.abs(s) > threshold
    empty = {
        "expr": label, "threshold": threshold, "horizon": horizon,
        "n_trades": 0, "trades_per_day": 0.0, "mean_bps": None, "n_days": 0,
        "pos_day_share": None, "max_day_share_pct": None,
        "top10_day_share_pct": None, "n_months": 0, "pos_month_share": None,
    }
    if not fired.any():
        return empty

    ts_f = ts[fired]
    direction = np.sign(s[fired])
    keep = _nonoverlap_indices(ts_f, float(horizon))
    ts_f, direction = ts_f[keep], direction[keep]

    n = len(mp.ts)
    ein = mp.first_at_or_after(ts_f)
    eout = mp.first_at_or_after(ts_f + horizon)
    ok = (ein < n) & (eout < n)
    ei = np.clip(ein, 0, max(n - 1, 0))
    xo = np.clip(eout, 0, max(n - 1, 0))
    ret = np.where(ok, (mp.mid[xo] - mp.mid[ei]) / mp.mid[ei] * 10000.0, np.nan)
    df = pd.DataFrame({"ts": ts_f, "signed": direction * ret}).dropna()
    if df.empty:
        return empty
    df = add_jst_columns(df, ts_col="ts")

    daily = df.groupby("jst_date")["signed"].sum()
    monthly = df.groupby(df["jst_date"].str[:7])["signed"].sum()
    total = float(daily.sum())
    top10 = float(daily.sort_values(ascending=False).head(10).sum())
    share = lambda v: round(v / total * 100, 1) if total else None  # noqa: E731
    return {
        "expr": label,
        "threshold": threshold,
        "horizon": horizon,
        "n_trades": int(len(df)),
        "trades_per_day": round(len(df) / max(len(daily), 1), 1),
        "mean_bps": round(float(df["signed"].mean()), 3),
        "n_days": int(len(daily)),
        "pos_day_share": round(float((daily > 0).mean()), 3),
        "max_day_share_pct": share(float(daily.max())),
        "top10_day_share_pct": share(top10),
        "n_months": int(len(monthly)),
        "pos_month_share": round(float((monthly > 0).mean()), 3),
    }


def learn_return_dist(
    spec: dict,
    start: str,
    end: str,
    *,
    thresholds: list[float] | None = None,
    horizon: int = 300,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> list[dict]:
    """Return-distribution rows for every (expression, threshold) in a spec.

    Answers "is the signal's directional edge spread across time, or driven by a
    few events?" — mid-to-mid, no cost. Needs the spec's `target` for prices.
    """
    from kilimanjaro.data.price_cache import load_target_prices

    markets = spec["markets"]
    thresholds = thresholds or spec.get("thresholds", [1.5])
    t = spec["target"]
    mp = load_target_prices(
        t["exchange"], t["symbol"], t["execution_size"], start, end, data_root
    )

    rows: list[dict] = []
    for e in spec["expressions"]:
        expr = e["expr"]
        label = e.get("label") or expr
        sig = ExpressionSignal.create(markets=markets, expr=expr, threshold=0.0)
        frame = sig.load_data(start, end, data_root)
        series = eval_expr(expr, frame)
        ts = frame["ts"].to_numpy()
        for thr in thresholds:
            rows.append(return_distribution(label, series, ts, thr, horizon, mp))
    return rows
