"""Kilimanjaro↔Himalaya factor calibration.

Exact-match a promoted signal's factors between the offline scan (Makalu parquet) and
the online strategy (qate/Himalaya, emitted to InfluxDB), factor by factor, until the
match rate is ≈100% — so the fast offline scan can be trusted. See registry.py for the
factor list and base.py/compare.py for the machinery. Driven by `cli.py calibrate`.
"""
from kilimanjaro.calibration.base import FactorCalib
from kilimanjaro.calibration.compare import CalibResult, compare
from kilimanjaro.calibration import registry

__all__ = ["FactorCalib", "CalibResult", "compare", "registry"]
