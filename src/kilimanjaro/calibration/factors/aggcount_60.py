"""Calibration factor: aggcount_60.

offline (Kilimanjaro): eval `zscore(a_ta_imbalance_count_60s, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "aggcount_60".
note: aggressor count imbalance 60s
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="aggcount_60",
    offline_expr="zscore(a_ta_imbalance_count_60s, 1800)",
    status="pending",
    note="aggressor count imbalance 60s",
)
