"""Calibration factor (RAW input): r_obi_ema_k1_30s.

offline: `a_obi_ema_k1_30s` over the Makalu feature frame. online: InfluxDB TRADING field "r_obi_ema_k1_30s".
Raw inputs isolate whether a factor mismatch is in the book/trade computation or the zscore.
note: RAW OBI-EMA k1 hl30 (input to obi_ema_k1_30) — the halflife EMA itself
"""

from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="r_obi_ema_k1_30s",
    offline_expr="a_obi_ema_k1_30s",
    status="calibrated",
    note="RAW OBI-EMA k1 hl30 — 99.90% (fixed: per-second ewm, not per-book-event)",
)
