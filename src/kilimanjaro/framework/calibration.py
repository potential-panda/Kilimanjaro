"""Calibration registry: which mechanisms the scan is trusted on, machine-readable.

A *mechanism* = entry style + return calc + execution model (doc/calibration.md).
Replay trust is earned once per mechanism and inherited by every formula on it.
This module keeps that status explicit so the loop cannot present unverified scan
numbers as promotion-ready.
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
    "expression+fixed_horizon": Calibration(
        "expression+fixed_horizon",
        TREND_ONLY,
        "Entry/exit timing has not been checked against an independent replay.",
    ),
    "expression+episode": Calibration(
        "expression+episode",
        TREND_ONLY,
        "Entry/exit timing has not been checked against an independent replay.",
    ),
}

# Unknown mechanisms are trend-only by default — trust must be earned explicitly.
_UNKNOWN = Calibration(
    "unknown", TREND_ONLY, "Mechanism not in the calibration registry."
)


def mechanism_name(signal_family: str, return_calc: str) -> str:
    """Canonical mechanism key, e.g. ("expression", "episode") -> 'expression+episode'."""
    return f"{signal_family}+{return_calc}"


def spec_mechanism(spec: dict) -> str:
    """Derive the mechanism key from a scan spec.

    Expression specs carry ``return_calc`` (default ``fixed_horizon``).
    """
    return mechanism_name("expression", spec.get("return_calc", "fixed_horizon"))


def lookup(mechanism: str) -> Calibration:
    return _REGISTRY.get(mechanism, _UNKNOWN)


def is_calibrated(mechanism: str) -> bool:
    return lookup(mechanism).is_calibrated
