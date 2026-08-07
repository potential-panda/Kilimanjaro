"""Calibration factor: basis_900.

offline (Kilimanjaro): eval `zscore(a_mid / b_mid, 900)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "basis_900".
note: BINANCE/COINCHECK basis; needs the 1s-ts tie-break (file_row_number) for b_mid
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="basis_900",
    offline_expr="zscore(a_mid / b_mid, 900)",
    status="pending",
    note="BINANCE/COINCHECK basis; needs the 1s-ts tie-break (file_row_number) for b_mid",
)
