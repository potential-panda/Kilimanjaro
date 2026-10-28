"""Calibration factor: vwapdev_60.

offline (Kilimanjaro): eval `zscore(a_mid / a_ta_vwap_60s - 1, 1800)` over the Makalu feature frame.
online  (Enoshima):    InfluxDB measurement TRADING, field "vwapdev_60".
note: vwap deviation 60s
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="vwapdev_60",
    offline_expr="zscore(a_mid / a_ta_vwap_60s - 1, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 100.0%] vwap deviation 60s",
)
