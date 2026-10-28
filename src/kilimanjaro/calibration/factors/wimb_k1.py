"""Calibration factor: wimb_k1.

offline (Kilimanjaro): eval `zscore(a_bs_weighted_imb_k1, 1800)` over the Makalu feature frame.
online  (Enoshima):    InfluxDB measurement TRADING, field "wimb_k1".
note: BookState weighted imbalance k1 (top weight)
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="wimb_k1",
    offline_expr="zscore(a_bs_weighted_imb_k1, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 100.00%] BookState weighted imbalance k1 — fixed: engine used walked guard, now BookState top-of-book filter",
)
