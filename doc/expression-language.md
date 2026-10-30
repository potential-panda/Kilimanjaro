# Expression language

`ExpressionSignal` evaluates a restricted, causal formula over the regular
one-second feature frame. Expressions may contain frame-column names, numeric
constants, parentheses, registered function calls, arithmetic (`+`, `-`, `*`,
`/`, `**`, `%`), unary signs, and comparisons (`>`, `>=`, `<`, `<=`, `==`,
`!=`). Every expression must reference at least one frame column.

Python attributes, subscripts, comprehensions, lambdas, imports, keyword
arguments, conditional expressions, and native `and`/`or`/`not` are rejected.
Use the element-wise functions below for conditions over a series.

All lookbacks are grid steps and therefore seconds on the standard feature
frame. Every registered operator is covered by a prefix-causality test: results
through time `t` must be identical whether the input ends at `t` or continues.

## Registered operators

| Group | Operators | Result |
|---|---|---|
| Exponential | `ema(x, halflife)`, `ema_halflife(x, halflife)` | EWM mean by halflife; `ema` is the compatibility alias |
| Exponential | `ema_span(x, span)`, `ema_alpha(x, alpha)` | EWM mean by span or smoothing factor |
| Exponential | `ewm_std(x, halflife)` | EWM sample standard deviation |
| Rolling | `roll_mean`, `roll_std`, `roll_sum`, `roll_max`, `roll_min` | Trailing statistic over `(x, window)` |
| Rolling | `roll_quantile(x, window, q)` | Trailing quantile |
| Rolling | `roll_rank(x, window)`, `percentile_rank(x, window)` | Current value's percentile rank in its trailing window |
| Rolling | `roll_slope(x, window)` | Trailing OLS slope per second |
| Rolling | `decay_linear(x, window)` | Trailing mean with weights increasing toward the present |
| Normalization | `zscore(x, window)` | Trailing z-score |
| History | `lag(x, n)`, `diff(x, n)`, `ret(x, n)` | Past value, difference, and log return |
| Crossover | `cross(x, y)` | `+1` upward, `-1` downward, `0` otherwise |
| Crossover | `cross_up(x, y)`, `cross_down(x, y)` | One-second unsigned event pulse |
| Pointwise | `sign(x)`, `abs(x)`, `clip(x, lo, hi)` | Sign, magnitude, and bounds |
| Pointwise | `min_(x, y)`, `max_(x, y)` | Element-wise extrema with a series or scalar |
| Conditions | `where(c, x, y)` | Select `x` where `c`, otherwise `y` |
| Conditions | `and_(a, b)`, `or_(a, b)`, `not_(a)` | Element-wise logic; missing conditions are false |
| Microstructure | `imbalance(a, b)` | `(a-b)/(a+b+1e-9)` |
| Indicator | `rsi(x, window)` | RSI with Wilder smoothing |
| Indicator | `macd(x, fast, slow)` | Difference of span-based EMAs |
| Indicator | `macd_hist(x, fast, slow, signal)` | MACD minus its span-based signal EMA |
| Indicator | `bollinger_position(x, window, deviations)` | `0` at mean and `+1`/`-1` at the bands |
| Rolling bar | `bar_open`, `bar_high`, `bar_low`, `bar_close` | OHLC of `(x, window)` over a trailing, overlapping bar |
| Range | `true_range(high, low, close)` | Wilder true range |
| Range | `atr(high, low, close, window)` | Wilder-smoothed average true range |
| Trend | `psar(high, low, step, max_step)` | Recursive Parabolic SAR |

The basic rolling statistics use `max(2, window // 2)` observations before
emitting a value. Rolling OHLC requires a complete window. A rolling bar is an
overlapping trailing snapshot calculated at every second; it is not a
calendar-resampled candle. ATR and PSAR can consume those snapshots or native
aligned high/low/close feature columns.

Crossovers are one-second event pulses. They are suitable for event-entry
evaluation but should not be treated as continuous factors without deliberately
choosing sparse-factor semantics.

`psar` requires `0 < step <= max_step`. Missing observations or invalid bars
(`high < low`) produce `NaN` and reset its recursive state, preventing a stale
trend from crossing a data gap.

## Examples

Regime-gated momentum:

```text
where(ewm_std(ret(a_mid, 1), 300) > 0.0005,
      zscore(ret(a_mid, 30), 1800),
      0)
```

EMA crossover pulse:

```text
cross(ema_span(a_mid, 12), ema_span(a_mid, 26))
```

MACD signal-line crossover:

```text
cross(macd(a_mid, 12, 26), ema_span(macd(a_mid, 12, 26), 9))
```

ATR-normalized movement from rolling one-minute OHLC:

```text
(bar_close(a_mid, 60) - bar_open(a_mid, 60))
/ atr(bar_high(a_mid, 60), bar_low(a_mid, 60), bar_close(a_mid, 60), 14)
```

Parabolic SAR distance:

```text
bar_close(a_mid, 60)
- psar(bar_high(a_mid, 60), bar_low(a_mid, 60), 0.02, 0.2)
```
