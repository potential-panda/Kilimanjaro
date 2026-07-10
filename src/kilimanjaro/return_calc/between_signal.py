"""Between-signal return: hold from entry flip until the next flip (any direction).

This is the "skew" style used by the SAR flip scan — enter at the flip price,
exit when the next SAR cross fires. Exit is always driven by ALL flip events,
even if the schedule filter would have blocked that flip as an entry. This
matches the backtest's NO_ENTRY close behaviour.

BUY (direction=+1): enter at ask, exit at bid.
SELL (direction=-1): enter at bid, exit at ask.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from kilimanjaro.data.price_loader import mp_glob
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
        """Join signals with target MarketPrice and compute between-signal returns.

        Input signals must have [ts, direction, strength, exit_ts].
        Returns [ts, direction, strength, exit_ts, return_bps, holding_seconds,
                 entry_price, exit_price, jst_hour, jst_weekday, jst_date].
        """
        price_glob = mp_glob(target.exchange, target.symbol, target.execution_size, data_root)
        con = duckdb.connect()
        con.register("_signals", signals)

        trades = con.sql(f"""
            WITH mp AS (
                SELECT ts, bid, ask
                FROM read_parquet('{price_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ),
            with_entry AS (
                SELECT s.ts, s.direction, s.strength, s.exit_ts,
                       o.ts  AS entry_exec_ts,
                       CASE WHEN s.direction > 0 THEN o.ask ELSE o.bid END AS entry_price
                FROM _signals s ASOF JOIN mp o ON o.ts >= s.ts
            ),
            with_exit AS (
                SELECT e.ts, e.direction, e.strength, e.exit_ts,
                       e.entry_exec_ts, e.entry_price,
                       x.ts  AS exit_exec_ts,
                       CASE WHEN e.direction > 0 THEN x.bid ELSE x.ask END AS exit_price
                FROM with_entry e ASOF LEFT JOIN mp x ON x.ts >= e.exit_ts
            )
            SELECT ts, direction, strength, exit_ts,
                   entry_exec_ts, entry_price, exit_exec_ts, exit_price,
                   direction * (exit_price - entry_price) / entry_price * 10000.0 AS return_bps,
                   exit_exec_ts - entry_exec_ts AS holding_seconds
            FROM with_exit
            WHERE entry_price IS NOT NULL
            ORDER BY ts
        """).df()

        # jst_hour/weekday from exit_ts (schedule decision point);
        # jst_date from entry ts (date blocking).
        trades = add_jst_columns(
            trades,
            ts_col="exit_exec_ts",
            date_ts_col="ts",
        )
        return trades
