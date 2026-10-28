"""Calibration factor: large_trade.

offline (Kilimanjaro): eval `sign(a_ta_net_flow_30s) * zscore(a_ta_max_trade_size_30s, 1800)` over the Makalu feature frame.
online  (Enoshima):    InfluxDB measurement TRADING, field "large_trade".
note: DIRECTIONAL: sign(net_flow)*zscore(max_trade_size)
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="large_trade",
    offline_expr="sign(a_ta_net_flow_30s) * zscore(a_ta_max_trade_size_30s, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 99.7%] DIRECTIONAL: sign(net_flow)*zscore(max_trade_size)",
)
