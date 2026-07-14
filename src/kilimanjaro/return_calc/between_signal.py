"""Between-signal return: hold from entry flip until the next flip (any direction).

This is the "skew" style used by the SAR flip scan — enter at the flip price,
exit when the next SAR cross fires. Exit is always driven by ALL flip events,
even if the schedule filter would have blocked that flip as an entry. This
matches the backtest's NO_ENTRY close behaviour.

BUY (direction=+1): enter at ask, exit at bid.
SELL (direction=-1): enter at bid, exit at ask.

Prices come from the shared per-window target-price cache
(kilimanjaro.data.price_cache) and entries/exits are resolved with
searchsorted — same "first MarketPrice tick at or after ts" semantics as the
DuckDB ASOF join this replaced (verified identical against the recorded
grid numbers), but ~free per combo instead of a parquet scan per combo.
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
class BetweenSignalReturn:
    """Compute return from entry flip to the next SAR flip (all flips, pre-filter).

    entry_price: ask (BUY) or bid (SELL) at first MarketPrice tick after entry ts
    exit_price:  bid (BUY) or ask (SELL) at first MarketPrice tick after exit_ts
    return_bps = direction × (exit_price - entry_price) / entry_price × 10000
    holding_seconds = exit_exec_ts - entry_exec_ts

    Signals must include exit_ts (from SARFlipSignal.generate()).
    jst_hour and jst_weekday are derived from exit_ts (backtest decision point);
    jst_date is derived from ts (entry date, for date blocking).
    """

    @property
    def params(self) -> dict:
        return {}

    def compute(
        self,
        signals: pd.DataFrame,
        target: MarketConfig,
        start_date: str,
        end_date: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
    ) -> pd.DataFrame:
        """Join signals with cached target prices and compute between-signal returns.

        Input signals must have [ts, direction, strength, exit_ts].
        Returns [ts, direction, strength, exit_ts, entry_exec_ts, entry_price,
                 exit_exec_ts, exit_price, return_bps, holding_seconds,
                 jst_hour, jst_weekday, jst_date].
        """
        mp = load_target_prices(
            target.exchange, target.symbol, target.execution_size,
            start_date, end_date, data_root,
        )
        sig = signals.sort_values("ts", kind="stable").reset_index(drop=True)
        direction = sig["direction"].values
        n_mp = len(mp.ts)

        entry_idx = mp.first_at_or_after(sig["ts"].values)
        has_entry = entry_idx < n_mp
        e = np.clip(entry_idx, 0, max(n_mp - 1, 0))
        entry_exec_ts = np.where(has_entry, mp.ts[e], np.nan)
        entry_price = np.where(
            has_entry, np.where(direction > 0, mp.ask[e], mp.bid[e]), np.nan
        )

        exit_idx = mp.first_at_or_after(sig["exit_ts"].values)
        has_exit = exit_idx < n_mp
        x = np.clip(exit_idx, 0, max(n_mp - 1, 0))
        exit_exec_ts = np.where(has_exit, mp.ts[x], np.nan)
        exit_price = np.where(
            has_exit, np.where(direction > 0, mp.bid[x], mp.ask[x]), np.nan
        )

        trades = pd.DataFrame({
            "ts": sig["ts"].values,
            "direction": direction,
            "strength": sig["strength"].values,
            "exit_ts": sig["exit_ts"].values,
            "entry_exec_ts": entry_exec_ts,
            "entry_price": entry_price,
            "exit_exec_ts": exit_exec_ts,
            "exit_price": exit_price,
            "return_bps": direction * (exit_price - entry_price) / entry_price * 10000.0,
            "holding_seconds": exit_exec_ts - entry_exec_ts,
        })
        # ASOF entry join was INNER: signals with no tick at/after ts drop out
        trades = trades[has_entry].reset_index(drop=True)

        # jst_hour/weekday from exit_ts (schedule decision point);
        # jst_date from entry ts (date blocking).
        return add_jst_columns(trades, ts_col="exit_exec_ts", date_ts_col="ts")
