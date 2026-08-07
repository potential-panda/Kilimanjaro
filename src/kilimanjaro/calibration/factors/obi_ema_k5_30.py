"""Calibration factor: obi_ema_k5_30.

offline (Kilimanjaro): eval `zscore(a_obi_ema_k5_30s, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "obi_ema_k5_30".
note: OBI EMA k5 hl30
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="obi_ema_k5_30",
    offline_expr="zscore(a_obi_ema_k5_30s, 1800)",
    status="pending",
    note="OBI EMA k5 hl30",
)
