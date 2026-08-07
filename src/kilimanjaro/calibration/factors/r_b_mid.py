"""Calibration factor (RAW input): r_b_mid.

offline: `b_mid` over the Makalu feature frame. online: InfluxDB TRADING field "r_b_mid".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW COINCHECK walked mid
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(name="r_b_mid", offline_expr="b_mid", status="pending", note="RAW COINCHECK walked mid")
