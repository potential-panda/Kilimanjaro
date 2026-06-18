#!/usr/bin/env python3
"""Volatility-vs-spread daily wave: for a market, at each time of day, how
likely is the price to move more than 1x/2x/3x the spread within X seconds?

This answers the question that has to come before testing any signal: is
horizon X even capable of clearing the spread often enough to be worth
testing, and does that capability vary predictably through the day?

Computed directly from bronze OrderBook (the only guaranteed dataset, per
the Bronze-First Reliability Model in
requirements/makalu-integration-contract.md) via DuckDB reading Makalu's
parquet files read-only -- not through Makalu's query CLI, whose JSON
output is capped at 200 rows and unsuitable for this volume (see that
contract's "Arbitrary Read Query" rules). mid = (bid0+ask0)/2, spread =
ask0-bid0, both top-of-book only.

Time-of-day bucketing is fixed-offset JST (UTC+9, no DST in Japan) since
every market in markets/registry.yaml trades in JPY.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

_ROOT = Path(__file__).parent.parent
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_WAVES_DIR = _ROOT / "markets" / "waves"
_DEFAULT_GRAPHS_DIR = _ROOT / "markets" / "graphs"

_JST_OFFSET_SECONDS = 9 * 3600
_DEFAULT_HORIZONS = [1, 5, 30, 120, 300]
_DEFAULT_BUCKET_MINUTES = 30
_DEFAULT_K_VALUES = [1, 2, 3]


def compute_wave(
    exchange: str,
    symbol: str,
    horizons: list[int] = _DEFAULT_HORIZONS,
    bucket_minutes: int = _DEFAULT_BUCKET_MINUTES,
    k_values: list[int] = _DEFAULT_K_VALUES,
    start_date: str | None = None,
    end_date: str | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Returns one row per (bucket, horizon_seconds): avg_spread, n, and
    p_gt_{k}x for each k in k_values."""
    glob = str(data_root / "bronze" / "dataset=OrderBook" / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet")
    con = duckdb.connect()

    date_filter = ""
    if start_date and end_date:
        date_filter = f"AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'"

    bucket_seconds = bucket_minutes * 60
    k_exprs = ",\n      ".join(
        f"sum(CASE WHEN abs(mid_fwd - mid) > {k}*spread THEN 1 ELSE 0 END)::DOUBLE / count(*) AS p_gt_{k}x"
        for k in k_values
    )

    frames = []
    for horizon in horizons:
        sql = f"""
        WITH ob AS (
          SELECT ts, (bid0_price+ask0_price)/2.0 AS mid, (ask0_price-bid0_price) AS spread
          FROM read_parquet('{glob}', hive_partitioning=true)
          WHERE ask0_price > bid0_price {date_filter}
        ),
        joined AS (
          SELECT a.ts, a.mid, a.spread, b.mid AS mid_fwd
          FROM ob a ASOF JOIN ob b ON b.ts >= a.ts + {horizon}
        )
        SELECT
          CAST(floor(CAST((ts + {_JST_OFFSET_SECONDS}) AS BIGINT) % 86400 / {bucket_seconds}) AS INT) AS bucket,
          avg(spread) AS avg_spread,
          count(*) AS n,
          {k_exprs}
        FROM joined
        WHERE mid_fwd IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket
        """
        df = con.sql(sql).df()
        df.insert(1, "horizon_seconds", horizon)
        frames.append(df)

    result = pd.concat(frames, ignore_index=True)
    result["bucket_start_jst"] = result["bucket"].apply(
        lambda b: f"{(b * bucket_minutes) // 60:02d}:{(b * bucket_minutes) % 60:02d}"
    )
    return result


def write_wave(df: pd.DataFrame, exchange: str, symbol: str, waves_dir: Path = _DEFAULT_WAVES_DIR) -> Path:
    waves_dir.mkdir(parents=True, exist_ok=True)
    path = waves_dir / f"{exchange}_{symbol}.csv"
    df.to_csv(path, index=False)
    return path


def plot_wave(df: pd.DataFrame, exchange: str, symbol: str, graphs_dir: Path = _DEFAULT_GRAPHS_DIR, k: int = 1) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    graphs_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 6))
    for horizon in sorted(df["horizon_seconds"].unique()):
        sub = df[df["horizon_seconds"] == horizon].sort_values("bucket")
        ax.plot(sub["bucket_start_jst"], sub[f"p_gt_{k}x"], label=f"X={horizon}s")

    ax.set_xlabel("Time of day (JST)")
    ax.set_ylabel(f"P(|return| > {k}x spread)")
    ax.set_title(f"{exchange}/{symbol}: daily wave, probability of clearing {k}x spread")
    ax.set_xticks(ax.get_xticks()[::4])
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()

    path = graphs_dir / f"{exchange}_{symbol}_k{k}x.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def query_at(
    df: pd.DataFrame, time_of_day: str, bucket_minutes: int = _DEFAULT_BUCKET_MINUTES, target_probability: float = 0.5
) -> dict:
    """Given a wave table and a JST time (\"HH:MM\"), answer: what's the
    spread, and for each horizon what's the probability of clearing 1x/2x/3x
    spread, plus the smallest horizon that clears target_probability at 1x."""
    hh, mm = (int(x) for x in time_of_day.split(":"))
    bucket = (hh * 60 + mm) // bucket_minutes
    rows = df[df["bucket"] == bucket].sort_values("horizon_seconds")
    if rows.empty:
        return {"time_of_day": time_of_day, "bucket": bucket, "error": "no data for this bucket"}

    k_cols = [c for c in df.columns if c.startswith("p_gt_")]
    horizons = []
    recommended_min_horizon = None
    for _, row in rows.iterrows():
        entry = {"horizon_seconds": int(row["horizon_seconds"]), **{c: float(row[c]) for c in k_cols}}
        horizons.append(entry)
        if recommended_min_horizon is None and row["p_gt_1x"] >= target_probability:
            recommended_min_horizon = int(row["horizon_seconds"])

    return {
        "time_of_day": time_of_day,
        "bucket": bucket,
        "bucket_start_jst": rows.iloc[0]["bucket_start_jst"],
        "avg_spread": float(rows.iloc[0]["avg_spread"]),
        "horizons": horizons,
        "recommended_min_horizon_seconds": recommended_min_horizon,
        "recommendation_note": (
            f"smallest tested horizon where P(>1x spread) >= {target_probability}"
            if recommended_min_horizon is not None
            else f"no tested horizon reaches P(>1x spread) >= {target_probability} in this bucket"
        ),
    }


