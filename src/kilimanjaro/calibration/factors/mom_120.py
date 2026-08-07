"""Calibration factor: mom_120.

offline (Kilimanjaro): eval `zscore(ret(a_mid, 120), 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "mom_120".
note: 120s BINANCE momentum
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="mom_120",
    offline_expr="zscore(ret(a_mid, 120), 1800)",
    status="pending",
    note="120s BINANCE momentum",
)
