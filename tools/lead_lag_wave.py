#!/usr/bin/env python3
"""BINANCE-leads-GMO lead-lag daily wave.

Per the user's prior successful strategy (signal generated on BINANCE,
traded on GMO), this answers: at a given time of day, how strongly and
stably does BINANCE lead the corresponding GMO JPY pair, and at what lag?

Uses Makalu's existing gold-layer LeadLag dataset (already populated for
BINANCE->GMO btc/eth/xrp, ~33 days) rather than recomputing cross-correlation
ourselves -- Makalu's algorithm is more sophisticated (EWM-decayed,
multi-window cross-correlation with optimal-lag search) than anything worth
reimplementing, and it's already verified queryable. COINCHECK/BITBANK lead-
lag is out of scope for v1 -- Makalu's populate pipeline doesn't support
those market_b targets yet (confirmed 2026-06-18); revisit if needed.

Read via DuckDB directly from Makalu's parquet files (not the query CLI,
whose JSON output caps at 200 rows), same as volatility_wave.py.

Data quality note: dataset_definitions.md documents ll_alpha_gain_{w}_{s} as
"clipped to [1, 100]", but live data shows values far outside that range
(tens of thousands) -- the documented clipping does not match reality.
Use ll_alpha_gain_log (well-behaved) and the binary "alpha_gain > 1" leading
fraction instead of averaging the raw ratio.

ll_opt_lag_ms_{w}_{s} is misleadingly named: per dataset_definitions.md it's
"normalised to [0, 1] by max_lag_ms", not literal milliseconds. max_lag_ms is
recovered from the param_id's `l{start}_{end}_{step}` prefix (e.g.
l0_5100_100 -> max_lag_ms=5100) and used to denormalize back to real ms.
"""

from __future__ import annotations

import argparse
import re
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
_DEFAULT_PARAM_ID = "l0_5100_100_s5_15_30_w10_20_30_60_wsz600_wst1"
_DEFAULT_WINDOW_SECONDS = 60
_DEFAULT_SPAN_SECONDS = 30

# BINANCE->GMO pairs Makalu's populate LeadLag pipeline actually supports
# (confirmed 2026-06-18). COINCHECK/BITBANK are not available.
_SUPPORTED_PAIRS = {
    "BTC": ("BINANCE:BTC_USDT", "GMO:BTC_JPY"),
    "ETH": ("BINANCE:ETH_USDT", "GMO:ETH_JPY"),
    "XRP": ("BINANCE:XRP_USDT", "GMO:XRP_JPY"),
}


def _max_lag_ms_from_param_id(param_id: str) -> float:
    m = re.match(r"l(\d+)_(\d+)_(\d+)", param_id)
    if not m:
        raise ValueError(f"can't parse max_lag_ms from param_id: {param_id}")
    return float(m.group(2))