def _cmd_compute(args: argparse.Namespace) -> int:
    df = compute_wave(
        args.exchange, args.symbol,
        horizons=[int(h) for h in args.horizons.split(",")],
        bucket_minutes=args.bucket_minutes,
        start_date=args.start, end_date=args.end,
    )
    csv_path = write_wave(df, args.exchange, args.symbol)
    print(f"Wrote {csv_path} ({len(df)} rows)")
    if args.plot:
        png_path = plot_wave(df, args.exchange, args.symbol)
        print(f"Wrote {png_path}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    import json

    df = pd.read_csv(args.wave_csv)
    result = query_at(df, args.time, bucket_minutes=args.bucket_minutes, target_probability=args.target_probability)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Volatility-vs-spread daily wave")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compute = sub.add_parser("compute", help="Compute a wave table from bronze OrderBook")
    p_compute.add_argument("--exchange", required=True)
    p_compute.add_argument("--symbol", required=True)
    p_compute.add_argument("--horizons", default=",".join(str(h) for h in _DEFAULT_HORIZONS), help="comma-separated seconds")
    p_compute.add_argument("--bucket-minutes", type=int, default=_DEFAULT_BUCKET_MINUTES)
    p_compute.add_argument("--start", default=None)
    p_compute.add_argument("--end", default=None)
    p_compute.add_argument("--plot", action="store_true")
    p_compute.set_defaults(func=_cmd_compute)

    p_query = sub.add_parser("query", help="Answer spread/volatility/viable-X for a time of day")
    p_query.add_argument("--wave-csv", required=True)
    p_query.add_argument("--time", required=True, help="HH:MM, JST")
    p_query.add_argument("--bucket-minutes", type=int, default=_DEFAULT_BUCKET_MINUTES)
    p_query.add_argument("--target-probability", type=float, default=0.5)
    p_query.set_defaults(func=_cmd_query)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
