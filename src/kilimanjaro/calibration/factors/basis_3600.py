"""Calibration factor: basis_3600.

offline (Kilimanjaro): eval `zscore(a_mid / b_mid, 3600)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "basis_3600".
note: as basis_900, longer window
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="basis_3600",
    offline_expr="zscore(a_mid / b_mid, 3600)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 100.0%] as basis_900, longer window",
)
