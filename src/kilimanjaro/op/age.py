"""Age operators: measure how long a signal has been in a given state.

These operate on regular-grid time series (e.g. 1s-resampled) where each
step equals one unit of elapsed time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sign_age(series: pd.Series) -> pd.Series:
    """Steps the sign of `series` has been continuously unchanged.

    Returns 0 at sign-change moments and increments each step the sign holds.
    Resets to 0 whenever sign is 0 (neutral value).

    Used in OBIFeature precomputation to track EMA zero-crossing age.
    For threshold-crossing age (more useful in scanners), use threshold_age().
    """
    sign = np.sign(series)
    changed = (sign != sign.shift(1)) | (sign == 0)
    group = changed.cumsum()
    age = group.groupby(group).cumcount().astype(np.int32)
    age[sign == 0] = 0
    return age


def threshold_age(series: pd.Series, threshold: float) -> pd.Series:
    """Steps since |series| last crossed `threshold` from below.

    Returns:
        age >= 0  when |series| > threshold  (0 = the crossing instant itself)
        -1        when |series| <= threshold  (not in an active episode)

    Parameters
    ----------
    series : pd.Series
        Signal on a regular grid (e.g. 1s-resampled EMA of OBI).
        Step count equals elapsed time in grid units.
    threshold : float
        Level to track crossings of. Applied to abs(series).

    Example
    -------
    threshold=0.3, series = [-0.1, -0.4, 0.2, 0.4, 0.5, 0.1, 0.6]
    abs > 0.3 :              [  F,    T,   F,   T,   T,   F,   T ]
    crossing  :              [  F,    T,   F,   T,   F,   F,   T ]
    age       :              [ -1,    0,  -1,   0,   1,  -1,   0 ]

    In a scanner: filter `age < max_age` to trade only fresh crossings.
    Unlike sign_age (zero-crossing), this correctly identifies the moment the
    EMA reaches a meaningful level — so age=0 AND abs(obi)>threshold always
    co-occur and produce data.
    """
    above = series.abs() > threshold
    crossed = above & ~above.shift(1, fill_value=False)
    group = crossed.cumsum()
    age = group.groupby(group).cumcount().astype(np.int32)
    age[~above] = -1
    return age
