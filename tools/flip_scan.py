#!/usr/bin/env python3
"""Flip-anchored scan for event-driven signals like orion's Parabolic SAR.

Unlike scanner.py (which joins a continuous signal against every OrderBook
observation), this tool anchors on FLIP events — one row per trade decision.
That matches how orion actually works: enter at the flip, hold until the next
flip.

Two return definitions are computed for each flip:
  - fixed-horizon: signed mid return at H seconds after the flip
  - between-flip:  signed mid return from this flip to the next flip
    (the true holding-period return for a strategy that reverses on every flip)

Both are expressed in bps and compared against a configurable round-trip cost.

## Output field definitions

All return fields are signed by signal direction: sar_up=+1 (expect up) or
sar_up=-1 (expect down). A positive signed return means the signal was correct.
1 bps = 0.01% = 1/10000 of price.

Top-level:
  n_flips              Total number of SAR flip events in the window.
  flips_per_day        n_flips / calendar days in window.
  costs_bps            Round-trip cost assumption (fee + slippage + half-spread,
                       both entry and exit). Default 5 bps for GMO margin:
                         fee 0 bps + slippage ~0.5+0.5 bps + half-spread ~2+2 bps.

fixed_horizon[] — one entry per horizon H:
  horizon_seconds      H in seconds (e.g. 30, 120, 300, 600).
  n                    Number of flips with a valid exit price at ts+H.
  avg_signed_return_bps  Mean of (sar_up × mid_return) across all flips.
                         Positive = signal was right on average.
  avg_net_return_bps   avg_signed_return_bps − costs_bps. What you keep after friction.
  hit_rate             Fraction of flips where signed return > 0 (right before costs).
  hit_rate_net         Fraction of flips where signed return > costs_bps (profitable after costs).
  median_signed_return_bps  Median signed return — less distorted by outliers than mean.
  p25_bps / p75_bps    25th / 75th percentile of signed returns.
  std_bps              Standard deviation of signed returns per flip.

between_flip — hold from this flip until the next SAR cross (orion's actual holding period):
  n                    Number of flips with a valid next flip (last flip excluded).
  avg_holding_seconds  Mean time between consecutive flips.
  median_holding_seconds  Median holding period (more robust; mean is skewed by long trends).
  avg_signed_return_bps  Mean signed return over the holding period.
  avg_net_return_bps   avg_signed_return_bps − costs_bps.
  hit_rate             Fraction of flips where the holding-period return > 0.
  hit_rate_net         Fraction of flips where holding-period return > costs_bps.
  median_signed_return_bps  Median signed return over holding period.
  p25_bps / p75_bps    25th / 75th percentile.
  std_bps              Standard deviation.

by_month[] — between-flip stats grouped by calendar month (YYYY-MM):
  month                Calendar month.
  n_flips              Number of flips originating in this month.
  avg_signed_return_bps / avg_net_return_bps / hit_rate  Same as between_flip above.

by_jst_hour[] — between-flip stats grouped by JST hour of the flip:
  jst_hour             0–23, JST (UTC+9).
  n_flips / avg_signed_return_bps / avg_net_return_bps / hit_rate  Same as above.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_FEATURES_ROOT = _ROOT / "markets" / "features"
_DEFAULT_SCAN_REPORTS_DIR = _ROOT / "scan-reports"

_DEFAULT_HORIZONS = [30, 120, 300, 600]
_DEFAULT_COSTS_BPS = 5.0  # round-trip: ~1 bps slippage + ~4 bps half-spread x2


def _flip_glob(flip_exchange: str, flip_symbol: str, features_root: Path) -> str:
    return str(
        features_root / "dataset=ChartRangeSAR"
        / f"exchange={flip_exchange}" / f"symbol={flip_symbol}"
        / "date=*" / "*.parquet"
    )


def _ob_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "bronze" / "dataset=OrderBook"
        / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet"
    )


def run_flip_scan(
    flip_exchange: str,
    flip_symbol: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    horizons: list[int] = _DEFAULT_HORIZONS,
    costs_bps: float = _DEFAULT_COSTS_BPS,
    data_root: Path = _DEFAULT_DATA_ROOT,
    features_root: Path = _DEFAULT_FEATURES_ROOT,
) -> dict:
    """Run a flip-anchored scan.

    Returns a report dict with per-flip statistics before and after costs,
    for both fixed-horizon and between-flip return definitions.
    """
    flip_glob = _flip_glob(flip_exchange, flip_symbol, features_root)
    ob_glob   = _ob_glob(target_exchange, target_symbol, data_root)
    con = duckdb.connect()

    # ── Step 1: load all flips with entry mid (GMO mid at flip timestamp) ────
    flips_df = con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        ob AS (
            SELECT ts, (bid0_price + ask0_price) / 2.0 AS mid
            FROM read_parquet('{ob_glob}', hive_partitioning=true)
            WHERE ask0_price > bid0_price
              AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT f.ts, f.sar_up, o.mid AS entry_mid
        FROM flips f ASOF JOIN ob o ON o.ts >= f.ts
        ORDER BY f.ts
    """).df().dropna()

    if flips_df.empty:
        raise ValueError(f"no flip data for {flip_exchange}/{flip_symbol} in {start_date}..{end_date}")

    n_flips = len(flips_df)
    n_days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days

    # ── Step 2: fixed-horizon returns ─────────────────────────────────────────
    horizon_results = []
    for h in horizons:
        exit_df = con.sql(f"""
            WITH flips AS (
                SELECT ts FROM read_parquet('{flip_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ),
            ob AS (
                SELECT ts, (bid0_price + ask0_price) / 2.0 AS mid
                FROM read_parquet('{ob_glob}', hive_partitioning=true)
                WHERE ask0_price > bid0_price
                  AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'
                    + INTERVAL {max(horizons) // 3600 + 1} DAY
            )
            SELECT f.ts, o.mid AS exit_mid
            FROM flips f ASOF JOIN ob o ON o.ts >= f.ts + {h}
            ORDER BY f.ts
        """).df().dropna()

        merged = flips_df.merge(exit_df, on="ts", how="inner")
        raw_ret = (merged["exit_mid"] - merged["entry_mid"]) / merged["entry_mid"] * 10000
        signed  = merged["sar_up"] * raw_ret

        horizon_results.append({
            "horizon_seconds": h,
            "n": len(signed),
            "avg_signed_return_bps":     float(signed.mean()),
            "avg_net_return_bps":        float(signed.mean() - costs_bps),
            "hit_rate":                  float((signed > 0).mean()),
            "hit_rate_net":              float((signed > costs_bps).mean()),
            "median_signed_return_bps":  float(signed.median()),
            "p25_bps":                   float(signed.quantile(0.25)),
            "p75_bps":                   float(signed.quantile(0.75)),
            "std_bps":                   float(signed.std()),
        })

    # ── Step 3: between-flip return ───────────────────────────────────────────
    flips_df = flips_df.reset_index(drop=True)
    flips_df["next_ts"]  = flips_df["ts"].shift(-1)
    flips_df["next_mid"] = flips_df["entry_mid"].shift(-1)

    bf = flips_df.dropna(subset=["next_ts", "next_mid"]).copy()
    bf["holding_seconds"] = bf["next_ts"] - bf["ts"]
    raw_bf  = (bf["next_mid"] - bf["entry_mid"]) / bf["entry_mid"] * 10000
    signed_bf = bf["sar_up"] * raw_bf

    between_flip = {
        "n": len(signed_bf),
        "avg_holding_seconds":       float(bf["holding_seconds"].mean()),
        "median_holding_seconds":    float(bf["holding_seconds"].median()),
        "avg_signed_return_bps":     float(signed_bf.mean()),
        "avg_net_return_bps":        float(signed_bf.mean() - costs_bps),
        "hit_rate":                  float((signed_bf > 0).mean()),
        "hit_rate_net":              float((signed_bf > costs_bps).mean()),
        "median_signed_return_bps":  float(signed_bf.median()),
        "p25_bps":                   float(signed_bf.quantile(0.25)),
        "p75_bps":                   float(signed_bf.quantile(0.75)),
        "std_bps":                   float(signed_bf.std()),
    }

    # ── Step 4: monthly breakdown (between-flip) ──────────────────────────────
    bf["month"] = pd.to_datetime(bf["ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    monthly = []
    for month, g in bf.groupby("month"):
        sr = bf["sar_up"].loc[g.index] * (g["next_mid"] - g["entry_mid"]) / g["entry_mid"] * 10000
        monthly.append({
            "month": month,
            "n_flips": len(g),
            "avg_signed_return_bps": float(sr.mean()),
            "avg_net_return_bps":    float(sr.mean() - costs_bps),
            "hit_rate":              float((sr > 0).mean()),
        })

    # ── Step 5: time-of-day breakdown (between-flip, JST buckets) ────────────
    _JST = 9 * 3600
    bf["jst_hour"] = ((bf["ts"] + _JST) % 86400 // 3600).astype(int)
    by_hour = []
    for hour, g in bf.groupby("jst_hour"):
        sr = bf["sar_up"].loc[g.index] * (g["next_mid"] - g["entry_mid"]) / g["entry_mid"] * 10000
        by_hour.append({
            "jst_hour": int(hour),
            "n_flips": len(g),
            "avg_signed_return_bps": float(sr.mean()),
            "avg_net_return_bps":    float(sr.mean() - costs_bps),
            "hit_rate":              float((sr > 0).mean()),
        })

    return {
        "flip_exchange":   flip_exchange,
        "flip_symbol":     flip_symbol,
        "target_exchange": target_exchange,
        "target_symbol":   target_symbol,
        "start_date":      start_date,
        "end_date":        end_date,
        "costs_bps":       costs_bps,
        "n_flips":         n_flips,
        "flips_per_day":   round(n_flips / n_days, 2),
        "fixed_horizon":   horizon_results,
        "between_flip":    between_flip,
        "by_month":        monthly,
        "by_jst_hour":     by_hour,
    }


def print_summary(report: dict) -> None:
    costs = report["costs_bps"]
    print(f"\n{'='*60}")
    print(f"FLIP SCAN: {report['flip_exchange']}/{report['flip_symbol']} → {report['target_exchange']}/{report['target_symbol']}")
    print(f"Window: {report['start_date']} .. {report['end_date']}")
    print(f"N flips: {report['n_flips']}  ({report['flips_per_day']:.1f}/day)")
    print(f"Round-trip cost assumption: {costs} bps")

    print(f"\n--- Fixed-horizon returns (signed, per flip) ---")
    print(f"{'Horizon':>10}  {'Avg gross':>10}  {'Avg net':>10}  {'Hit%':>7}  {'Hit% net':>9}")
    for r in report["fixed_horizon"]:
        print(f"{r['horizon_seconds']:>9}s  {r['avg_signed_return_bps']:>+9.2f}  "
              f"{r['avg_net_return_bps']:>+9.2f}  {r['hit_rate']:>6.1%}  {r['hit_rate_net']:>8.1%}")

    bf = report["between_flip"]
    print(f"\n--- Between-flip returns (hold until next SAR cross) ---")
    print(f"  Avg holding:   {bf['avg_holding_seconds']:.0f}s  (median {bf['median_holding_seconds']:.0f}s)")
    print(f"  Avg gross:     {bf['avg_signed_return_bps']:+.2f} bps")
    print(f"  Avg net:       {bf['avg_net_return_bps']:+.2f} bps  (after {costs} bps cost)")
    print(f"  Hit rate:      {bf['hit_rate']:.1%} gross  /  {bf['hit_rate_net']:.1%} net")
    print(f"  p25/med/p75:   {bf['p25_bps']:+.1f} / {bf['median_signed_return_bps']:+.1f} / {bf['p75_bps']:+.1f} bps")

    print(f"\n--- Monthly breakdown (between-flip, net of {costs} bps) ---")
    print(f"{'Month':>8}  {'N flips':>8}  {'Avg gross':>10}  {'Avg net':>10}  {'Hit%':>7}")
    for m in report["by_month"]:
        print(f"{m['month']:>8}  {m['n_flips']:>8}  {m['avg_signed_return_bps']:>+9.2f}  "
              f"{m['avg_net_return_bps']:>+9.2f}  {m['hit_rate']:>6.1%}")
    print()


def _cmd_scan(args: argparse.Namespace) -> int:
    report = run_flip_scan(
        flip_exchange=args.flip_exchange,
        flip_symbol=args.flip_symbol,
        target_exchange=args.target_exchange,
        target_symbol=args.target_symbol,
        start_date=args.start,
        end_date=args.end,
        horizons=[int(h) for h in args.horizons.split(",")],
        costs_bps=args.costs_bps,
    )
    print_summary(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"Wrote {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Flip-anchored scan for event-driven signals")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="Run a flip-anchored scan")
    p.add_argument("--flip-exchange",   required=True, help="Exchange that generates flips (e.g. BINANCE)")
    p.add_argument("--flip-symbol",     required=True, help="Symbol for flips (e.g. XRP_USDT)")
    p.add_argument("--target-exchange", required=True, help="Exchange to measure returns on (e.g. GMO)")
    p.add_argument("--target-symbol",   required=True, help="Symbol to measure returns on (e.g. XRP_JPY)")
    p.add_argument("--start",           required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end",             required=True, help="End date YYYY-MM-DD")
    p.add_argument("--horizons",        default=",".join(str(h) for h in _DEFAULT_HORIZONS))
    p.add_argument("--costs-bps",       type=float, default=_DEFAULT_COSTS_BPS)
    p.add_argument("--out",             default=None, help="Write JSON report to this path")
    p.set_defaults(func=_cmd_scan)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
