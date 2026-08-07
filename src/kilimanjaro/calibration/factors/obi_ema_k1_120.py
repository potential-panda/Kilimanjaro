"""Calibration factor: obi_ema_k1_120.

offline (Kilimanjaro): eval `zscore(a_obi_ema_k1_120s, 1800)` over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "obi_ema_k1_120".
note: OBI EMA k1 hl120 — known drift (EMA best-effort)
"""
from kilimanjaro.calibration.base import FactorCalib

FACTOR = FactorCalib(
    name="obi_ema_k1_120",
    offline_expr="zscore(a_obi_ema_k1_120s, 1800)",
    status="pending",
    note="[2026-03-19..21 pct_match 0.8%] OBI EMA k1 hl120 — known drift (EMA best-effort)",
)