def compute_wave(
    asset: str,
    window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    span_seconds: int = _DEFAULT_SPAN_SECONDS,
    bucket_minutes: int = _DEFAULT_BUCKET_MINUTES,
    param_id: str = _DEFAULT_PARAM_ID,
    start_date: str | None = None,
    end_date: str | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    if asset not in _SUPPORTED_PAIRS:
        raise ValueError(f"{asset} not in supported BINANCE->GMO pairs: {sorted(_SUPPORTED_PAIRS)}")
    market_a, market_b = _SUPPORTED_PAIRS[asset]
    max_lag_ms = _max_lag_ms_from_param_id(param_id)

    suffix = f"{window_seconds}s_{span_seconds}s"
    glob = str(
        data_root / "gold" / "dataset=LeadLag" / f"market_a={market_a}" / f"market_b={market_b}"
        / f"param_id={param_id}" / "date=*" / "*.parquet"
    )

    date_filter = ""
    if start_date and end_date:
        date_filter = f"AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'"

    def clean(col: str) -> str:
        return f"CASE WHEN isnan({col}) THEN NULL ELSE {col} END"

    bucket_seconds = bucket_minutes * 60
    sql = f"""
    SELECT
      CAST(floor(CAST((ts + {_JST_OFFSET_SECONDS}) AS BIGINT) % 86400 / {bucket_seconds}) AS INT) AS bucket,
      avg({clean(f'll_corr_at_zero_{suffix}')}) AS avg_corr_at_zero,
      avg({clean(f'll_max_corr_{suffix}')}) AS avg_max_corr,
      avg({clean(f'll_alpha_gain_log_{suffix}')}) AS avg_alpha_gain_log,
      avg({clean(f'll_opt_lag_ms_{suffix}')}) * {max_lag_ms} AS avg_opt_lag_ms,
      avg({clean(f'll_lag_stability_{suffix}')}) AS avg_lag_stability,
      avg(CASE WHEN isnan(ll_alpha_gain_{suffix}) THEN NULL
               WHEN ll_alpha_gain_{suffix} > 1 THEN 1.0 ELSE 0.0 END) AS frac_binance_leads,
      count(*) AS n
    FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)
    WHERE TRUE {date_filter}
    GROUP BY bucket
    ORDER BY bucket
    """
    con = duckdb.connect()
    df = con.sql(sql).df()
    df.insert(0, "asset", asset)
    df.insert(1, "window_seconds", window_seconds)
    df.insert(2, "span_seconds", span_seconds)
    df["bucket_start_jst"] = df["bucket"].apply(
        lambda b: f"{(b * bucket_minutes) // 60:02d}:{(b * bucket_minutes) % 60:02d}"
    )
    return df


def write_wave(df: pd.DataFrame, asset: str, waves_dir: Path = _DEFAULT_WAVES_DIR) -> Path:
    waves_dir.mkdir(parents=True, exist_ok=True)
    path = waves_dir / f"leadlag_BINANCE_GMO_{asset}.csv"
    df.to_csv(path, index=False)
    return path


def plot_wave(df: pd.DataFrame, asset: str, graphs_dir: Path = _DEFAULT_GRAPHS_DIR) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    graphs_dir.mkdir(parents=True, exist_ok=True)
    df = df.sort_values("bucket")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax1.plot(df["bucket_start_jst"], df["frac_binance_leads"], color="tab:blue")
    ax1.set_ylabel("P(BINANCE leads)")
    ax1.set_ylim(0, 1)
    ax1.set_title(f"BINANCE→GMO {asset}: lead-lag daily wave")
    ax1.grid(alpha=0.3)

    ax2.plot(df["bucket_start_jst"], df["avg_opt_lag_ms"], color="tab:orange")
    ax2.set_ylabel("Avg optimal lag (ms)")
    ax2.set_xlabel("Time of day (JST)")
    ax2.set_xticks(ax2.get_xticks()[::4])
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    path = graphs_dir / f"leadlag_BINANCE_GMO_{asset}.png"
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
        "asset": row["asset"],
        "avg_corr_at_zero": float(row["avg_corr_at_zero"]),
        "avg_max_corr": float(row["avg_max_corr"]),
        "avg_opt_lag_ms": float(row["avg_opt_lag_ms"]),
        "avg_lag_stability": float(row["avg_lag_stability"]),
        "frac_binance_leads": float(row["frac_binance_leads"]),
    }


def _cmd_compute(args: argparse.Namespace) -> int:
    df = compute_wave(
        args.asset, window_seconds=args.window, span_seconds=args.span,
        bucket_minutes=args.bucket_minutes, start_date=args.start, end_date=args.end,
    )
    csv_path = write_wave(df, args.asset)
    print(f"Wrote {csv_path} ({len(df)} rows)")
    if args.plot:
        png_path = plot_wave(df, args.asset)
        print(f"Wrote {png_path}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    import json

    df = pd.read_csv(args.wave_csv)
    result = query_at(df, args.time, bucket_minutes=args.bucket_minutes)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BINANCE-leads-GMO lead-lag daily wave")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compute = sub.add_parser("compute", help="Compute a lead-lag wave from Makalu's LeadLag dataset")
    p_compute.add_argument("--asset", required=True, choices=sorted(_SUPPORTED_PAIRS))
    p_compute.add_argument("--window", type=int, default=_DEFAULT_WINDOW_SECONDS)
    p_compute.add_argument("--span", type=int, default=_DEFAULT_SPAN_SECONDS)
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
