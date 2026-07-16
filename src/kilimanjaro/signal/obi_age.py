"""OBI + threshold-age signal definition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.obi_loader import load_obi_series
from kilimanjaro.op.age import threshold_age, threshold_episode

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


@dataclass(frozen=True)
class OBIAgeSignal:
    """Signal that fires when |OBI EMA| > threshold and age is within a window.

    cache_key = (signal_exchange, signal_symbol, depth, halflife)
    All combos sharing the same key reuse one parquet load.

    Parameters
    ----------
    signal_exchange : str   Exchange for OBIFeature, e.g. "BINANCE".
    signal_symbol   : str   Symbol for OBIFeature, e.g. "ETH_USDT".
    depth           : int   OBI depth: 1, 3, 5, or 10.
    halflife        : int | None  EMA halflife in seconds, or None for raw OBI.
    threshold       : float Minimum |OBI| to qualify as a signal.
    min_age         : int | None  Min steps since threshold crossing (skip noisy starts).
    max_age         : int | None  Max steps since threshold crossing (skip exhausted tail).
    """

    signal_exchange: str
    signal_symbol: str
    depth: int
    halflife: int | None
    threshold: float
    min_age: int | None = None
    max_age: int | None = None

    @property
    def params(self) -> dict:
        return {
            "depth": self.depth,
            "halflife": self.halflife,
            "threshold": self.threshold,
            "min_age": self.min_age,
            "max_age": self.max_age,
        }

    @property
    def cache_key(self) -> tuple:
        return (self.signal_exchange, self.signal_symbol, self.depth, self.halflife)

    def load_data(
        self, start: str, end: str, data_root: Path = _DEFAULT_DATA_ROOT
    ) -> pd.DataFrame:
        """Load [ts, obi] series for this (depth, halflife). Shared across combos."""
        return load_obi_series(
            exchange=self.signal_exchange,
            symbol=self.signal_symbol,
            start_date=start,
            end_date=end,
            depth=self.depth,
            obi_halflife=self.halflife,
            data_root=data_root,
        )

    def generate(self, data: pd.DataFrame) -> pd.DataFrame:
        """Filter OBI series to active signal rows.

        Returns DataFrame with [ts, direction, strength, episode]:
            ts        signal timestamp (seconds)
            direction +1 (bid pressure) or -1 (ask pressure)
            strength  raw OBI value (used for IC calculation)
            episode   maximal-run id (for EpisodeReturn; -1 filtered out here)
        """
        df = data.copy()
        needs_age = self.min_age is not None or self.max_age is not None
        # Episode id from the full |obi|>threshold runs (before age gating),
        # so it labels the true episode even when min_age/max_age drop rows.
        df["episode"] = threshold_episode(df["obi"], self.threshold)

        if needs_age:
            df["_age"] = threshold_age(df["obi"], self.threshold)
            mask = df["obi"].abs() > self.threshold
            if self.min_age is not None:
                mask &= df["_age"] >= self.min_age
            if self.max_age is not None:
                mask &= df["_age"] < self.max_age
        else:
            mask = df["obi"].abs() > self.threshold

        active = df[mask].copy()
        active["direction"] = np.sign(active["obi"])
        active["strength"] = active["obi"]
        return active[["ts", "direction", "strength", "episode"]].reset_index(drop=True)
