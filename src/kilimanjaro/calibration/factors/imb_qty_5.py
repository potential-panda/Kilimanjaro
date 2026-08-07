"""Calibration factor: imb_qty_5.

offline (Kilimanjaro): eval `zscore(a_ta_imbalance_qty_5s, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "imb_qty_5".
note: trade imbalance qty 5s
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="imb_qty_5",
    offline_expr="zscore(a_ta_imbalance_qty_5s, 1800)",
    status="pending",
    note="trade imbalance qty 5s",
)
