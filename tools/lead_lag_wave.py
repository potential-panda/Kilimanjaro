#!/usr/bin/env python3
"""Lead-lag daily wave: at a given time of day, how strongly and stably
does one market lead another, and by how much lag?

**Revised (2026-06-19).** Originally read Makalu's precomputed gold-layer
LeadLag dataset. Found for real while building tools/scanner.py's
market_context: that dataset only covers 2025-12-30..2026-01-31, which
doesn't overlap any window this project has actually scanned -- so a wave
built from it would describe a different period than the one any
hypothesis was tested against. Rewritten to compute cross-correlation
directly from bronze OrderBook (the only dataset Kilimanjaro treats as
guaranteed, per the Bronze-First Reliability Model), the same per-day
peak-lag method as scanner.py's _measured_lead_lag, but aggregated by
30-minute time-of-day bucket across many days to build a genuinely STATIC,
historical profile -- not a live measurement of one scan's window. This
also generalizes beyond the original BINANCE->GMO-JPY-margin scope:
_SUPPORTED_PAIRS remains the convenient "asset" shorthand for those three
known relationships, but compute_wave() itself takes any
(feature_exchange, feature_symbol, target_exchange, target_symbol).

Method (per bucket, per day): resample both markets to 1-second mid-price
bars, join on the shared second grid, compute log returns, search integer
lags in [-max_lag_seconds, +max_lag_seconds] for peak cross-correlation.
Average the per-day results within each bucket across all available days.
A proxy, not a true microstructure model -- documented limitation, not
hidden, same spirit as fill_probability.py's own documented proxy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from scanner import _peak_lag_corr  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_WAVES_DIR = _ROOT / "markets" / "waves"
_DEFAULT_GRAPHS_DIR = _ROOT / "markets" / "graphs"

_JST_OFFSET_SECONDS = 9 * 3600
_DEFAULT_BUCKET_MINUTES = 30
_DEFAULT_MAX_LAG_SECONDS = 10
_MIN_ROWS_PER_CELL = 200

# Convenience shorthand for the three originally-confirmed BINANCE-leads-
# GMO-JPY-margin relationships. compute_wave() itself works for any pair.
_SUPPORTED_PAIRS = {
    "BTC": ("BINANCE", "BTC_USDT", "GMO", "BTC_JPY"),
    "ETH": ("BINANCE", "ETH_USDT", "GMO", "ETH_JPY"),
    "XRP": ("BINANCE", "XRP_USDT", "GMO", "XRP_JPY"),
}


def _ob_glob(data_root: Path, exchange: str, symbol: str) -> str:
    return str(data_root / "bronze" / "dataset=OrderBook" / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet")


def compute_wave(
    feature_exchange: str,
    feature_symbol: str,
    target_exchange: str,
    target_symbol: str,
    bucket_minutes: int = _DEFAULT_BUCKET_MINUTES,
    max_lag_seconds: int = _DEFAULT_MAX_LAG_SECONDS,
    start_date: str | None = None,
    end_date: str | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    feature_glob = _ob_glob(data_root, feature_exchange, feature_symbol)
    target_glob = _ob_glob(data_root, target_exchange, target_symbol)
    if feature_glob == target_glob:
        raise ValueError("feature and target are the same market -- lead-lag isn't meaningful against yourself")

    date_filter = ""
    if start_date and end_date:
        date_filter = f"AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'"

    bucket_seconds = bucket_minutes * 60
    sql = f"""
    WITH f AS (
      SELECT CAST(floor(ts) AS BIGINT) AS t_sec, last((bid0_price + ask0_price) / 2.0 ORDER BY ts) AS mid
      FROM read_parquet('{feature_glob}', hive_partitioning=true)
      WHERE ask0_price > bid0_price {date_filter}
      GROUP BY t_sec
    ),
    t AS (
      SELECT CAST(floor(ts) AS BIGINT) AS t_sec, last((bid0_price + ask0_price) / 2.0 ORDER BY ts) AS mid
      FROM read_parquet('{target_glob}', hive_partitioning=true)
      WHERE ask0_price > bid0_price {date_filter}
      GROUP BY t_sec
    )
    SELECT f.t_sec,
      CAST(DATE_TRUNC('day', TO_TIMESTAMP(f.t_sec + {_JST_OFFSET_SECONDS})) AS VARCHAR) AS jst_date,
      CAST(floor((f.t_sec + {_JST_OFFSET_SECONDS}) % 86400 / {bucket_seconds}) AS INT) AS bucket,
      f.mid AS feature_mid, t.mid AS target_mid
    FROM f JOIN t ON f.t_sec = t.t_sec
    ORDER BY f.t_sec
    """
    con = duckdb.connect()
    raw = con.sql(sql).df().dropna()

    cells = []
    for (jst_date, bucket), cell in raw.groupby(["jst_date", "bucket"]):
        if len(cell) < _MIN_ROWS_PER_CELL:
            continue
        feature_ret = np.diff(np.log(cell["feature_mid"].to_numpy()))
        target_ret = np.diff(np.log(cell["target_mid"].to_numpy()))
        result = _peak_lag_corr(feature_ret, target_ret, max_lag_seconds)
        if result is not None:
            best_lag, best_corr, corr_at_zero = result
            cells.append({"bucket": int(bucket), "best_lag_seconds": best_lag, "best_corr": best_corr, "corr_at_zero": corr_at_zero})

    cells_df = pd.DataFrame(cells)
    if cells_df.empty:
        return pd.DataFrame(columns=["bucket", "bucket_start_jst", "feature_leads_fraction", "mean_best_lag_seconds", "mean_best_corr", "mean_corr_at_zero", "n_days"])

    wave = cells_df.groupby("bucket", as_index=False).agg(
        feature_leads_fraction=("best_lag_seconds", lambda x: float((x > 0).mean())),
        mean_best_lag_seconds=("best_lag_seconds", "mean"),
        mean_best_corr=("best_corr", "mean"),
        mean_corr_at_zero=("corr_at_zero", "mean"),
        n_days=("best_lag_seconds", "count"),
    ).sort_values("bucket")
    wave["bucket_start_jst"] = wave["bucket"].apply(lambda b: f"{(b * bucket_minutes) // 60:02d}:{(b * bucket_minutes) % 60:02d}")
    wave.insert(0, "target_symbol", target_symbol)
    wave.insert(0, "target_exchange", target_exchange)
    wave.insert(0, "feature_symbol", feature_symbol)
    wave.insert(0, "feature_exchange", feature_exchange)
    return wave.reset_index(drop=True)


def compute_wave_for_known_asset(asset: str, **kwargs) -> pd.DataFrame:
    if asset not in _SUPPORTED_PAIRS:
        raise ValueError(f"{asset} not in known pairs: {sorted(_SUPPORTED_PAIRS)} -- use compute_wave() directly for any other pair")
    return compute_wave(*_SUPPORTED_PAIRS[asset], **kwargs)


def write_wave(df: pd.DataFrame, name: str, waves_dir: Path = _DEFAULT_WAVES_DIR) -> Path:
    waves_dir.mkdir(parents=True, exist_ok=True)
    path = waves_dir / f"leadlag_{name}.csv"
    df.to_csv(path, index=False)
    return path


def plot_wave(df: pd.DataFrame, name: str, graphs_dir: Path = _DEFAULT_GRAPHS_DIR) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    graphs_dir.mkdir(parents=True, exist_ok=True)
    df = df.sort_values("bucket")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax1.plot(df["bucket_start_jst"], df["feature_leads_fraction"], color="tab:blue")
    ax1.set_ylabel("P(feature market leads)")
    ax1.set_ylim(0, 1)
    ax1.set_title(f"{name}: lead-lag daily wave")
    ax1.grid(alpha=0.3)

    ax2.plot(df["bucket_start_jst"], df["mean_best_corr"], color="tab:orange", label="best-lag corr")
    ax2.plot(df["bucket_start_jst"], df["mean_corr_at_zero"], color="tab:gray", label="zero-lag corr")
    ax2.set_ylabel("Cross-correlation")
    ax2.set_xlabel("Time of day (JST)")
    ax2.set_xticks(ax2.get_xticks()[::4])
    ax2.legend()
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    path = graphs_dir / f"leadlag_{name}.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def query_at(df: pd.DataFrame, time_of_day: str, bucket_minutes: int = _DEFAULT_BUCKET_MINUTES) -> dict:
    hh, mm = (int(x) for x in time_of_day.split(":"))
    bucket = (hh * 60 + mm) // bucket_minutes
    rows = df[df["bucket"] == bucket]
    if rows.empty:
        return {"time_of_day": time_of_day, "bucket": bucket, "error": "no data for this bucket"}
    row = rows.iloc[0]
    return {
        "time_of_day": time_of_day,
        "bucket": int(bucket),
        "bucket_start_jst": row["bucket_start_jst"],
        "feature_leads_fraction": float(row["feature_leads_fraction"]),
        "mean_best_lag_seconds": float(row["mean_best_lag_seconds"]),
        "mean_best_corr": float(row["mean_best_corr"]),
        "mean_corr_at_zero": float(row["mean_corr_at_zero"]),
    }


def _cmd_compute(args: argparse.Namespace) -> int:
    if args.asset:
        df = compute_wave_for_known_asset(args.asset, bucket_minutes=args.bucket_minutes, start_date=args.start, end_date=args.end)
        name = f"BINANCE_GMO_{args.asset}"
    else:
        df = compute_wave(
            args.feature_exchange, args.feature_symbol, args.target_exchange, args.target_symbol,
            bucket_minutes=args.bucket_minutes, start_date=args.start, end_date=args.end,
        )
        name = f"{args.feature_exchange}_{args.feature_symbol}__{args.target_exchange}_{args.target_symbol}"
    csv_path = write_wave(df, name)
    print(f"Wrote {csv_path} ({len(df)} rows)")
    if args.plot:
        png_path = plot_wave(df, name)
        print(f"Wrote {png_path}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    import json

    df = pd.read_csv(args.wave_csv)
    result = query_at(df, args.time, bucket_minutes=args.bucket_minutes)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Lead-lag daily wave, computed directly from bronze OrderBook")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compute = sub.add_parser("compute", help="Compute a lead-lag wave")
    p_compute.add_argument("--asset", default=None, choices=sorted(_SUPPORTED_PAIRS), help="Shorthand for a known BINANCE->GMO pair")
    p_compute.add_argument("--feature-exchange", default=None)
    p_compute.add_argument("--feature-symbol", default=None)
    p_compute.add_argument("--target-exchange", default=None)
    p_compute.add_argument("--target-symbol", default=None)
    p_compute.add_argument("--bucket-minutes", type=int, default=_DEFAULT_BUCKET_MINUTES)
    p_compute.add_argument("--start", default=None)
    p_compute.add_argument("--end", default=None)
    p_compute.add_argument("--plot", action="store_true")
    p_compute.set_defaults(func=_cmd_compute)

    p_query = sub.add_parser("query", help="Answer lead-lag strength/stability for a time of day")
    p_query.add_argument("--wave-csv", required=True)
    p_query.add_argument("--time", required=True, help="HH:MM, JST")
    p_query.add_argument("--bucket-minutes", type=int, default=_DEFAULT_BUCKET_MINUTES)
    p_query.set_defaults(func=_cmd_query)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
