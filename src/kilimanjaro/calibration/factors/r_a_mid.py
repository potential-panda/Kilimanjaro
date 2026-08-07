"""Calibration factor (RAW input): r_a_mid.

offline: `a_mid` over the Makalu feature frame. online: InfluxDB TRADING field "r_a_mid".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW BINANCE walked mid
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(name="r_a_mid", offline_expr="a_mid", status="calibrated", note="RAW BINANCE walked mid")
