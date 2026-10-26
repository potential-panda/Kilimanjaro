"""SAR flip signal definition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from kilimanjaro.data.root import data_root as _resolve_data_root
from kilimanjaro.data.sar_loader import load_sar_flips

_DEFAULT_DATA_ROOT = None


@dataclass(frozen=True)
class SARFlipSignal:
    """Signal that fires at every Parabolic SAR flip event.

    cache_key = (flip_exchange, flip_symbol, box_size_rate, sar_step)
    — each (box, step) combination reads a different parquet partition,
    so there is no data sharing between combos with different params.

    The generated signals include exit_ts (LEAD of the next flip), which
    BetweenSignalReturn uses to determine the holding period. This means
    the exit is always driven by ALL flips, even after schedule filtering
    removes some entries — matching the backtest's NO_ENTRY close behaviour.

    Parameters
    ----------
    flip_exchange   : str   Exchange for ChartRangeSAR, e.g. "BINANCE".
    flip_symbol     : str   Symbol for ChartRangeSAR, e.g. "ETH_USDT".
    box_size_rate   : float Range bar size as fraction of price.
    sar_step        : float Parabolic SAR acceleration step.
    """

    flip_exchange: str
    flip_symbol: str
    box_size_rate: float
    sar_step: float

    @property
    def params(self) -> dict:
        return {"box": self.box_size_rate, "step": self.sar_step}

    @property
    def cache_key(self) -> tuple:
        return (self.flip_exchange, self.flip_symbol, self.box_size_rate, self.sar_step)

    def load_data(
        self, start: str, end: str, data_root: Path | None = _DEFAULT_DATA_ROOT
    ) -> pd.DataFrame:
        """Load all flip events with LEAD-derived exit_ts."""
        data_root = _resolve_data_root(data_root)
        return load_sar_flips(
            exchange=self.flip_exchange,
            symbol=self.flip_symbol,
            start_date=start,
            end_date=end,
            box_size_rate=self.box_size_rate,
            sar_step=self.sar_step,
            features_root=data_root / "feature",
        )

    def generate(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return all flip events as signals.

        Returns DataFrame with [ts, direction, strength, exit_ts]:
            ts        signal timestamp
            direction sar_up (+1 or -1)
            strength  same as direction (binary signal)
            exit_ts   next flip timestamp (NaN for last flip)
        """
        df = data[["ts", "sar_up", "exit_ts"]].copy()
        df["direction"] = df["sar_up"].astype(float)
        df["strength"] = df["sar_up"].astype(float)
        return df[["ts", "direction", "strength", "exit_ts"]].reset_index(drop=True)
