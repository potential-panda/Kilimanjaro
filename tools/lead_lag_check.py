#!/usr/bin/env python3
"""Independent daily lead-lag check from silver MarketPrice.

For each day: resample BINANCE and GMO mid to a fixed grid (ffill), take log
returns, and compute plain Pearson correlation corr(gmo_t, binance_{t-k})
over a lag grid. Positive k = BINANCE leads. Deliberately different math from
Makalu's EWLL (no EW weighting, no windowing) so it can serve as an
independent cross-check of the gold LeadLag dataset.

Robustness options (see doc/lead-lag-2026-investigation.md for why):
  --winsorize   clip each day's returns at the 99.9% |r| quantile
  --medfilt N   centered rolling-median of N over the resampled mid before
                returns — fully removes the single-interval glitch spikes
                present in BINANCE ETH_USDT MarketPrice (degenerate bronze
                OrderBook snapshots); winsorizing alone is not enough in the
                worst months (2026-03/05/06, 56-73 glitches/day)

Usage
-----
    python tools/lead_lag_check.py --start 2025-07-01 --end 2026-06-29 \
        --out cycle/13_results/lead_lag/daily_raw.csv
    python tools/lead_lag_check.py --start 2025-07-01 --end 2026-06-29 \
        --winsorize --out cycle/13_results/lead_lag/daily_winsorized.csv
"""

from __future__ import annotations

import argparse
import datetime

import numpy as np
import pandas as pd

ROOT = "/panda/makalu-data/silver/dataset=MarketPrice"

MARKETS = {
    "ETH": (
        "exchange=BINANCE/symbol=ETH_USDT/execution_size=0.1",
        "exchange=GMO/symbol=ETH_JPY/execution_size=0.1",
    ),
    "XRP": (
        "exchange=BINANCE/symbol=XRP_USDT/execution_size=150",
        "exchange=GMO/symbol=XRP_JPY/execution_size=150",
    ),
}

GRIDS = {
    # name: (step_seconds, lags_in_steps)
    "100ms": (0.1, np.arange(-10, 31)),  # -1000ms .. +3000ms
    "1s": (1.0, np.arange(-10, 11)),  # -10s .. +10s
}


def _load_day(path: str, date: str) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(
            f"{ROOT}/{path}/date={date}/data.parquet",
            columns=["ts", "mid", "spread_bps"],
        )
    except FileNotFoundError:
        return None


def _grid_returns(
    df: pd.DataFrame, t0: float, t1: float, step: float, medfilt: int
) -> np.ndarray:
    grid = np.arange(t0, t1, step)
    idx = np.searchsorted(df["ts"].values, grid, side="right") - 1
    mid = np.where(idx >= 0, df["mid"].values[np.clip(idx, 0, None)], np.nan)
    if medfilt > 1:
        mid = (
            pd.Series(mid).rolling(medfilt, center=True, min_periods=1).median().values
        )
    return np.diff(np.log(mid))


def day_rows(market: str, date: str, winsorize: bool, medfilt: int) -> list[dict]:
    lead_path, lag_path = MARKETS[market]
    df_lead = _load_day(lead_path, date)
    df_lag = _load_day(lag_path, date)
    if df_lead is None or df_lag is None or len(df_lead) < 1000 or len(df_lag) < 1000:
        return []

    t0 = max(df_lead["ts"].iloc[0], df_lag["ts"].iloc[0])
    t1 = min(df_lead["ts"].iloc[-1], df_lag["ts"].iloc[-1])
    if t1 - t0 < 3600:
        return []

    rows = []
    for grid_name, (step, lags) in GRIDS.items():
        r_lead = _grid_returns(df_lead, t0, t1, step, medfilt)
        r_lag = _grid_returns(df_lag, t0, t1, step, medfilt)
        valid = ~(np.isnan(r_lead) | np.isnan(r_lag))
        r_lead, r_lag = r_lead[valid], r_lag[valid]
        n = len(r_lead)
        if n < 1000:
            continue

        if winsorize:
            for r in (r_lead, r_lag):
                q = np.quantile(np.abs(r), 0.999)
                np.clip(r, -q, q, out=r)

        corrs: dict[int, float] = {}
        for k in lags:
            if k >= 0:
                a, b = r_lag[k:], (r_lead[: n - k] if k else r_lead)
            else:
                a, b = r_lag[: n + k], r_lead[-k:]
            sa, sb = a.std(), b.std()
            corrs[int(k)] = (
                float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))
                if sa > 0 and sb > 0
                else 0.0
            )

        opt_k = max(corrs, key=lambda k: abs(corrs[k]))
        pos_max = max(v for k, v in corrs.items() if k > 0)
        neg_max = max(v for k, v in corrs.items() if k < 0)
        rows.append(
            {
                "market": market,
                "grid": grid_name,
                "date": date,
                "n_points": n,
                "corr0": round(corrs[0], 5),
                "max_corr": round(corrs[opt_k], 5),
                "opt_lag_ms": int(opt_k * step * 1000),
                "pos_edge": round(pos_max - neg_max, 5),
                "gmo_spread_bps_med": round(float(df_lag["spread_bps"].median()), 3),
                "binance_ticks": len(df_lead),
                "gmo_ticks": len(df_lag),
            }
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    ap.add_argument(
        "--markets", nargs="+", default=list(MARKETS), choices=list(MARKETS)
    )
    ap.add_argument(
        "--winsorize",
        action="store_true",
        help="Clip returns at the per-day 99.9%% |r| quantile",
    )
    ap.add_argument(
        "--medfilt",
        type=int,
        default=1,
        help="Centered rolling-median width on the resampled mid (1 = off)",
    )
    ap.add_argument("--out", required=True, help="Output CSV path")
    args = ap.parse_args()

    rows: list[dict] = []
    d = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    while d <= end:
        for market in args.markets:
            rows.extend(day_rows(market, d.isoformat(), args.winsorize, args.medfilt))
        d += datetime.timedelta(days=1)
        if d.day == 1:
            print(f"done through {d - datetime.timedelta(days=1)}", flush=True)

    pd.DataFrame(rows).to_csv(args.out, index=False)
    print(f"wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
