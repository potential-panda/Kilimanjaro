"""Load SAR flip events from ChartRangeSAR parquet."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def _features_root() -> Path:
    import qate.env.sys_env as sys_env
    from qate.store.parquet_store import Layer, ParquetStore
    return ParquetStore(sys_env.PARQUET_ROOT_DIR).layer_dir[Layer.FEATURE]


def _flip_glob(
    exchange: str,
    symbol: str,
    box_size_rate: float,
    sar_step: float,
    features_root: Path,
) -> str:
    return str(
        features_root / "dataset=ChartRangeSAR"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / f"box_size_rate={box_size_rate}" / f"step={sar_step}"
        / "date=*" / "*.parquet"
    )


def load_sar_flips(
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    box_size_rate: float,
    sar_step: float,
    features_root: Path | None = None,
) -> pd.DataFrame:
    """Load SAR flip events and pair each with the next flip timestamp via LEAD.

    Returns DataFrame with columns [ts, sar_up, exit_ts] where:
        ts        float64  Unix timestamp of this flip
        sar_up    float64  +1 (SAR flipped up) or -1 (SAR flipped down)
        exit_ts   float64  ts of the next flip (NaN for the last flip)
    """
    if features_root is None:
        features_root = _features_root()
    glob = _flip_glob(exchange, symbol, box_size_rate, sar_step, features_root)
    return duckdb.connect().sql(f"""
        SELECT ts, sar_up,
               LEAD(ts) OVER (ORDER BY ts) AS exit_ts
        FROM read_parquet('{glob}', hive_partitioning=true)
        WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ORDER BY ts
    """).df()
