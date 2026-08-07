"""Calibration factor: wimb_k5.

offline (Kilimanjaro): eval `zscore(a_bs_weighted_imb_k5, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "wimb_k5".
note: BookState weighted imbalance k5 (top weight)
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="wimb_k5",
    offline_expr="zscore(a_bs_weighted_imb_k5, 1800)",
    status="pending",
    note="[2026-03-19..21 pct_match 70.2%] BookState weighted imbalance k5 (top weight)",
)
