"""Fixed-horizon return: tradeable bid/ask return over a fixed hold of H seconds.

Per the Kilimanjaro cost policy (framework/costs.py): execution is at the
**actual bid/ask**, never mid — BUY enters at ask / exits at bid, SELL the
reverse — so the spread is already paid inside the return. Only the flat
round-trip latency slippage is charged on top, upstream (costs_bps).

Prices come from the shared per-window target-price cache — one parquet load
per (market, window), then a searchsorted per combo, instead of a full
DuckDB scan+ASOF join per combo (which dominated grid runtime).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.price_cache import load_target_prices
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.framework.schedule import add_jst_columns

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


@dataclass(frozen=True)
class FixedHorizonReturn:
    """Compute return from signal time to signal time + horizon_seconds.

    Bid/ask execution (spread-inclusive, per the cost policy):
      entry: ask (BUY) / bid (SELL) at first tick at or after signal ts
      exit:  bid (BUY) / ask (SELL) at first tick at or after ts + horizon
      return_bps = direction × (exit_price - entry_price) / entry_price × 10000
    """

    horizon_seconds: int

    @property
    def params(self) -> dict:
        return {"horizon": self.horizon_seconds}

    def compute(
        self,
        signals: pd.DataFrame,
        target: MarketConfig,
        start_date: str,
        end_date: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
    ) -> pd.DataFrame:
        """Join signals with cached target prices and compute fixed-horizon returns.

        Input signals must have [ts, direction, strength].
        Returns [ts, direction, strength, entry_price, exit_price, return_bps,
                 jst_hour, jst_weekday, jst_date].
        """
        mp = load_target_prices(
            target.exchange,
            target.symbol,
            target.execution_size,
            start_date,
            end_date,
            data_root,
        )
        sig = signals.sort_values("ts", kind="stable").reset_index(drop=True)
        direction = sig["direction"].values
        n_mp = len(mp.ts)

        # Bid/ask execution: enter aggressive (ask BUY / bid SELL), exit passive.
        entry_idx = mp.first_at_or_after(sig["ts"].values)
        has_entry = entry_idx < n_mp
        e = np.clip(entry_idx, 0, max(n_mp - 1, 0))
        entry_price = np.where(
            has_entry, np.where(direction > 0, mp.ask[e], mp.bid[e]), np.nan
        )

        exit_idx = mp.first_at_or_after(sig["ts"].values + self.horizon_seconds)
        has_exit = exit_idx < n_mp
        x = np.clip(exit_idx, 0, max(n_mp - 1, 0))
        exit_price = np.where(
            has_exit, np.where(direction > 0, mp.bid[x], mp.ask[x]), np.nan
        )

        trades = pd.DataFrame(
            {
                "ts": sig["ts"].values,
                "direction": direction,
                "strength": sig["strength"].values,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "return_bps": direction
                * (exit_price - entry_price)
                / entry_price
                * 10000.0,
            }
        )
        # Carry the episode id through so stats can report n_episodes — the
        # count of *independent* events behind these overlapping observations
        # (doc/overlapping-returns.md). Per-second sampling means n is not it.
        if "episode" in sig.columns:
            trades["episode"] = sig["episode"].values

        # entry join was INNER; exit stays LEFT (NaN return, dropped at stats)
        trades = trades[has_entry].reset_index(drop=True)

        return add_jst_columns(trades, ts_col="ts")
