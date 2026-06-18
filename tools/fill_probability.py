#!/usr/bin/env python3
"""Limit-order fill probability: for a hypothetical resting limit order
placed X basis points away from the best bid/ask, what's the probability
the market trades through that price within Y seconds?

This matters specifically for the negative-fee (maker rebate) markets in
markets/registry.yaml (order_type: limit_only) -- on those markets only
limit orders are explored at all, so knowing how far from the touch an
order can rest and still expect to fill is as important as the
volatility-vs-spread question is for market/aggressive-order strategies.

Method (a proxy, not a true queue-position simulation -- documented
limitation, not hidden): a buy limit at price = best_bid * (1 - X/10000) is
"filled" within Y seconds if any subsequent trade in bronze Trade prints at
or below that price (symmetric for sell limits above best_ask). This is the
necessary condition for a passive order at that level to have a chance of
filling -- it ignores queue position ahead of the hypothetical order, so it
is an upper bound on true fill probability, not an unbiased estimate.

Like volatility_wave.py and lead_lag_wave.py: bronze OrderBook/Trade read
directly via DuckDB (not Makalu's query CLI, capped at 200 rows), 30-minute
JST buckets. OrderBook is downsampled to one snapshot per second before the
join -- at full tick rate this would be redundant (consecutive snapshots a
few ms apart give near-identical answers) and the per-second density is
still high enough density for a stable bucket aggregate.
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
_DEFAULT_BUCKET_MINUTES = 30
_DEFAULT_X_VALUES_BPS = [1, 5, 10, 20, 50]
_DEFAULT_HORIZONS = [1, 5, 30, 120, 300]


def _x_label(x: float) -> str:
    """SQL-identifier-safe label for a bps value, e.g. 1.5 -> '1p5'."""
    s = f"{x:g}"
    return s.replace(".", "p").replace("-", "m")


def _x_value_from_label(label: str) -> float:
    return float(label.replace("p", ".").replace("m", "-"))


def compute_wave(
    exchange: str,
    symbol: str,
    x_values_bps: list[float] = _DEFAULT_X_VALUES_BPS,
    horizons: list[int] = _DEFAULT_HORIZONS,
    bucket_minutes: int = _DEFAULT_BUCKET_MINUTES,
    start_date: str | None = None,
    end_date: str | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Returns one row per (bucket, horizon_seconds): n, and
    p_fill_buy_x{X}bps / p_fill_sell_x{X}bps for each X in x_values_bps."""
    trade_glob = str(data_root / "bronze" / "dataset=Trade" / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet")
    ob_glob = str(data_root / "bronze" / "dataset=OrderBook" / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet")
    con = duckdb.connect()

    # Anchor rows (the OrderBook snapshots we treat as order-placement
    # times) are date-filtered; the trade-side rolling extremes are
    # computed over the FULL history first so a lookahead window near the
    # filtered range's edge can still draw on data outside it, the same
    # way the ASOF joins in volatility_wave.py span day boundaries.
    date_filter = ""
    if start_date and end_date:
        date_filter = f"AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'"

    bucket_seconds = bucket_minutes * 60
    x_exprs = ",\n      ".join(
        f"avg(CASE WHEN future_min <= bid0_price*(1-{x}/10000.0) THEN 1.0 ELSE 0.0 END) AS p_fill_buy_x{_x_label(x)}bps,\n"
        f"      avg(CASE WHEN future_max >= ask0_price*(1+{x}/10000.0) THEN 1.0 ELSE 0.0 END) AS p_fill_sell_x{_x_label(x)}bps"
        for x in x_values_bps
    )

    frames = []
    for horizon in horizons:
        sql = f"""
        WITH trades AS (
          SELECT ts, price FROM read_parquet('{trade_glob}', hive_partitioning=true)
        ),
        trade_extremes AS (
          SELECT ts,
            MIN(price) OVER (ORDER BY ts RANGE BETWEEN CURRENT ROW AND {horizon} FOLLOWING) AS future_min,
            MAX(price) OVER (ORDER BY ts RANGE BETWEEN CURRENT ROW AND {horizon} FOLLOWING) AS future_max
          FROM trades
        ),
        ob_sampled AS (
          SELECT ts, bid0_price, ask0_price, date
          FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY CAST(ts AS BIGINT) ORDER BY ts DESC) AS rn
                FROM read_parquet('{ob_glob}', hive_partitioning=true)
                WHERE ask0_price > bid0_price)
          WHERE rn = 1 {date_filter}
        ),
        joined AS (
          SELECT ob.ts, ob.bid0_price, ob.ask0_price, te.future_min, te.future_max
          FROM ob_sampled ob ASOF JOIN trade_extremes te ON te.ts >= ob.ts
        )
        SELECT
          CAST(floor(CAST((ts + {_JST_OFFSET_SECONDS}) AS BIGINT) % 86400 / {bucket_seconds}) AS INT) AS bucket,
          count(*) AS n,
          {x_exprs}
        FROM joined
        WHERE future_min IS NOT NULL
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
    path = waves_dir / f"fillprob_{exchange}_{symbol}.csv"
    df.to_csv(path, index=False)
    return path


def plot_wave(
    df: pd.DataFrame, exchange: str, symbol: str, graphs_dir: Path = _DEFAULT_GRAPHS_DIR,
    horizon_seconds: int = 30, side: str = "buy",
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    graphs_dir.mkdir(parents=True, exist_ok=True)
    sub = df[df["horizon_seconds"] == horizon_seconds].sort_values("bucket")
    x_cols = sorted(
        (c for c in df.columns if c.startswith(f"p_fill_{side}_x")),
        key=lambda c: _x_value_from_label(c.split("_x")[1].replace("bps", "")),
    )

    fig, ax = plt.subplots(figsize=(12, 6))
    for col in x_cols:
        x_bps = _x_value_from_label(col.split("_x")[1].replace("bps", ""))
        ax.plot(sub["bucket_start_jst"], sub[col], label=f"X={x_bps:g}bps")

    ax.set_xlabel("Time of day (JST)")
    ax.set_ylabel(f"P(filled within {horizon_seconds}s)")
    ax.set_title(f"{exchange}/{symbol}: limit-order fill probability ({side} side, Y={horizon_seconds}s)")
    ax.set_xticks(ax.get_xticks()[::4])
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()

    path = graphs_dir / f"fillprob_{exchange}_{symbol}_{side}_Y{horizon_seconds}s.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def query_at(
    df: pd.DataFrame, time_of_day: str, horizon_seconds: int, side: str = "buy",
    bucket_minutes: int = _DEFAULT_BUCKET_MINUTES, target_probability: float = 0.5,
) -> dict:
    """Given a wave table, a JST time, and a horizon Y: report fill
    probability at each tested X, and the largest X (best price
    improvement) that still clears target_probability."""
    hh, mm = (int(x) for x in time_of_day.split(":"))
    bucket = (hh * 60 + mm) // bucket_minutes
    rows = df[(df["bucket"] == bucket) & (df["horizon_seconds"] == horizon_seconds)]
    if rows.empty:
        return {"time_of_day": time_of_day, "bucket": bucket, "horizon_seconds": horizon_seconds, "error": "no data"}
    row = rows.iloc[0]

    x_cols = sorted(
        (c for c in df.columns if c.startswith(f"p_fill_{side}_x")),
        key=lambda c: _x_value_from_label(c.split("_x")[1].replace("bps", "")),
    )
    by_x = []
    recommended_max_distance_bps = None
    for col in x_cols:
        x_bps = _x_value_from_label(col.split("_x")[1].replace("bps", ""))
        p = float(row[col])
        by_x.append({"distance_bps": x_bps, "p_fill": p})
        if p >= target_probability:
            recommended_max_distance_bps = x_bps

    return {
        "time_of_day": time_of_day,
        "bucket": bucket,
        "bucket_start_jst": row["bucket_start_jst"],
        "horizon_seconds": horizon_seconds,
        "side": side,
        "n": int(row["n"]),
        "by_distance": by_x,
        "recommended_max_distance_bps": recommended_max_distance_bps,
        "recommendation_note": (
            f"largest tested distance where P(fill) >= {target_probability}"
            if recommended_max_distance_bps is not None
            else f"no tested distance reaches P(fill) >= {target_probability} in this bucket/horizon"
        ),
    }


def _cmd_compute(args: argparse.Namespace) -> int:
    df = compute_wave(
        args.exchange, args.symbol,
        x_values_bps=[float(x) for x in args.distances.split(",")],
        horizons=[int(h) for h in args.horizons.split(",")],
        bucket_minutes=args.bucket_minutes,
        start_date=args.start, end_date=args.end,
    )
    csv_path = write_wave(df, args.exchange, args.symbol)
    print(f"Wrote {csv_path} ({len(df)} rows)")
    if args.plot:
        png_path = plot_wave(df, args.exchange, args.symbol, horizon_seconds=args.plot_horizon, side=args.plot_side)
        print(f"Wrote {png_path}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    import json

    df = pd.read_csv(args.wave_csv)
    result = query_at(
        df, args.time, args.horizon, side=args.side,
        bucket_minutes=args.bucket_minutes, target_probability=args.target_probability,
    )
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Limit-order fill probability vs. distance from best bid/ask")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compute = sub.add_parser("compute", help="Compute a fill-probability wave from bronze Trade+OrderBook")
    p_compute.add_argument("--exchange", required=True)
    p_compute.add_argument("--symbol", required=True)
    p_compute.add_argument("--distances", default=",".join(str(x) for x in _DEFAULT_X_VALUES_BPS), help="comma-separated bps")
    p_compute.add_argument("--horizons", default=",".join(str(h) for h in _DEFAULT_HORIZONS), help="comma-separated seconds")
    p_compute.add_argument("--bucket-minutes", type=int, default=_DEFAULT_BUCKET_MINUTES)
    p_compute.add_argument("--start", default=None)
    p_compute.add_argument("--end", default=None)
    p_compute.add_argument("--plot", action="store_true")
    p_compute.add_argument("--plot-horizon", type=int, default=30)
    p_compute.add_argument("--plot-side", default="buy", choices=["buy", "sell"])
    p_compute.set_defaults(func=_cmd_compute)

    p_query = sub.add_parser("query", help="Answer fill probability / max viable distance for a time of day")
    p_query.add_argument("--wave-csv", required=True)
    p_query.add_argument("--time", required=True, help="HH:MM, JST")
    p_query.add_argument("--horizon", type=int, required=True)
    p_query.add_argument("--side", default="buy", choices=["buy", "sell"])
    p_query.add_argument("--bucket-minutes", type=int, default=_DEFAULT_BUCKET_MINUTES)
    p_query.add_argument("--target-probability", type=float, default=0.5)
    p_query.set_defaults(func=_cmd_query)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
