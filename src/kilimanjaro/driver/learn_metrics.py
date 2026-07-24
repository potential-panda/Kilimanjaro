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
