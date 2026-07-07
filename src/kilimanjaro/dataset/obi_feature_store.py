"""OBIFeature dataset: EMA-smoothed order book imbalance from Makalu BookState.

Reads BINANCE BookState (silver layer), resamples to a 1-second grid, then
computes exponential moving averages of volume imbalance at multiple halflife
windows, writing one parquet file per calendar day.

Resampling approach
-------------------
BookState ticks are irregular (~10–50ms spacing during active periods).
We floor each tick to the second and take the last value per second bucket.
Quiet-period seconds are forward-filled (OBI doesn't change between ticks).
This cuts file size ~10x vs tick-level and makes EMA computation simple.

EMA computation
---------------
With a regular 1s grid, halflife=N periods = N seconds directly.
Uses pandas ewm(halflife=N, adjust=True) — the standard adjust=True form
gives a proper infinite-history EMA initialization from the series start.
Warmup: one extra day of data is loaded before the target window so the
EMA state is warm at day boundaries (not zero-initialized).

Partition path (feature layer):
    {PARQUET_ROOT}/feature/dataset=OBIFeature
        /exchange={exchange}/symbol={symbol}
        /date={YYYY-MM-DD}/data.parquet

Schema — one row per second (1s resampled):
    ts               float64  Unix timestamp (floored to second)
    vol_imb_k1       float64  Last raw volume imbalance at depth 1 in that second
    vol_imb_k3       float64  Last raw volume imbalance at depth 3
    vol_imb_k5       float64  Last raw volume imbalance at depth 5
    vol_imb_k10      float64  Last raw volume imbalance at depth 10
    ema_k1_10s       float64  EMA of vol_imb_k1 with halflife=10s
    ema_k1_30s       float64  halflife=30s
    ema_k1_60s       float64  halflife=60s
    ema_k1_120s      float64  halflife=120s
    ema_k1_300s      float64  halflife=300s
    ema_k3_{w}s ...  float64  same for depth 3
    ema_k5_{w}s ...  float64  same for depth 5
    ema_k10_{w}s ... float64  same for depth 10

EMA halflives stored: 10s, 30s, 60s, 120s, 300s
EMA depths stored: k1, k3, k5, k10

Age columns
-----------
For each EMA column, a corresponding age column tracks how many seconds the
EMA sign has been continuously unchanged:
    age_k{depth}_{w}s  int32  seconds since EMA last changed sign (0 = just flipped)

age=0 means the EMA just crossed zero — the freshest, most actionable signal.
age=60 means the EMA has held the same sign for 60 seconds — likely already priced in.
The scanner can filter on max_age_seconds to trade only fresh crossings.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

LOG = logging.getLogger(__name__)

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

_EMA_DEPTHS = [1, 3, 5, 10]
_EMA_HALFLIVES_S = [10, 30, 60, 120, 300]

_RAW_COLS = ["vol_imb_k1", "vol_imb_k3", "vol_imb_k5", "vol_imb_k10"]


def _bs_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "silver" / "dataset=BookState"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / "date=*" / "*.parquet"
    )


def _out_dir(exchange: str, symbol: str, date: str, data_root: Path) -> Path:
    return (
        data_root / "feature" / "dataset=OBIFeature"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / f"date={date}"
    )


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    days = (end - start).days + 1
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(days)]


def _sign_age(series: pd.Series) -> pd.Series:
    """Seconds the sign of `series` has been continuously unchanged.

    Returns 0 at sign-change moments (the crossing instant) and increments
    each second the sign holds. Resets to 0 whenever sign is 0 (neutral OBI).
    """
    sign = np.sign(series)
    changed = (sign != sign.shift(1)) | (sign == 0)
    group = changed.cumsum()
    age = group.groupby(group).cumcount().astype(np.int32)
    age[sign == 0] = 0
    return age


def _resample_and_compute_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Resample tick data to 1s grid and compute EMA columns.

    Steps:
      1. Floor ts to integer second, take last OBI per second bucket.
      2. Reindex to fill every second in the range (forward-fill gaps).
      3. Compute EMA for each (depth, halflife) pair — at 1s resolution
         halflife=N periods equals N seconds exactly.
    """
    df = df.copy()
    df["ts_s"] = df["ts"].astype("int64")

    # Last OBI value per second bucket
    df_1s = df.groupby("ts_s")[_RAW_COLS].last()

    # Fill every second in range so EMA doesn't skip gaps
    full_idx = pd.RangeIndex(int(df["ts_s"].min()), int(df["ts_s"].max()) + 1)
    df_1s = df_1s.reindex(full_idx).ffill()

    for depth in _EMA_DEPTHS:
        raw_col = f"vol_imb_k{depth}"
        for hw in _EMA_HALFLIVES_S:
            ema_col = f"ema_k{depth}_{hw}s"
            df_1s[ema_col] = df_1s[raw_col].ewm(halflife=hw, adjust=True).mean()
            df_1s[f"age_k{depth}_{hw}s"] = _sign_age(df_1s[ema_col])

    df_1s.index.name = "ts"
    df_1s = df_1s.reset_index()
    df_1s["ts"] = df_1s["ts"].astype("float64")
    return df_1s


def populate(
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    recreate: bool = False,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> int:
    """Populate OBIFeature parquet files for exchange/symbol over the date range.

    Returns the number of dates written (skipped dates not counted).
    """
    import duckdb

    dates = _date_range(start_date, end_date)

    # Check which dates need writing
    to_write = []
    for d in dates:
        out_path = _out_dir(exchange, symbol, d, data_root) / "data.parquet"
        if out_path.exists() and not recreate:
            LOG.info("Skip %s (already exists)", d)
        else:
            to_write.append(d)

    if not to_write:
        LOG.info("All dates already populated, nothing to do.")
        return 0

    # Load BookState for full range + 1 warmup day before start
    warmup_start = (
        datetime.date.fromisoformat(start_date) - datetime.timedelta(days=1)
    ).isoformat()
    bs_glob = _bs_glob(exchange, symbol, data_root)
    con = duckdb.connect()

    LOG.info(
        "Loading BookState %s/%s from %s to %s (warmup from %s)…",
        exchange, symbol, start_date, end_date, warmup_start,
    )
    df = con.sql(f"""
        SELECT ts, {', '.join(_RAW_COLS)}
        FROM read_parquet('{bs_glob}', hive_partitioning=true, union_by_name=true)
        WHERE date >= DATE '{warmup_start}' AND date <= DATE '{end_date}'
        ORDER BY ts
    """).df()

    if df.empty:
        raise ValueError(
            f"No BookState data for {exchange}/{symbol} in {warmup_start}..{end_date}"
        )

    LOG.info("Loaded %d rows — resampling to 1s and computing EMA…", len(df))
    df = _resample_and_compute_emas(df)

    # Write per-day partitions (warmup day excluded)
    _JST = 9 * 3600
    df["_date"] = (
        pd.to_datetime((df["ts"] + _JST).astype("int64"), unit="s", utc=True)
        .dt.date.astype(str)
    )

    written = 0
    for d in to_write:
        day_df = df[df["_date"] == d].drop(columns=["_date"]).reset_index(drop=True)
        if day_df.empty:
            LOG.warning("No data for %s — skipping", d)
            continue
        out_path = _out_dir(exchange, symbol, d, data_root) / "data.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(day_df, preserve_index=False), out_path)
        LOG.info("Wrote %d rows → %s", len(day_df), out_path)
        written += 1

    return written
