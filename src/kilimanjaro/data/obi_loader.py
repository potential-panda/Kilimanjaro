"""Load OBIFeature 1s-resampled EMA series from parquet."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

_VALID_DEPTHS = frozenset([1, 3, 5, 10])
_VALID_HALFLIVES = frozenset([10, 30, 60, 120, 300])


def _obi_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "feature" / "dataset=OBIFeature"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / "date=*" / "*.parquet"
    )


def _signal_col(depth: int, halflife: int | None) -> str:
    return f"vol_imb_k{depth}" if halflife is None else f"ema_k{depth}_{halflife}s"


def load_obi_series(
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    depth: int,
    obi_halflife: int | None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Load the full 1s OBI series for one (depth, halflife) over a date range.

    Returns a DataFrame with columns [ts, obi] — no threshold filtering applied.
    Pass the result to OBIAgeSignal.generate() or directly to scan() to avoid
    re-reading parquet on every threshold/horizon combo in a grid sweep.
    """
    if depth not in _VALID_DEPTHS:
        raise ValueError(f"depth must be one of {sorted(_VALID_DEPTHS)}, got {depth}")
    if obi_halflife is not None and obi_halflife not in _VALID_HALFLIVES:
        raise ValueError(
            f"obi_halflife must be one of {sorted(_VALID_HALFLIVES)} or None, got {obi_halflife}"
        )
    col = _signal_col(depth, obi_halflife)
    glob = _obi_glob(exchange, symbol, data_root)
    # Hold the connection in a local until .df() materializes — an inline
    # duckdb.connect().sql(...).df() lets the connection be GC'd mid-query
    # ("Connection has already been closed"), same bug fixed in sar_loader.
    con = duckdb.connect()
    try:
        return con.sql(f"""
            SELECT ts, {col} AS obi
            FROM read_parquet('{glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ORDER BY ts
        """).df()
    finally:
        con.close()
