"""Calibration factor (RAW input): r_weighted_imb_k1.

offline: `a_bs_weighted_imb_k1` over the Makalu feature frame. online: InfluxDB TRADING field "r_weighted_imb_k1".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW weighted_imb k1 (input to wimb_k1) — BookState vs qate compute_weighted_imbalance
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(name="r_weighted_imb_k1", offline_expr="a_bs_weighted_imb_k1", status="pending", note="RAW weighted_imb k1 (input to wimb_k1) — BookState vs qate compute_weighted_imbalance")
