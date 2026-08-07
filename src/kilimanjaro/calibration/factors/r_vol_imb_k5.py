"""Calibration factor (RAW input): r_vol_imb_k5.

offline: `a_obi_k5` over the Makalu feature frame. online: InfluxDB TRADING field "r_vol_imb_k5".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW volume imbalance k5 (input to book_shape)
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(name="r_vol_imb_k5", offline_expr="a_obi_k5", status="pending", note="RAW volume imbalance k5 (input to book_shape)")
