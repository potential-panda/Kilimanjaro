"""Calibration factor: imb_qty_30.

offline (Kilimanjaro): eval `zscore(a_ta_imbalance_qty_30s, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "imb_qty_30".
note: trade imbalance qty 30s
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="imb_qty_30",
    offline_expr="zscore(a_ta_imbalance_qty_30s, 1800)",
    status="pending",
    note="trade imbalance qty 30s",
)
