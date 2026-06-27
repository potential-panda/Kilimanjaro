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
  costs_bps            Residual round-trip cost not captured by bid/ask prices.
                       Default 1 bps (slippage only: ~0.5 bps entry + ~0.5 bps exit).
                       Spread is already baked into returns via actual bid/ask execution
                       prices (BUY enters at ask, exits at bid; SELL enters at bid, exits
                       at ask), so only slippage remains here. Fee is 0 for GMO margin.

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
_DEFAULT_COSTS_BPS = 1.0  # round-trip slippage only (~0.5 bps each way);
                          # spread is already baked in via bid/ask execution prices


def _flip_glob(flip_exchange: str, flip_symbol: str, box_size_rate: float, features_root: Path) -> str:
    return str(
        features_root / "dataset=ChartRangeSAR"
        / f"exchange={flip_exchange}" / f"symbol={flip_symbol}"
        / f"box_size_rate={box_size_rate}"
        / "date=*" / "*.parquet"
    )


def _mp_glob(exchange: str, symbol: str, execution_size: str, data_root: Path) -> str:
    return str(
        data_root / "silver" / "dataset=MarketPrice"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / f"execution_size={execution_size}" / "date=*" / "*.parquet"
    )


def _trade_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "bronze" / "dataset=Trade"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / "date=*" / "*.parquet"
    )


