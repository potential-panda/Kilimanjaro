"""Calibration factor (RAW input): r_weighted_imb_k5.

offline: `a_bs_weighted_imb_k5` over the Makalu feature frame. online: InfluxDB TRADING field "r_weighted_imb_k5".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW weighted_imb k5 (input to wimb_k5)
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="r_weighted_imb_k5",
    offline_expr="a_bs_weighted_imb_k5",
    status="calibrated",
    note="RAW weighted_imb k5 (input to wimb_k5)",
)
