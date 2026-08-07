"""Calibration factor: vwapdev_10.

offline (Kilimanjaro): eval `zscore(a_mid / a_ta_vwap_10s - 1, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "vwapdev_10".
note: vwap deviation 10s
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="vwapdev_10",
    offline_expr="zscore(a_mid / a_ta_vwap_10s - 1, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 100.0%] vwap deviation 10s",
)
