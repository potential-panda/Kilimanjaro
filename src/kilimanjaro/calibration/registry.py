"""THE calibration registry — the human-facing list of every factor of the promoted
COINCHECK composite, in composite order, plus how to load each one's FactorCalib.

This is the single place to see what's being calibrated and its status, and the source
`cli.py calibrate` iterates over. Each factor's recipe lives in its own module under
calibration/factors/<name>.py (offline expr, online field, status, note).

Sources:
  offline (Kilimanjaro): Makalu parquet feature frame, markets = MARKETS below.
  online  (Enoshima):    InfluxDB measurement TRADING (emitted by aquila add_metric),
                         bucket = the backtest bucket (DEFAULT_BUCKET or --bucket).

Calibration loop for a coding agent: for each factor, run its match; while pct_match is
below target, inspect the mismatches and fix the online engine (aquila) or the offline
recipe; repeat until ≈100%. Flip status to "calibrated" in the factor module when it holds.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]  # repo root
_SPEC = yaml.safe_load(
    (
        _ROOT
        / "cycle"
        / "18_handoffs"
        / "blend-btc-jpy-us-z35"
        / "composite_coincheck.yaml"
    ).read_text()
)

# Offline market aliases the factor expressions reference (a=leader, b=basis leg).
MARKETS = _SPEC["markets"]

# Default InfluxDB bucket holding the emitted TRADING factor series. The factors are
# strategy-level (identical across param combos), so any combo's bucket works; override
# with --bucket. Run a backtest with the factor-emitting aquila to populate it.
DEFAULT_BUCKET = "backtest_AQUILA_COINCHECK_BTC_SPOT_0.002__3.5_10.0_300"

# In composite order (see composite_coincheck.yaml). One module per name in factors/.
FACTOR_NAMES = [
    "basis_900",
    "basis_3600",
    "mom_30",
    "mom_120",
    "mom_300",
    "obi_ema_k1_30",
    "obi_ema_k1_120",
    "obi_ema_k5_30",
    "imb_qty_5",
    "imb_qty_30",
    "net_flow_10",
    "vwapdev_10",
    "vwapdev_60",
    "aggcount_10",
    "aggcount_60",
    "large_trade",
    "book_shape",
    "wimb_k1",
    "wimb_k5",
    "composite_z",  # the end-to-end signal
    # RAW inputs (r_ prefix) — diagnostics to localise a mismatch to the raw
    # book/trade computation vs the zscore. Emitted by aquila alongside the factors.
    "r_a_mid",
    "r_b_mid",
    "r_vol_imb_k1",
    "r_vol_imb_k5",
    "r_weighted_imb_k1",
    "r_weighted_imb_k5",
    "r_obi_ema_k1_30s",
]


def load(name: str):
    """Return the FactorCalib for `name` (from calibration/factors/<name>.py)."""
    return import_module(f"{__package__}.factors.{name}").FACTOR


def all_factors() -> list:
    return [load(n) for n in FACTOR_NAMES]


def status_table() -> list[tuple[str, str, str]]:
    """[(name, status, note)] for a human-readable overview."""
    return [(f.name, f.status, f.note) for f in all_factors()]
