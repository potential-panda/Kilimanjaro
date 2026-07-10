"""Fixed-horizon return: measure mid-price return at exactly ts + H seconds."""

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
class FixedHorizonReturn:
    """Compute return from signal time to signal time + horizon_seconds.

    entry: first MarketPrice mid at or after signal ts
    exit:  first MarketPrice mid at or after signal ts + horizon_seconds
    return_bps = direction × (exit_mid - entry_mid) / entry_mid × 10000
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
        """Join signals with target MarketPrice and compute fixed-horizon returns.

        Input signals must have [ts, direction, strength].
        Returns [ts, direction, strength, return_bps, entry_mid, exit_mid,
                 jst_hour, jst_weekday, jst_date].
        """
        price_glob = mp_glob(target.exchange, target.symbol, target.execution_size, data_root)
        con = duckdb.connect()
        con.register("_signals", signals)

        h = self.horizon_seconds
        trades = con.sql(f"""
            WITH mp AS (
                SELECT ts, (bid + ask) / 2.0 AS mid
                FROM read_parquet('{price_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ),
            with_entry AS (
                SELECT s.ts, s.direction, s.strength,
                       e.ts  AS entry_exec_ts,
                       e.mid AS entry_mid
                FROM _signals s ASOF JOIN mp e ON e.ts >= s.ts
            ),
            with_exit AS (
                SELECT e.ts, e.direction, e.strength,
                       e.entry_exec_ts, e.entry_mid,
                       x.mid AS exit_mid
                FROM with_entry e ASOF LEFT JOIN mp x ON x.ts >= e.ts + {h}
            )
            SELECT ts, direction, strength, entry_mid, exit_mid,
                   direction * (exit_mid - entry_mid) / entry_mid * 10000.0 AS return_bps
            FROM with_exit
            ORDER BY ts
        """).df()

        return add_jst_columns(trades, ts_col="ts")
