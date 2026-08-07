"""Calibration factor: mom_300.

offline (Kilimanjaro): eval `zscore(ret(a_mid, 300), 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "mom_300".
note: 300s BINANCE momentum
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="mom_300",
    offline_expr="zscore(ret(a_mid, 300), 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 100.0%] 300s BINANCE momentum",
)
