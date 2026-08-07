"""Calibration factor (RAW input): r_vol_imb_k1.

offline: `a_obi_k1` over the Makalu feature frame. online: InfluxDB TRADING field "r_vol_imb_k1".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW volume imbalance k1 (input to book_shape / obi_ema)
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(name="r_vol_imb_k1", offline_expr="a_obi_k1", status="calibrated", note="RAW volume imbalance k1 (input to book_shape / obi_ema)")
