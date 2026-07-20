"""Calibration registry: which mechanisms the scan is trusted on, machine-readable.

A *mechanism* = entry style + return calc + execution model (doc/calibration.md).
Scan↔Himalaya trust is earned once per mechanism and inherited by every formula
on it. This module turns that status into data so Stage-A can refuse to present a
survivor on a *trend-only* mechanism as promotion-ready — the human gate is told,
in the review packet and the ledger, when the scan numbers are not yet
Himalaya-verified.

Ground-truth note (2026-07): Himalaya only backtests SAR-flip strategies (every
orion variant is parabolic-SAR). So expression mechanisms cannot be directly
Himalaya-calibrated until a signal-driven strategy exists in orion (Stage 5,
human-gated). Their execution model (bid/ask first-tick fills + flat latency cost
+ orion schedule) is *inherited* from the flip calibration; what remains unverified
is entry/exit-timing fidelity. Until then they are trend-only.
"""

from __future__ import annotations

from dataclasses import dataclass

CALIBRATED = "calibrated"
TREND_ONLY = "trend-only"


@dataclass(frozen=True)
class Calibration:
    mechanism: str
    status: str  # CALIBRATED | TREND_ONLY
    evidence: str

    @property
    def is_calibrated(self) -> bool:
        return self.status == CALIBRATED


_REGISTRY: dict[str, Calibration] = {
    "flip+between_signal": Calibration(
        "flip+between_signal",
        CALIBRATED,
        "ETH May 2026 (0¥ entry, 0.000s fill, 98.3% PnL match) and BTC "
        "2025-07..2026-06 (scan↔Himalaya matched to ~0.7% on trade count and "
        "~0.1–0.5 on Calmar) after the orion-schedule + bid/ask-cost fixes.",
    ),
    "expression+fixed_horizon": Calibration(
        "expression+fixed_horizon",
        TREND_ONLY,
        "No orion counterpart strategy to Himalaya-check (all orion variants are "
        "SAR-flip). Execution model inherited from the flip calibration; "
        "entry/exit-timing fidelity unverified. Blocked on a Stage-5 signal strategy.",
    ),
    "expression+episode": Calibration(
        "expression+episode",
        TREND_ONLY,
        "Same as fixed_horizon: no orion counterpart; execution inherited, "
        "entry/exit-timing unverified. Trend-only until a Stage-5 strategy exists.",
    ),
}

# Unknown mechanisms are trend-only by default — trust must be earned explicitly.
_UNKNOWN = Calibration("unknown", TREND_ONLY, "Mechanism not in the calibration registry.")


def mechanism_name(signal_family: str, return_calc: str) -> str:
    """Canonical mechanism key, e.g. ("expression", "episode") -> 'expression+episode'."""
    return f"{signal_family}+{return_calc}"


def spec_mechanism(spec: dict) -> str:
    """Derive the mechanism key from a scan spec.

    Expression specs carry `return_calc` (default fixed_horizon); their signal
    family is 'expression'. The flip grid is 'flip+between_signal'.
    """
    if spec.get("signal_family") == "flip" or "expressions" not in spec:
        return "flip+between_signal"
    return mechanism_name("expression", spec.get("return_calc", "fixed_horizon"))


def lookup(mechanism: str) -> Calibration:
    return _REGISTRY.get(mechanism, _UNKNOWN)


def is_calibrated(mechanism: str) -> bool:
    return lookup(mechanism).is_calibrated
