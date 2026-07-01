#!/usr/bin/env python3
"""Populate horizon lookup tables for all markets.

Two outputs per market:
  1. Wave CSV (via volatility_wave.py):
       markets/waves/{exchange}_{symbol}/x{X}_y{Y}_z{Z}/{end_date}.csv
     Rows: (bucket, horizon_seconds, avg_spread, n, p_gt_1x, p_gt_2x, p_gt_3x, ...)

  2. Horizon lookup CSV:
       markets/horizons/{exchange}_{symbol}.csv
     One row per (bucket, k_value): the minimum viable horizon and the
     fraction of the bucket's reference horizon it represents, so the
     scanner can answer "what horizon should I use at 14:30 JST today?"

Usage:
    python tools/populate_horizon.py [--end YYYY-MM-DD] [--lookback-days N] [--dry-run]

The horizon lookup is derived from the wave by applying a viability threshold
(p_gt_kx >= P_THRESHOLD) per (bucket, horizon) and selecting the minimum
viable H at each k-level (1x = base cost, 2x = 2× cost / half-horizon, 3x).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
from volatility_wave import compute_wave, write_wave  # noqa: E402

# ── params ────────────────────────────────────────────────────────────────────

MARKETS = [
    ("GMO",     "BTC_JPY"),
    ("GMO",     "ETH_JPY"),
    ("GMO",     "XRP_JPY"),
    ("BINANCE", "BTC_USDT"),
    ("BINANCE", "ETH_USDT"),
    ("BINANCE", "XRP_USDT"),
]

# Wave computation params
X_SECONDS = 1       # resample granularity: raw ticks → 1s bars
Y_SECONDS = 60      # observation interval: one sample per minute
Z_SECONDS = 1800    # bucket width: 30-min JST buckets (48 per day)

CANDIDATE_HORIZONS = [15, 30, 60, 120, 300, 600]  # seconds
K_VALUES = [1, 2, 3]
LOOKBACK_DAYS = 60

# Horizon lookup params
P_THRESHOLD = 0.5   # min fraction of windows that must clear k× spread

HORIZONS_DIR = _ROOT / "markets" / "horizons"


def derive_horizon_lookup(wave_df: pd.DataFrame, p_threshold: float = P_THRESHOLD) -> pd.DataFrame:
    """Derive the horizon lookup table from a wave CSV.

    For each (bucket, k), finds:
      - min_viable_horizon_seconds: smallest H where p_gt_kx >= p_threshold
      - max_viable_horizon_seconds: largest H where p_gt_kx >= p_threshold
      - viable_horizons: comma-separated list of all viable H values

    k=1 → base horizon (min cost)
    k=2 → can use if vol is 2× spread (shorter horizon may work)
    k=3 → can use if vol is 3× spread (even shorter)

    The scanner interprets this as:
      During normal conditions, use h_1x (k=1 row).
      When p_gt_2x at a shorter H also clears threshold, h_2x ≤ h_1x,
      meaning vol is high enough relative to spread to use a shorter hold.
    """
    rows = []
    buckets = sorted(wave_df["bucket"].unique())
    for bucket in buckets:
        bdf = wave_df[wave_df["bucket"] == bucket].sort_values("horizon_seconds")
        bucket_start_jst = bdf.iloc[0]["bucket_start_jst"]
        avg_spread = bdf.iloc[0]["avg_spread"]

        for k in K_VALUES:
            col = f"p_gt_{k}x"
            if col not in bdf.columns:
                continue
            viable = bdf[bdf[col] >= p_threshold]
            viable_hs = sorted(viable["horizon_seconds"].tolist())

            rows.append({
                "bucket": int(bucket),
                "bucket_start_jst": bucket_start_jst,
                "k": k,
                "avg_spread": avg_spread,
                "min_viable_horizon_seconds": int(viable_hs[0]) if viable_hs else None,
                "max_viable_horizon_seconds": int(viable_hs[-1]) if viable_hs else None,
                "viable_horizons": ",".join(str(h) for h in viable_hs) if viable_hs else "",
                "p_threshold": p_threshold,
                "n_viable": len(viable_hs),
            })

    return pd.DataFrame(rows)


def populate_one(
    exchange: str,
    symbol: str,
    end_date: str | None,
    lookback_days: int,
    dry_run: bool,
) -> None:
    from datetime import date, timedelta
    end_dt = date.fromisoformat(end_date) if end_date else date.today()
    start_dt = end_dt - timedelta(days=lookback_days)
    start_str, end_str = str(start_dt), str(end_dt)

    print(f"\n{'─'*60}")
    print(f"  {exchange}/{symbol}  window={start_str}→{end_str}")
    print(f"{'─'*60}")

    if dry_run:
        print("  [dry-run] would compute wave and derive horizon lookup")
        return

    # 1. Compute wave
    print(f"  Computing wave (X={X_SECONDS}s Y={Y_SECONDS}s Z={Z_SECONDS}s) …")
    wave_df = compute_wave(
        exchange, symbol,
        x_seconds=X_SECONDS,
        y_seconds=Y_SECONDS,
        z_seconds=Z_SECONDS,
        horizons=CANDIDATE_HORIZONS,
        k_values=K_VALUES,
        start_date=start_str,
        end_date=end_str,
    )
    wave_path = write_wave(wave_df, exchange, symbol, X_SECONDS, Y_SECONDS, Z_SECONDS)
    print(f"  Wave: {wave_path}  ({len(wave_df)} rows)")

    # 2. Derive horizon lookup
    lookup_df = derive_horizon_lookup(wave_df, p_threshold=P_THRESHOLD)
    HORIZONS_DIR.mkdir(parents=True, exist_ok=True)
    lookup_path = HORIZONS_DIR / f"{exchange}_{symbol}.csv"
    lookup_df.to_csv(lookup_path, index=False)
    print(f"  Horizon lookup: {lookup_path}  ({len(lookup_df)} rows, {lookup_df['bucket'].nunique()} buckets)")

    # Summary: viable horizons at k=1 across JST day
    k1 = lookup_df[lookup_df["k"] == 1].copy()
    viable_count = k1["min_viable_horizon_seconds"].notna().sum()
    n_buckets = k1["bucket"].nunique()
    print(f"  k=1 viable buckets: {viable_count}/{n_buckets} "
          f"({100*viable_count/n_buckets:.0f}%)")
    if viable_count > 0:
        mn = k1["min_viable_horizon_seconds"].dropna().min()
        mx = k1["min_viable_horizon_seconds"].dropna().max()
        print(f"  k=1 min-viable-horizon range: {mn:.0f}s – {mx:.0f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Populate horizon lookup tables for all markets")
    parser.add_argument("--end", default=None, help="window end date YYYY-MM-DD (default: today)")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--markets", default=None,
                        help="comma-separated EXCHANGE:SYMBOL pairs to restrict (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    markets = MARKETS
    if args.markets:
        markets = [tuple(m.split(":")) for m in args.markets.split(",")]

    print(f"Populating horizon tables for {len(markets)} market(s)")
    print(f"Params: X={X_SECONDS}s Y={Y_SECONDS}s Z={Z_SECONDS}s "
          f"horizons={CANDIDATE_HORIZONS} k={K_VALUES} "
          f"p_threshold={P_THRESHOLD} lookback={args.lookback_days}d")

    for exchange, symbol in markets:
        try:
            populate_one(exchange, symbol, args.end, args.lookback_days, args.dry_run)
        except Exception as e:
            print(f"  ERROR {exchange}/{symbol}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
