"""Calibration factor: net_flow_10.

offline (Kilimanjaro): eval `zscore(a_ta_net_flow_10s, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "net_flow_10".
note: trade net flow 10s
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="net_flow_10",
    offline_expr="zscore(a_ta_net_flow_10s, 1800)",
    status="pending",
    note="trade net flow 10s",
)
