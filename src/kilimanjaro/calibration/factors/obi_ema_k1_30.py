"""Calibration factor: obi_ema_k1_30.

offline (Kilimanjaro): eval `zscore(a_obi_ema_k1_30s, 1800)` over the Makalu feature frame.
online  (Enoshima):    InfluxDB measurement TRADING, field "obi_ema_k1_30".
note: OBI EMA k1 hl30 — engine OBIEma vs Makalu OBIFeature EMA
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="obi_ema_k1_30",
    offline_expr="zscore(a_obi_ema_k1_30s, 1800)",
    status="calibrated",
    note="[2026-03-19..21 pct_match 99.58%] OBI EMA k1 hl30 — per-second ewm (fixed: was per-event) + BookState top-of-book filter",
)
