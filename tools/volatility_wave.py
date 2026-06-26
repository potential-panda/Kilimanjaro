#!/usr/bin/env python3
"""Volatility-vs-spread rolling wave: for a market, at each JST time-of-day
bucket and tested horizon H, what fraction of observations have
|mid(t+H) − mid(t)| > k × spread(t)?

This answers the question that has to come before testing any signal: is
horizon H even capable of clearing the spread often enough to be worth
testing, and does that capability vary predictably through the day?

## X → Y → Z computation model (see markets/wave-design.md for full design)

Three parameters control the computation:

  X (x_seconds)  — resample granularity: raw OrderBook ticks are aggregated
                    to X-second bars (last mid/spread per window).
  Y (y_seconds)  — observation interval: one sample is taken every Y seconds.
                    Spreads the observation load and sets the sample density.
  Z (z_seconds)  — wave bucket width: one wave point covers Z seconds of JST
                    time-of-day. With Z=3600 we get 24 hourly points; with
                    Z=1800 we get 48 half-hourly points (old default).

Sample count per wave point ≈ 60 × (Z / Y) over the default 60-day window
(e.g. 60 × 12 = 720 for Y=300, Z=3600).

## Rolling window

Each wave file covers a fixed lookback window (default 60 days). Files are
named by their window end date and stored under a param-specific subdirectory:

  markets/waves/{exchange}_{symbol}/x{X}_y{Y}_z{Z}/{window_end_date}.csv

Use load_wave(exchange, symbol, x, y, z, as_of_date) to get the wave that
was current as of a given date.

## Data source

Computed directly from bronze OrderBook (the only guaranteed dataset, per the
Bronze-First Reliability Model in requirements/makalu-integration-contract.md)
via DuckDB reading Makalu's parquet files read-only — not through Makalu's
query CLI, whose JSON output is capped at 200 rows. mid = (bid0+ask0)/2,
spread = ask0−bid0, both top-of-book only.

Time-of-day bucketing is fixed-offset JST (UTC+9, no DST in Japan) since
every market in markets/registry.yaml trades in JPY.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

_ROOT = Path(__file__).parent.parent
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_WAVES_DIR = _ROOT / "markets" / "waves"
_DEFAULT_GRAPHS_DIR = _ROOT / "markets" / "graphs"

_JST_OFFSET_SECONDS = 9 * 3600
_DEFAULT_HORIZONS = [1, 5, 30, 120, 300]
_DEFAULT_K_VALUES = [1, 2, 3]
_DEFAULT_X_SECONDS = 1
_DEFAULT_Y_SECONDS = 300
_DEFAULT_Z_SECONDS = 3600
_DEFAULT_LOOKBACK_DAYS = 60


def compute_wave(
    exchange: str,
    symbol: str,
    x_seconds: int = _DEFAULT_X_SECONDS,
    y_seconds: int = _DEFAULT_Y_SECONDS,
    z_seconds: int = _DEFAULT_Z_SECONDS,
    horizons: list[int] = _DEFAULT_HORIZONS,
    k_values: list[int] = _DEFAULT_K_VALUES,
    start_date: str | None = None,
    end_date: str | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Compute a rolling wave table.

    Returns one row per (bucket, horizon_seconds). Each bucket covers
    z_seconds of JST time-of-day, centered using a ±z_seconds/2 window
    so adjacent buckets tile without overlap.

    Parameters
    ----------
    x_seconds : resample granularity — raw ticks → X-second bars
    y_seconds : observation interval — one sample per Y seconds
    z_seconds : wave bucket width — one wave point per Z seconds of JST day
    """
    glob = str(
        data_root / "bronze" / "dataset=OrderBook"
        / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet"
    )
    date_filter = ""
    if start_date and end_date:
        date_filter = f"AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'"

    n_buckets = 86400 // z_seconds
    k_exprs = ",\n      ".join(
        f"sum(CASE WHEN abs(mid_fwd - mid) > {k}*spread THEN 1 ELSE 0 END)::DOUBLE"
        f" / count(*) AS p_gt_{k}x"
        for k in k_values
    )

    frames = []
    con = duckdb.connect()
    for horizon in horizons:
        sql = f"""
        WITH ob_raw AS (
          SELECT ts,
                 (bid0_price + ask0_price) / 2.0 AS mid,
                 (ask0_price - bid0_price)        AS spread
          FROM read_parquet('{glob}', hive_partitioning=true)
          WHERE ask0_price > bid0_price {date_filter}
        ),
        ob_x AS (
          -- resample to X-second bars: last mid/spread per window
          SELECT
            CAST(floor(ts / {x_seconds}) AS BIGINT) * {x_seconds} AS t_x,
            arg_max(mid,    ts) AS mid,
            arg_max(spread, ts) AS spread
          FROM ob_raw
          GROUP BY CAST(floor(ts / {x_seconds}) AS BIGINT) * {x_seconds}
        ),
        ob_y AS (
          -- downsample to Y-second observation marks
          SELECT
            CAST(floor(t_x / {y_seconds}) AS BIGINT) * {y_seconds} AS t_y,
            arg_max(mid,    t_x) AS mid,
            arg_max(spread, t_x) AS spread
          FROM ob_x
          GROUP BY CAST(floor(t_x / {y_seconds}) AS BIGINT) * {y_seconds}
        ),
        joined AS (
          -- forward return: ASOF join against X-second bars for best precision
          -- at horizon H even when H is not a multiple of Y
          SELECT
            a.t_y                                            AS ts,
            a.mid                                            AS mid,
            a.spread                                         AS spread,
            b.mid                                            AS mid_fwd
          FROM ob_y a
          ASOF JOIN ob_x b ON b.t_x >= a.t_y + {horizon}
        )
        SELECT
          -- bucket by JST time-of-day using ±Z/2 centering:
          -- shift ts by Z/2 so the bucket boundary falls between hour marks
          CAST(floor(((ts + {_JST_OFFSET_SECONDS} + {z_seconds // 2}) % 86400)
               / {z_seconds}) AS INT) AS bucket,
          avg(mid)    AS avg_mid,
          avg(spread) AS avg_spread,
          avg(spread / mid) * 10000 AS avg_spread_bps,
          count(*)    AS n,
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

    # bucket label: center of the Z-second bucket in JST HH:MM
    def _bucket_label(b: int) -> str:
        center_sec = b * z_seconds
        hh = (center_sec // 3600) % 24
        mm = (center_sec % 3600) // 60
        return f"{hh:02d}:{mm:02d}"

    result["bucket_start_jst"] = result["bucket"].apply(_bucket_label)
    result["window_start"] = start_date or ""
    result["window_end"] = end_date or ""
    return result


def write_wave(
    df: pd.DataFrame,
    exchange: str,
    symbol: str,
    x_seconds: int,
    y_seconds: int,
    z_seconds: int,
    waves_dir: Path = _DEFAULT_WAVES_DIR,
) -> Path:
    """Write the wave to its dated param-specific file.

    Path: {waves_dir}/{exchange}_{symbol}/x{X}_y{Y}_z{Z}/{window_end}.csv
    """
    end_date = df["window_end"].iloc[0] if "window_end" in df.columns else "unknown"
    param_dir = waves_dir / f"{exchange}_{symbol}" / f"x{x_seconds}_y{y_seconds}_z{z_seconds}"
    param_dir.mkdir(parents=True, exist_ok=True)
    path = param_dir / f"{end_date}.csv"
    df.to_csv(path, index=False)
    return path


def load_wave(
    exchange: str,
    symbol: str,
    x_seconds: int = _DEFAULT_X_SECONDS,
    y_seconds: int = _DEFAULT_Y_SECONDS,
    z_seconds: int = _DEFAULT_Z_SECONDS,
    as_of_date: str | None = None,
    waves_dir: Path = _DEFAULT_WAVES_DIR,
) -> pd.DataFrame | None:
    """Load the most recent wave file with window_end <= as_of_date.

    Returns None if no wave exists for the given params / as-of date.
    """
    param_dir = waves_dir / f"{exchange}_{symbol}" / f"x{x_seconds}_y{y_seconds}_z{z_seconds}"
    if not param_dir.exists():
        return None
    candidates = sorted(param_dir.glob("*.csv"))
    if not candidates:
        return None
    if as_of_date is None:
        return pd.read_csv(candidates[-1])
    valid = [f for f in candidates if f.stem <= as_of_date]
    if not valid:
        return None
    return pd.read_csv(valid[-1])


def plot_wave(
    df: pd.DataFrame,
    exchange: str,
    symbol: str,
    x_seconds: int,
    y_seconds: int,
    z_seconds: int,
    graphs_dir: Path = _DEFAULT_GRAPHS_DIR,
    k: int = 1,
) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    graphs_dir.mkdir(parents=True, exist_ok=True)
    buckets = sorted(df["bucket"].unique())
    n_buckets = len(buckets)
    tick_step = max(1, n_buckets // 12)

    fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    ax1 = axes[0]
    for horizon in sorted(df["horizon_seconds"].unique()):
        sub = df[df["horizon_seconds"] == horizon].sort_values("bucket")
        ax1.plot(sub["bucket_start_jst"], sub[f"p_gt_{k}x"], label=f"{horizon}s", marker="o", markersize=2)
    ax1.set_ylabel(f"P(|return| > {k}× spread)")
    window = f"{df['window_start'].iloc[0]} – {df['window_end'].iloc[0]}" if "window_start" in df.columns else ""
    ax1.set_title(f"{exchange}/{symbol} — volatility wave (x={x_seconds}s y={y_seconds}s z={z_seconds}s)  [{window}]")
    ax1.legend(title="horizon")
    ax1.grid(alpha=0.3)
    ax1.set_ylim(0, 1)

    ax2 = axes[1]
    sub30 = df[df["horizon_seconds"] == df["horizon_seconds"].min()].sort_values("bucket")
    ax2.fill_between(sub30["bucket_start_jst"], sub30["avg_spread"], alpha=0.4, color="tab:gray")
    ax2.plot(sub30["bucket_start_jst"], sub30["avg_spread"], color="tab:gray")
    ax2.set_ylabel("Avg spread (native units)")
    ax2.set_xlabel("Time of day (JST, bucket center)")
    ax2.set_title("Avg bid-ask spread")
    ax2.grid(alpha=0.3)

    all_labels = df.sort_values("bucket")["bucket_start_jst"].unique()
    for ax in axes:
        ax.set_xticks(range(0, n_buckets, tick_step))
        ax.set_xticklabels(all_labels[::tick_step], rotation=45, ha="right")

    fig.tight_layout()
    param_tag = f"x{x_seconds}_y{y_seconds}_z{z_seconds}"
    path = graphs_dir / f"{exchange}_{symbol}_{param_tag}_k{k}x.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def query_at(
    df: pd.DataFrame,
    time_of_day: str,
    z_seconds: int = _DEFAULT_Z_SECONDS,
    target_probability: float = 0.5,
) -> dict:
    """Given a wave table and a JST time (HH:MM), return the wave data for
    that bucket plus the smallest horizon that clears target_probability at 1x.
    """
    hh, mm = (int(x) for x in time_of_day.split(":"))
    total_seconds = hh * 3600 + mm * 60
    bucket = (total_seconds + z_seconds // 2) % 86400 // z_seconds
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
        "bucket": int(bucket),
        "bucket_start_jst": rows.iloc[0]["bucket_start_jst"],
        "avg_spread": float(rows.iloc[0]["avg_spread"]),
        "horizons": horizons,
        "recommended_min_horizon_seconds": recommended_min_horizon,
        "recommendation_note": (
            f"smallest tested horizon where P(>1x spread) >= {target_probability}"
            if recommended_min_horizon is not None
            else f"no tested horizon reaches P(>1x spread) >= {target_probability} in this bucket"
        ),
        "window_start": rows.iloc[0].get("window_start", ""),
        "window_end": rows.iloc[0].get("window_end", ""),
    }


def _end_date_from_lookback(end: str | None, lookback_days: int) -> tuple[str, str]:
    end_dt = date.fromisoformat(end) if end else date.today()
    start_dt = end_dt - timedelta(days=lookback_days)
    return str(start_dt), str(end_dt)


def _cmd_compute(args: argparse.Namespace) -> int:
    start_date, end_date = _end_date_from_lookback(args.end, args.lookback_days)
    horizons = [int(h) for h in args.horizons.split(",")]
    df = compute_wave(
        args.exchange, args.symbol,
        x_seconds=args.x_seconds,
        y_seconds=args.y_seconds,
        z_seconds=args.z_seconds,
        horizons=horizons,
        start_date=start_date,
        end_date=end_date,
    )
    csv_path = write_wave(df, args.exchange, args.symbol, args.x_seconds, args.y_seconds, args.z_seconds)
    print(f"Window: {start_date} → {end_date}  ({len(df)} rows)")
    print(f"Wrote {csv_path}")
    if args.plot:
        png_path = plot_wave(df, args.exchange, args.symbol, args.x_seconds, args.y_seconds, args.z_seconds)
        print(f"Wrote {png_path}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    import json
    df = pd.read_csv(args.wave_csv)
    result = query_at(df, args.time, z_seconds=args.z_seconds, target_probability=args.target_probability)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    """Load and print the wave that was current as of a given date."""
    import json
    df = load_wave(
        args.exchange, args.symbol,
        x_seconds=args.x_seconds,
        y_seconds=args.y_seconds,
        z_seconds=args.z_seconds,
        as_of_date=args.as_of,
    )
    if df is None:
        print(f"No wave found for {args.exchange}/{args.symbol} x={args.x_seconds} y={args.y_seconds} z={args.z_seconds} as_of={args.as_of}", file=sys.stderr)
        return 1
    window = f"{df['window_start'].iloc[0]} – {df['window_end'].iloc[0]}"
    print(f"Loaded wave: {len(df)} rows, window {window}")
    if args.time:
        result = query_at(df, args.time, z_seconds=args.z_seconds)
        print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Volatility-vs-spread rolling wave (X→Y→Z model)")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── compute ──────────────────────────────────────────────────────────────
    p = sub.add_parser("compute", help="Compute a wave from bronze OrderBook and write to dated CSV")
    p.add_argument("--exchange", required=True)
    p.add_argument("--symbol",   required=True)
    p.add_argument("--x-seconds",      type=int, default=_DEFAULT_X_SECONDS,   help="resample granularity (default 1)")
    p.add_argument("--y-seconds",      type=int, default=_DEFAULT_Y_SECONDS,   help="observation interval (default 300)")
    p.add_argument("--z-seconds",      type=int, default=_DEFAULT_Z_SECONDS,   help="wave bucket width in seconds (default 3600)")
    p.add_argument("--horizons",       default=",".join(str(h) for h in _DEFAULT_HORIZONS), help="comma-separated horizon seconds")
    p.add_argument("--lookback-days",  type=int, default=_DEFAULT_LOOKBACK_DAYS, help="rolling window size in days (default 60)")
    p.add_argument("--end",            default=None, help="window end date YYYY-MM-DD (default: today)")
    p.add_argument("--plot",           action="store_true")
    p.set_defaults(func=_cmd_compute)

    # ── query ─────────────────────────────────────────────────────────────────
    p = sub.add_parser("query", help="Answer spread/volatility/viable-H for a time of day from a specific CSV")
    p.add_argument("--wave-csv",           required=True)
    p.add_argument("--time",               required=True, help="HH:MM, JST")
    p.add_argument("--z-seconds",          type=int, default=_DEFAULT_Z_SECONDS)
    p.add_argument("--target-probability", type=float, default=0.5)
    p.set_defaults(func=_cmd_query)

    # ── load ──────────────────────────────────────────────────────────────────
    p = sub.add_parser("load", help="Load the wave current as-of a date and optionally query a time")
    p.add_argument("--exchange", required=True)
    p.add_argument("--symbol",   required=True)
    p.add_argument("--x-seconds", type=int, default=_DEFAULT_X_SECONDS)
    p.add_argument("--y-seconds", type=int, default=_DEFAULT_Y_SECONDS)
    p.add_argument("--z-seconds", type=int, default=_DEFAULT_Z_SECONDS)
    p.add_argument("--as-of",    default=None, help="YYYY-MM-DD (default: latest available)")
    p.add_argument("--time",     default=None, help="optional HH:MM to query after loading")
    p.set_defaults(func=_cmd_load)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
