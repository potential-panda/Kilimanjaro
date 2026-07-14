"""Target-market price cache: load once per (market, window), join in memory.

The return calcs used to re-read the target MarketPrice parquet and redo a
DuckDB ASOF join for EVERY combo — a 245-combo train/holdout grid scanned
the same year of prices 490 times, and that IO dominated grid runtime. This
module loads the (ts, bid, ask) arrays once per (market, window) behind a
small LRU cache; the return calcs then resolve entries/exits with
np.searchsorted, which preserves the exact semantics the backtest
verification certified (first MarketPrice tick at or after the signal ts —
see CLAUDE.md "Verification status").

Memory: a year of GMO MarketPrice is ~64M rows ≈ 1.5 GB as three float64
arrays. Cache holds at most 4 windows (train + holdout for two markets).
Using a BINANCE symbol as the *target* would be ~5x larger — fine for the
short windows expression scans use, but don't cache a year of it casually.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import duckdb
import numpy as np

from kilimanjaro.data.price_loader import mp_glob

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


class TargetPrices(NamedTuple):
    ts: np.ndarray    # float64, sorted ascending
    bid: np.ndarray   # float64
    ask: np.ndarray   # float64

    @property
    def mid(self) -> np.ndarray:
        return (self.bid + self.ask) / 2.0

    def first_at_or_after(self, when: np.ndarray) -> np.ndarray:
        """Index of the first tick with ts >= when; len(ts) when none exists.

        Matches DuckDB `ASOF JOIN mp o ON o.ts >= s.ts` (and the backtest's
        fill selection): the earliest tick at or after the signal moment.
        NaN `when` values map to len(ts) (= no match)."""
        when = np.asarray(when, dtype=np.float64)
        idx = np.searchsorted(self.ts, np.where(np.isnan(when), np.inf, when), side="left")
        return idx


@lru_cache(maxsize=4)
def _load(exchange: str, symbol: str, execution_size: str,
          start_date: str, end_date: str, data_root: str) -> TargetPrices:
    glob = mp_glob(exchange, symbol, execution_size, Path(data_root))
    con = duckdb.connect()
    try:
        df = con.sql(f"""
            SELECT ts, bid, ask
            FROM read_parquet('{glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ORDER BY ts
        """).df()
    finally:
        con.close()
    return TargetPrices(
        ts=np.ascontiguousarray(df["ts"].values, dtype=np.float64),
        bid=np.ascontiguousarray(df["bid"].values, dtype=np.float64),
        ask=np.ascontiguousarray(df["ask"].values, dtype=np.float64),
    )


def load_target_prices(
    exchange: str,
    symbol: str,
    execution_size: str,
    start_date: str,
    end_date: str,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> TargetPrices:
    """Cached (ts, bid, ask) arrays for one market over one date window."""
    return _load(exchange, symbol, execution_size, start_date, end_date, str(data_root))
