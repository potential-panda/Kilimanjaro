"""Calibration factor: aggcount_10.

offline (Kilimanjaro): eval `zscore(a_ta_imbalance_count_10s, 1800)` over the Makalu feature frame.
online  (Enoshima):    InfluxDB measurement TRADING, field "aggcount_10".
note: aggressor count imbalance 10s
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="aggcount_10",
    offline_expr="zscore(a_ta_imbalance_count_10s, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 99.7%] aggressor count imbalance 10s",
)
