"""Calibration factor: book_shape.

offline (Kilimanjaro): eval `zscore(a_obi_k1 - a_obi_k5, 1800)` over the Makalu feature frame.
online  (Enoshima):    InfluxDB measurement TRADING, field "book_shape".
note: OBI depth gradient k1-k5
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="book_shape",
    offline_expr="zscore(a_obi_k1 - a_obi_k5, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 100.00%] OBI depth gradient k1-k5",
)
