"""Causal series operators for signal expressions.

Every op here is strictly causal: the output at time t depends only on
inputs at or before t. That property is what makes an expression built from
these ops safe to scan without lookahead — and it is enforced by test
(tests/test_expression_signal.py truncates the input and checks the prefix
output is unchanged for every registered op).

All ops assume a regular 1-second grid (the feature-frame contract), so
window/halflife arguments are in seconds.

OPS is the whitelist namespace available inside ExpressionSignal formulas —
an expression can call these and nothing else.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(x: pd.Series, halflife: float) -> pd.Series:
    """Exponential moving average, halflife in seconds (grid steps)."""
    return x.ewm(halflife=halflife, min_periods=1).mean()


def roll_mean(x: pd.Series, window: int) -> pd.Series:
    return x.rolling(int(window), min_periods=max(2, int(window) // 2)).mean()


def roll_std(x: pd.Series, window: int) -> pd.Series:
    return x.rolling(int(window), min_periods=max(2, int(window) // 2)).std()


def roll_sum(x: pd.Series, window: int) -> pd.Series:
    return x.rolling(int(window), min_periods=max(2, int(window) // 2)).sum()


def roll_max(x: pd.Series, window: int) -> pd.Series:
    return x.rolling(int(window), min_periods=max(2, int(window) // 2)).max()


def roll_min(x: pd.Series, window: int) -> pd.Series:
    return x.rolling(int(window), min_periods=max(2, int(window) // 2)).min()


def zscore(x: pd.Series, window: int) -> pd.Series:
    """(x - rolling mean) / rolling std over a trailing window."""
    m = roll_mean(x, window)
    s = roll_std(x, window)
    return (x - m) / s.replace(0.0, np.nan)


def lag(x: pd.Series, n: int) -> pd.Series:
    """x shifted n steps into the past (n must be >= 0: no lookahead)."""
    n = int(n)
    if n < 0:
        raise ValueError("lag(x, n): n must be >= 0 — negative lag is lookahead")
    return x.shift(n)


def diff(x: pd.Series, n: int = 1) -> pd.Series:
    return x - lag(x, n)


def ret(x: pd.Series, n: int) -> pd.Series:
    """Log return of x over the trailing n steps."""
    return np.log(x) - np.log(lag(x, n))


def sign(x: pd.Series) -> pd.Series:
    return np.sign(x)


def abs_(x: pd.Series) -> pd.Series:
    return x.abs()


def clip(x: pd.Series, lo: float, hi: float) -> pd.Series:
    return x.clip(lo, hi)


# Whitelist namespace for expression evaluation. Keys are the callable names
# usable inside a formula; "abs" maps to abs_ so formulas read naturally.
OPS: dict[str, object] = {
    "ema": ema,
    "roll_mean": roll_mean,
    "roll_std": roll_std,
    "roll_sum": roll_sum,
    "roll_max": roll_max,
    "roll_min": roll_min,
    "zscore": zscore,
    "lag": lag,
    "diff": diff,
    "ret": ret,
    "sign": sign,
    "abs": abs_,
    "clip": clip,
}
