"""Calibration factor: composite_z — the final IC-weighted composite z.

offline (Kilimanjaro): eval the full composite expression (from the promoted spec)
                       over the Makalu feature frame.
online  (Himalaya):    InfluxDB measurement TRADING, field "composite_z" (the value
                       the aquila strategy fires on).
This is the end-to-end signal: it matches only once every input factor does.
"""

from pathlib import Path

import yaml

from kilimanjaro.calibration.base import FactorCalib

_SPEC = yaml.safe_load(
    (
        Path(__file__).resolve().parents[4]
        / "cycle"
        / "18_handoffs"
        / "blend-btc-jpy-us-z35"
        / "composite_coincheck.yaml"
    ).read_text()
)

FACTOR = FactorCalib(
    name="composite_z",
    offline_expr=_SPEC["expressions"][0]["expr"],
    status="calibrated",
    note="[2026-03-19..21 pct_match 99.79%, corr 1.00000, max z-diff 0.062] full composite; end-to-end signal the strategy fires on. Remaining <1% is warmup/edge-second zscore noise (all inputs >=99.3%).",
)