def run_flip_scan(
    flip_exchange: str,
    flip_symbol: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    box_size_rate: float = 0.0025,
    execution_size: str = "150",
    horizons: list[int] = _DEFAULT_HORIZONS,
    costs_bps: float = _DEFAULT_COSTS_BPS,
    max_spread_bps: float | None = None,
    max_trades_30s: int | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    features_root: Path = _DEFAULT_FEATURES_ROOT,
) -> dict:
    """Run a flip-anchored scan.

    Returns a report dict with per-flip statistics before and after costs,
    for both fixed-horizon and between-flip return definitions.
    """
    flip_glob  = _flip_glob(flip_exchange, flip_symbol, box_size_rate, features_root)
    mp_glob    = _mp_glob(target_exchange, target_symbol, execution_size, data_root)
    trade_glob = _trade_glob(flip_exchange, flip_symbol, data_root)
    con = duckdb.connect()

    # ── Step 1: load all flips with actual entry execution price ─────────────
    # Uses silver MarketPrice: bid/ask are VWAP at execution_size depth (more
    # realistic than best bid0/ask0), and spread > 10 bps rows are pre-filtered.
    # BUY (sar_up=+1): enter at ask  SELL (sar_up=-1): enter at bid
    flips_df = con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT f.ts, f.sar_up,
            CASE WHEN f.sar_up > 0 THEN o.ask ELSE o.bid END AS entry_price,
            (o.bid + o.ask) / 2.0 AS entry_mid,
            o.bid AS entry_bid,
            o.ask AS entry_ask
        FROM flips f ASOF JOIN mp o ON o.ts >= f.ts
        ORDER BY f.ts
    """).df().dropna()

    if flips_df.empty:
        raise ValueError(f"no flip data for {flip_exchange}/{flip_symbol} in {start_date}..{end_date}")

    # ── Risk gates ────────────────────────────────────────────────────────────
    flips_df["entry_spread_bps"] = (
        (flips_df["entry_ask"] - flips_df["entry_bid"]) / flips_df["entry_bid"] * 10_000
    )
    n_spread_blocked = 0
    n_rate_blocked   = 0

    if max_spread_bps is not None:
        spread_mask = flips_df["entry_spread_bps"] >= max_spread_bps
        n_spread_blocked = int(spread_mask.sum())
        flips_df = flips_df[~spread_mask].copy()

    if max_trades_30s is not None and not flips_df.empty:
        con.register("_flips_for_rate", flips_df[["ts"]])
        rate_df = con.sql(f"""
            WITH seconds AS (
                SELECT floor(ts) AS sec, count(*) AS n
                FROM read_parquet('{trade_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
                GROUP BY 1
            ),
            rolling AS (
                SELECT sec,
                    SUM(n) OVER (ORDER BY sec RANGE BETWEEN 30.0 PRECEDING AND CURRENT ROW)
                    AS trades_30s
                FROM seconds
            )
            SELECT f.ts, COALESCE(r.trades_30s, 0) AS trades_30s
            FROM _flips_for_rate f
            LEFT JOIN rolling r ON r.sec = floor(f.ts)
        """).df()
        flips_df = flips_df.merge(rate_df, on="ts", how="left")
        rate_mask = flips_df["trades_30s"] >= max_trades_30s
        n_rate_blocked = int(rate_mask.sum())
        flips_df = flips_df[~rate_mask].copy()

    n_flips_raw = len(flips_df) + n_spread_blocked + n_rate_blocked
    n_flips = len(flips_df)
    n_days  = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days

    # ── Step 2: fixed-horizon returns ─────────────────────────────────────────
    # Exit execution price is the opposite side: long exits at bid, short at ask
    horizon_results = []
    for h in horizons:
        exit_df = con.sql(f"""
            WITH flips AS (
                SELECT ts, sar_up FROM read_parquet('{flip_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ),
            mp AS (
                SELECT ts, bid, ask
                FROM read_parquet('{mp_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
                    + INTERVAL {max(horizons) // 3600 + 1} DAY
            )
            SELECT f.ts,
                CASE WHEN f.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
            FROM flips f ASOF JOIN mp o ON o.ts >= f.ts + {h}
            ORDER BY f.ts
        """).df().dropna()

        merged = flips_df.merge(exit_df, on="ts", how="inner")
        raw_ret = (merged["exit_price"] - merged["entry_price"]) / merged["entry_price"] * 10000
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
    # Exit at the next flip: long exits at next flip's bid, short at next flip's ask.
    flips_df = flips_df.reset_index(drop=True)
    flips_df["next_ts"] = flips_df["ts"].shift(-1)

    # Build a pairs table: (ts, sar_up, next_ts) then ASOF JOIN OB at next_ts.
    # This avoids shift-based misalignment when some rows have no OB match.
    pairs = flips_df[["ts", "sar_up", "next_ts"]].dropna(subset=["next_ts"]).copy()
    con.register("_pairs", pairs)
    exit_ob = con.sql(f"""
        WITH mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT p.ts,
            CASE WHEN p.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
        FROM _pairs p ASOF JOIN mp o ON o.ts >= p.next_ts
    """).df().dropna()

    bf = (
        flips_df.merge(exit_ob, on="ts", how="inner")
    )
    bf["holding_seconds"] = bf["next_ts"] - bf["ts"]
    raw_bf    = (bf["exit_price"] - bf["entry_price"]) / bf["entry_price"] * 10000
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
        sr = g["sar_up"] * (g["exit_price"] - g["entry_price"]) / g["entry_price"] * 10000
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
        sr = g["sar_up"] * (g["exit_price"] - g["entry_price"]) / g["entry_price"] * 10000
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
        "box_size_rate":   box_size_rate,
        "execution_size":  execution_size,
        "risk_gates": {
            "max_spread_bps":   max_spread_bps,
            "max_trades_30s":   max_trades_30s,
            "n_spread_blocked": n_spread_blocked,
            "n_rate_blocked":   n_rate_blocked,
            "n_raw_flips":      n_flips_raw,
        },
        "fixed_horizon":   horizon_results,
        "between_flip":    between_flip,
        "by_month":        monthly,
        "by_jst_hour":     by_hour,
    }


def print_summary(report: dict) -> None:
    costs = report["costs_bps"]
    print(f"\n{'='*60}")
    print(f"FLIP SCAN: {report['flip_exchange']}/{report['flip_symbol']} → {report['target_exchange']}/{report['target_symbol']}")
    print(f"Window: {report['start_date']} .. {report['end_date']}  box_size_rate={report['box_size_rate']}  execution_size={report['execution_size']}")
    rg = report["risk_gates"]
    gates_active = rg["max_spread_bps"] is not None or rg["max_trades_30s"] is not None
    if gates_active:
        print(f"Risk gates: spread<{rg['max_spread_bps']} bps / trades_30s<{rg['max_trades_30s']}")
        print(f"  Blocked: {rg['n_spread_blocked']} spread + {rg['n_rate_blocked']} rate "
              f"= {rg['n_spread_blocked']+rg['n_rate_blocked']} of {rg['n_raw_flips']} raw flips")
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
        box_size_rate=args.box_size_rate,
        execution_size=args.execution_size,
        horizons=[int(h) for h in args.horizons.split(",")],
        costs_bps=args.costs_bps,
        max_spread_bps=args.max_spread_bps or None,
        max_trades_30s=args.max_trades_30s or None,
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
    p.add_argument("--box-size-rate",    type=float, default=0.0025, help="Range bar size rate (e.g. 0.0025)")
    p.add_argument("--execution-size",   default="150", help="MarketPrice execution size partition (e.g. 150 for XRP_JPY, 0.1 for ETH_JPY, 0.002 for BTC_JPY)")
    p.add_argument("--horizons",        default=",".join(str(h) for h in _DEFAULT_HORIZONS))
    p.add_argument("--costs-bps",       type=float, default=_DEFAULT_COSTS_BPS)
    p.add_argument("--max-spread-bps",  type=float, default=6.0,
                   help="Skip flips where GMO spread >= this (bps). 0 to disable.")
    p.add_argument("--max-trades-30s",  type=int,   default=1000,
                   help="Skip flips where BINANCE trades in prior 30s >= this. 0 to disable.")
    p.add_argument("--out",             default=None, help="Write JSON report to this path")
    p.set_defaults(func=_cmd_scan)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
