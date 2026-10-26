"""Structural protocols for the scan framework.

SignalDef  — knows how to load and filter raw data into signal events.
ReturnCalc — knows how to join signal events with execution prices and compute returns.

These are typing.Protocol, so no import is required to satisfy them; any class with
the right methods and properties is automatically compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class MarketConfig:
    """Execution market used by ReturnCalc implementations."""

    exchange: str
    symbol: str
    execution_size: str


class SignalDef(Protocol):
    """Defines one concrete signal parameterisation.

    Lifecycle called by run_grid():
        data = sig.load_data(start, end, data_root)  # once per cache_key group
        signals = sig.generate(data)                  # once per signal_def

    signals DataFrame must have columns: [ts, direction, strength]
        ts         float64  Unix timestamp of the signal event
        direction  float64  +1 (long) or -1 (short)
        strength   float64  raw signal value
    It may also carry extra columns (e.g. exit_ts for between-signal returns).
    """

    @property
    def params(self) -> dict:
        """Parameter dict written verbatim into the output row."""
        ...

    @property
    def cache_key(self) -> tuple:
        """Signals with the same cache_key share one load_data() call.

        Example: an OBI ExpressionSignal keys on (markets, columns) — all threshold/age
        combos for that pair reuse the same loaded OBI series.
        """
        ...

    def load_data(self, start: str, end: str, data_root: Path) -> Any:
        """Load raw data covering start..end. Called once per cache_key group."""
        ...

    def generate(self, data: Any) -> pd.DataFrame:
        """Transform loaded data into a signal events DataFrame."""
        ...


class ReturnCalc(Protocol):
    """Computes per-signal returns by joining signal events with execution prices.

    Returns a DataFrame with at minimum:
        ts          float64  signal timestamp
        direction   float64  +1 / -1
        strength    float64  raw signal value (for IC)
        return_bps  float64  signed return in basis points (NaN if no valid exit)
        jst_hour    int      JST hour of the relevant timestamp (for schedule filter)
        jst_weekday int      day-of-week 0=Mon..6=Sun (for schedule filter)
        jst_date    str      YYYY-MM-DD in JST (for date blocking)

    May also include holding_seconds float64 (for between-signal style).
    """

    @property
    def params(self) -> dict:
        """Return-calc parameters (e.g. {"horizon": 60}) merged into output row."""
        ...

    def compute(
        self,
        signals: pd.DataFrame,
        target: MarketConfig,
        start_date: str,
        end_date: str,
        data_root: Path,
    ) -> pd.DataFrame: ...
