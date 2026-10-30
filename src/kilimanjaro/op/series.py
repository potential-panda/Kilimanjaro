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


def ema_halflife(x: pd.Series, halflife: float) -> pd.Series:
    """Exponential moving average parameterized by halflife in grid steps."""
    return x.ewm(halflife=halflife, min_periods=1).mean()


def ema_span(x: pd.Series, span: float) -> pd.Series:
    """Exponential moving average using the conventional span parameter."""
    return x.ewm(span=span, min_periods=1).mean()


def ema_alpha(x: pd.Series, alpha: float) -> pd.Series:
    """Exponential moving average using a direct smoothing factor."""
    return x.ewm(alpha=alpha, min_periods=1).mean()


def ewm_std(x: pd.Series, halflife: float) -> pd.Series:
    """Exponentially weighted sample standard deviation by halflife."""
    return x.ewm(halflife=halflife, min_periods=2).std()


def ema(x: pd.Series, halflife: float) -> pd.Series:
    """Backward-compatible alias for :func:`ema_halflife`."""
    return ema_halflife(x, halflife)


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


def roll_quantile(x: pd.Series, window: int, quantile: float) -> pd.Series:
    """Trailing quantile with the standard rolling-operator warm-up policy."""
    window = int(window)
    return x.rolling(window, min_periods=max(2, window // 2)).quantile(quantile)


def roll_rank(x: pd.Series, window: int) -> pd.Series:
    """Percentile rank in [0, 1] of the current value in its trailing window."""
    window = int(window)
    return x.rolling(window, min_periods=max(2, window // 2)).rank(pct=True)


def percentile_rank(x: pd.Series, window: int) -> pd.Series:
    """Descriptive alias for :func:`roll_rank`."""
    return roll_rank(x, window)


def _window_slope(values: np.ndarray) -> float:
    valid = np.isfinite(values)
    if valid.sum() < 2:
        return float("nan")
    y = values[valid]
    x = np.arange(len(values), dtype=float)[valid]
    x = x - x.mean()
    denominator = float(x @ x)
    return float((x @ (y - y.mean())) / denominator) if denominator else 0.0


def roll_slope(x: pd.Series, window: int) -> pd.Series:
    """Trailing ordinary-least-squares slope per grid step."""
    window = int(window)
    return x.rolling(window, min_periods=max(2, window // 2)).apply(
        _window_slope, raw=True
    )


def _linear_decay(values: np.ndarray) -> float:
    valid = np.isfinite(values)
    if not valid.any():
        return float("nan")
    weights = np.arange(1, len(values) + 1, dtype=float)[valid]
    return float(np.average(values[valid], weights=weights))


def decay_linear(x: pd.Series, window: int) -> pd.Series:
    """Trailing weighted mean with linearly increasing weight toward the present."""
    window = int(window)
    return x.rolling(window, min_periods=max(2, window // 2)).apply(
        _linear_decay, raw=True
    )


def bar_open(x: pd.Series, window: int) -> pd.Series:
    """Open of the trailing ``window``-step rolling bar."""
    window = int(window)
    return x.shift(window - 1).where(x.rolling(window).count() >= window)


def bar_high(x: pd.Series, window: int) -> pd.Series:
    """High of the trailing ``window``-step rolling bar."""
    window = int(window)
    return x.rolling(window, min_periods=window).max()


def bar_low(x: pd.Series, window: int) -> pd.Series:
    """Low of the trailing ``window``-step rolling bar."""
    window = int(window)
    return x.rolling(window, min_periods=window).min()


def bar_close(x: pd.Series, window: int) -> pd.Series:
    """Close of the trailing ``window``-step rolling bar."""
    window = int(window)
    return x.where(x.rolling(window).count() >= window)


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


def cross_up(x: pd.Series, y) -> pd.Series:
    """One on an upward crossing of ``x`` through ``y``, otherwise zero."""
    delta = x - y
    previous = lag(delta, 1)
    event = delta.notna() & previous.notna() & (delta > 0) & (previous <= 0)
    return event.astype(float)


def cross_down(x: pd.Series, y) -> pd.Series:
    """One on a downward crossing of ``x`` through ``y``, otherwise zero."""
    delta = x - y
    previous = lag(delta, 1)
    event = delta.notna() & previous.notna() & (delta < 0) & (previous >= 0)
    return event.astype(float)


def cross(x: pd.Series, y) -> pd.Series:
    """Signed crossing pulse: +1 upward, -1 downward, and zero otherwise."""
    return cross_up(x, y) - cross_down(x, y)


def ret(x: pd.Series, n: int) -> pd.Series:
    """Log return of x over the trailing n steps."""
    return np.log(x) - np.log(lag(x, n))


def sign(x: pd.Series) -> pd.Series:
    return np.sign(x)


def abs_(x: pd.Series) -> pd.Series:
    return x.abs()


def clip(x: pd.Series, lo: float, hi: float) -> pd.Series:
    return x.clip(lo, hi)


def min_(a: pd.Series, b) -> pd.Series:
    """Element-wise minimum of a series and another series or scalar."""
    return np.minimum(a, b)


def max_(a: pd.Series, b) -> pd.Series:
    """Element-wise maximum of a series and another series or scalar."""
    return np.maximum(a, b)


def _truth(x: pd.Series) -> pd.Series:
    """Element-wise truth value with missing observations treated as false."""
    return x.fillna(False).astype(bool)


def where(condition: pd.Series, when_true, when_false) -> pd.Series:
    """Element-wise conditional selection; unlike Python ``if``, works on series."""
    condition = _truth(condition)
    return pd.Series(
        np.where(condition, when_true, when_false),
        index=condition.index,
    )


def and_(a: pd.Series, b: pd.Series) -> pd.Series:
    """Element-wise logical AND. Missing observations are false."""
    return _truth(a) & _truth(b)


def or_(a: pd.Series, b: pd.Series) -> pd.Series:
    """Element-wise logical OR. Missing observations are false."""
    return _truth(a) | _truth(b)


def not_(x: pd.Series) -> pd.Series:
    """Element-wise logical NOT. Missing observations are false before negation."""
    return ~_truth(x)


_EPS = 1e-9


def imbalance(a: pd.Series, b: pd.Series) -> pd.Series:
    """Normalized imbalance (a - b) / (a + b), in [-1, 1] for non-negative a, b.

    Pointwise (no lookback), so trivially causal. The house normalization for
    two-sided quantities (buy vs sell, bid vs ask) — keeps every such signal on
    one comparable scale, the way the Makalu feature library uses it."""
    return (a - b) / (a + b + _EPS)


def rsi(x: pd.Series, window: int) -> pd.Series:
    """Relative Strength Index using Wilder smoothing over ``window`` steps."""
    window = int(window)
    change = diff(x, 1)
    gain = max_(change, 0.0)
    loss = max_(-change, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    relative_strength = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def macd(x: pd.Series, fast: float, slow: float) -> pd.Series:
    """Moving Average Convergence Divergence line using EMA spans."""
    return ema_span(x, fast) - ema_span(x, slow)


def macd_hist(
    x: pd.Series, fast: float, slow: float, signal: float
) -> pd.Series:
    """MACD line minus its span-based EMA signal line."""
    line = macd(x, fast, slow)
    return line - ema_span(line, signal)


def bollinger_position(
    x: pd.Series, window: int, deviations: float
) -> pd.Series:
    """Position relative to Bollinger bands: +1 upper, -1 lower, 0 mean."""
    width = deviations * roll_std(x, window)
    return (x - roll_mean(x, window)) / width.replace(0.0, np.nan)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder true range from aligned high, low, and close observations."""
    previous_close = lag(close, 1)
    ranges = pd.concat(
        [high - low, abs_(high - previous_close), abs_(low - previous_close)],
        axis=1,
    )
    return ranges.max(axis=1, skipna=True).where(high.notna() & low.notna())


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int
) -> pd.Series:
    """Average True Range using Wilder's recursive smoothing."""
    window = int(window)
    return true_range(high, low, close).ewm(
        alpha=1.0 / window,
        adjust=False,
        min_periods=window,
    ).mean()


def _psar_segment(
    high: np.ndarray,
    low: np.ndarray,
    step: float,
    max_step: float,
) -> np.ndarray:
    """Parabolic SAR for one finite, valid contiguous segment."""
    n = len(high)
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return out
    if n == 1:
        out[0] = low[0]
        return out

    bullish = bool(high[1] >= high[0])
    sar = low[0] if bullish else high[0]
    extreme = high[0] if bullish else low[0]
    acceleration = step
    out[0] = sar

    for i in range(1, n):
        candidate = sar + acceleration * (extreme - sar)
        if bullish:
            candidate = min(candidate, low[i - 1])
            if i > 1:
                candidate = min(candidate, low[i - 2])
            if low[i] < candidate:
                bullish = False
                sar = extreme
                extreme = low[i]
                acceleration = step
            else:
                sar = candidate
                if high[i] > extreme:
                    extreme = high[i]
                    acceleration = min(acceleration + step, max_step)
        else:
            candidate = max(candidate, high[i - 1])
            if i > 1:
                candidate = max(candidate, high[i - 2])
            if high[i] > candidate:
                bullish = True
                sar = extreme
                extreme = high[i]
                acceleration = step
            else:
                sar = candidate
                if low[i] < extreme:
                    extreme = low[i]
                    acceleration = min(acceleration + step, max_step)
        out[i] = sar
    return out


def psar(
    high: pd.Series,
    low: pd.Series,
    step: float = 0.02,
    max_step: float = 0.2,
) -> pd.Series:
    """Causal Parabolic SAR, resetting state across missing or invalid bars."""
    step = float(step)
    max_step = float(max_step)
    if step <= 0 or max_step < step:
        raise ValueError("psar: require 0 < step <= max_step")

    h = high.to_numpy(dtype=float)
    lo = low.to_numpy(dtype=float)
    valid = np.isfinite(h) & np.isfinite(lo) & (h >= lo)
    out = np.full(len(high), np.nan, dtype=float)
    start = 0
    while start < len(out):
        while start < len(out) and not valid[start]:
            start += 1
        end = start
        while end < len(out) and valid[end]:
            end += 1
        if start < end:
            out[start:end] = _psar_segment(h[start:end], lo[start:end], step, max_step)
        start = end + 1
    return pd.Series(out, index=high.index)


# Whitelist namespace for expression evaluation. Keys are the callable names
# usable inside a formula; "abs" maps to abs_ so formulas read naturally.
OPS: dict[str, object] = {
    "ema": ema,
    "ema_halflife": ema_halflife,
    "ema_span": ema_span,
    "ema_alpha": ema_alpha,
    "ewm_std": ewm_std,
    "roll_mean": roll_mean,
    "roll_std": roll_std,
    "roll_sum": roll_sum,
    "roll_max": roll_max,
    "roll_min": roll_min,
    "roll_quantile": roll_quantile,
    "roll_rank": roll_rank,
    "percentile_rank": percentile_rank,
    "roll_slope": roll_slope,
    "decay_linear": decay_linear,
    "bar_open": bar_open,
    "bar_high": bar_high,
    "bar_low": bar_low,
    "bar_close": bar_close,
    "zscore": zscore,
    "lag": lag,
    "diff": diff,
    "cross": cross,
    "cross_up": cross_up,
    "cross_down": cross_down,
    "ret": ret,
    "sign": sign,
    "abs": abs_,
    "clip": clip,
    "min_": min_,
    "max_": max_,
    "where": where,
    "and_": and_,
    "or_": or_,
    "not_": not_,
    "imbalance": imbalance,
    "rsi": rsi,
    "macd": macd,
    "macd_hist": macd_hist,
    "bollinger_position": bollinger_position,
    "true_range": true_range,
    "atr": atr,
    "psar": psar,
}
