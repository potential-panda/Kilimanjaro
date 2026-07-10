"""Standardised statistics aggregation for scan results.

Output key names (used across all scan styles):
    n                number of trades with valid return
    avg_gross        mean signed return before costs (bps)
    avg_net          mean net return after costs (bps)
    hit_rate         fraction where gross return > 0
    hit_rate_net     fraction where gross return > costs_bps
    std_bps          std dev of gross returns
    sharpe_per_trade avg_net / std_net
    t_stat           avg_net / (std_net / sqrt(n))
    p_value_1sided   one-sided P(mean <= 0 | data) via t-distribution
    skewness         skewness of net returns
    total_net_bps    n * avg_net
    ic               Pearson corr(signal_strength, return_bps)  [optional]
    avg_hold_s       mean holding period in seconds             [optional]
    median_hold_s    median holding period                      [optional]
    median_gross     median gross return                        [optional]
    p25_bps          25th percentile gross return               [optional]
    p75_bps          75th percentile gross return               [optional]
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_stats(
    returns: pd.Series,
    costs_bps: float,
    signal_strength: pd.Series | None = None,
    holding_seconds: pd.Series | None = None,
) -> dict:
    """Compute standardised trade statistics.

    Parameters
    ----------
    returns : pd.Series
        Signed gross returns in basis points (NaN rows should be dropped before calling).
    costs_bps : float
        Round-trip cost per trade in bps, subtracted to get net returns.
    signal_strength : pd.Series, optional
        Raw signal value aligned with returns, used to compute IC.
    holding_seconds : pd.Series, optional
        Per-trade holding time in seconds. When provided, adds avg_hold_s,
        median_hold_s, median_gross, p25_bps, p75_bps to output.
    """
    n = len(returns)
    nan = float("nan")

    if n == 0:
        base: dict = {
            "n": 0, "avg_gross": nan, "avg_net": nan,
            "hit_rate": nan, "hit_rate_net": nan,
            "std_bps": nan, "sharpe_per_trade": nan,
            "t_stat": nan, "p_value_1sided": nan,
            "skewness": nan, "total_net_bps": nan,
        }
        if signal_strength is not None:
            base["ic"] = nan
        if holding_seconds is not None:
            base.update({"avg_hold_s": nan, "median_hold_s": nan,
                         "median_gross": nan, "p25_bps": nan, "p75_bps": nan})
        return base

    net = returns - costs_bps
    avg_gross = float(returns.mean())
    avg_net = float(net.mean())
    std_net = float(net.std())

    if n > 1 and std_net > 0:
        from scipy import stats as _st
        t_stat = float(avg_net / (std_net / np.sqrt(n)))
        p_value = float(_st.t.sf(t_stat, df=n - 1))
        sharpe = avg_net / std_net
    else:
        t_stat = p_value = sharpe = nan

    result: dict = {
        "n": n,
        "avg_gross": avg_gross,
        "avg_net": avg_net,
        "hit_rate": float((returns > 0).mean()),
        "hit_rate_net": float((returns > costs_bps).mean()),
        "std_bps": float(returns.std()),
        "sharpe_per_trade": sharpe,
        "t_stat": t_stat,
        "p_value_1sided": p_value,
        "skewness": float(net.skew()) if n > 2 else nan,
        "total_net_bps": float(n * avg_net),
    }

    if signal_strength is not None:
        result["ic"] = float(signal_strength.corr(returns)) if n > 2 else nan

    if holding_seconds is not None:
        result["avg_hold_s"] = float(holding_seconds.mean())
        result["median_hold_s"] = float(holding_seconds.median())
        result["median_gross"] = float(returns.median())
        result["p25_bps"] = float(returns.quantile(0.25))
        result["p75_bps"] = float(returns.quantile(0.75))

    return result
