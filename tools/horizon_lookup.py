#!/usr/bin/env python3
"""Horizon lookup utility — shared by scanner.py and param_scan.py.

Loads the pre-computed horizon tables from markets/horizons/ and answers:
  - What is the minimum viable horizon for a given (market, JST bucket)?
  - Which of a candidate set of horizons are viable (and at what k-level)?
  - What fraction of the scan window's JST buckets are viable at each horizon?

All functions are read-only against the CSV files written by populate_horizon.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
_HORIZONS_DIR = _ROOT / "markets" / "horizons"
_JST_SECONDS = 9 * 3600
_Z_SECONDS = 1800  # must match populate_horizon.py Z_SECONDS


def load_lookup(exchange: str, symbol: str, horizons_dir: Path = _HORIZONS_DIR) -> pd.DataFrame | None:
    """Load the horizon lookup CSV for (exchange, symbol). Returns None if not found."""
    path = horizons_dir / f"{exchange}_{symbol}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


def _jst_bucket(unix_ts: float, z_seconds: int = _Z_SECONDS) -> int:
    """Return the JST time-of-day bucket index for a Unix timestamp."""
    jst_sec = int(unix_ts + _JST_SECONDS) % 86400
    return (jst_sec + z_seconds // 2) % 86400 // z_seconds


def viable_horizons_for_bucket(
    lookup_df: pd.DataFrame,
    bucket: int,
    k: int = 1,
) -> list[int]:
    """Return sorted list of viable horizon values for (bucket, k).

    A horizon is viable if it appears in the lookup's viable_horizons column
    for this bucket and k. Empty list if no viable horizons exist.
    """
    row = lookup_df[(lookup_df["bucket"] == bucket) & (lookup_df["k"] == k)]
    if row.empty or pd.isna(row.iloc[0]["viable_horizons"]) or row.iloc[0]["viable_horizons"] == "":
        return []
    return [int(h) for h in str(row.iloc[0]["viable_horizons"]).split(",")]


def min_viable_horizon(
    lookup_df: pd.DataFrame,
    bucket: int,
    k: int = 1,
) -> int | None:
    """Return the minimum viable horizon for (bucket, k), or None if none exists."""
    row = lookup_df[(lookup_df["bucket"] == bucket) & (lookup_df["k"] == k)]
    if row.empty:
        return None
    val = row.iloc[0]["min_viable_horizon_seconds"]
    return int(val) if pd.notna(val) else None


def horizon_viability_summary(
    lookup_df: pd.DataFrame,
    candidate_horizons: list[int],
    k: int = 1,
) -> dict[int, float]:
    """For each candidate horizon, return the fraction of JST buckets where it is viable.

    Returns: {horizon_seconds: pct_buckets_viable} for each candidate horizon.
    A horizon that clears the spread in 80% of JST buckets is broadly viable;
    one that only clears in 30% is only useful during specific time windows.
    """
    buckets = sorted(lookup_df["bucket"].unique())
    n_buckets = len(buckets)
    if n_buckets == 0:
        return {h: 0.0 for h in candidate_horizons}

    result = {}
    for h in candidate_horizons:
        viable_count = 0
        for b in buckets:
            vh = viable_horizons_for_bucket(lookup_df, b, k=k)
            if h in vh:
                viable_count += 1
        result[h] = viable_count / n_buckets
    return result


def annotate_timestamps(
    timestamps: "pd.Series",
    lookup_df: pd.DataFrame,
    candidate_horizons: list[int],
    k: int = 1,
) -> "pd.DataFrame":
    """For a Series of Unix timestamps, return a DataFrame indicating which
    horizons are viable at each timestamp's JST bucket.

    Returns DataFrame with columns: ts, bucket, viable_H_{h} for each candidate H.
    Use this to filter per-observation to only compute IC/hit_rate on observations
    where the horizon can actually clear the spread.
    """
    import numpy as np

    buckets = ((timestamps.values + _JST_SECONDS) % 86400 + _Z_SECONDS // 2) % 86400 // _Z_SECONDS

    # Pre-build bucket → set of viable horizons
    bucket_viable: dict[int, set] = {}
    for b in sorted(lookup_df["bucket"].unique()):
        vh = viable_horizons_for_bucket(lookup_df, int(b), k=k)
        bucket_viable[int(b)] = set(vh)

    result = {"ts": timestamps.values, "bucket": buckets}
    for h in candidate_horizons:
        result[f"viable_{h}s"] = np.array([h in bucket_viable.get(int(b), set()) for b in buckets])
    return pd.DataFrame(result)


def print_viability_table(
    exchange: str,
    symbol: str,
    candidate_horizons: list[int] | None = None,
    k: int = 1,
    horizons_dir: Path = _HORIZONS_DIR,
) -> None:
    """CLI helper: print a summary table of horizon viability for a market."""
    df = load_lookup(exchange, symbol, horizons_dir)
    if df is None:
        print(f"No horizon table for {exchange}/{symbol} in {horizons_dir}")
        return
    if candidate_horizons is None:
        all_h: set[int] = set()
        for vh_str in df[df["k"] == k]["viable_horizons"].dropna():
            for h in str(vh_str).split(","):
                if h:
                    all_h.add(int(h))
        candidate_horizons = sorted(all_h)

    summary = horizon_viability_summary(df, candidate_horizons, k=k)
    print(f"\n{exchange}/{symbol}  k={k}  (fraction of 30-min JST buckets where horizon is viable)")
    print(f"{'Horizon':>10}  {'% buckets viable':>18}  {'Recommended':>12}")
    for h in candidate_horizons:
        pct = summary[h]
        rec = "<-- use" if pct >= 0.75 else ("(partial)" if pct >= 0.25 else "")
        print(f"{h:>9}s  {pct:>17.1%}  {rec}")


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Horizon viability lookup")
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--horizons", default="15,30,60,120,300,600",
                        help="Comma-separated candidate horizons to check")
    parser.add_argument("--k", type=int, default=1,
                        help="k-value: 1=base cost, 2=2x spread, 3=3x spread")
    args = parser.parse_args()
    candidate_horizons = [int(h) for h in args.horizons.split(",")]
    print_viability_table(args.exchange, args.symbol, candidate_horizons, k=args.k)
    sys.exit(0)
