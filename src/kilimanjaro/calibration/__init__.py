"""Kilimanjaro↔Enoshima factor calibration.

Exact-match a promoted signal's factors between the offline scan (Makalu parquet) and
the online strategy (qate/Enoshima, emitted to InfluxDB), factor by factor, until the
match rate is ≈100% — so the fast offline scan can be trusted. See registry.py for the
factor list and base.py/compare.py for the machinery. Driven by `cli.py calibrate`.
"""

from kilimanjaro.calibration import registry
from kilimanjaro.calibration.base import FactorCalib
from kilimanjaro.calibration.compare import CalibResult, compare

__all__ = ["CalibResult", "FactorCalib", "compare", "registry"]
