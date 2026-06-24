#!/usr/bin/env python3
"""Step 3 of the vol/spread + lead-lag wave-hypothesis project (Kilimanjaro
side): learns a simple, coarse trade/no-trade gate from Makalu's raw
VolSpread/LeadLag data -- never from Makalu's already-built ActivityCurve/
LeadLagCurve. Those are computed at a much finer (day_of_week x 5-min)
grain with a min-max normalization that's fragile to a single outlier bin
(confirmed for real on BITBANK XRP_SPOT: one anomalous 15-min window
flattened the entire rest of that curve to near-indistinguishable values).
Aggregating directly from raw data at this coarser (workday/holiday x hour)
grain pools far more samples per bucket, which is both simpler (per direct
instruction, starting with 2 day-types and hourly buckets rather than 7
and 5-min) and naturally more robust to that exact failure mode.

Makalu remains the data owner -- this only ever reads its already-
populated VolSpread/LeadLag parquet directly and never writes back to it.
The learning (grouping granularity, threshold) lives here, per direct
instruction: "build tools in Kilimanjaro that read the data from Makalu
and learn."

Day-type/hour bucketing uses JST, consistent with every other wave/regime
tool in this project (volatility_wave.py, lead_lag_wave.py, regime_window.py),
since the target markets (GMO, BITBANK) trade in JST.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

_MAKALU_DATA_ROOT = Path("/panda/makalu-data")
_JST_OFFSET_SECONDS = 9 * 3600

_VOLSPREAD_PARAM_IDS = ["vs_60_300_120_1s", "vs_60_900_300_1s", "vs_180_900_300_1s", "vs_300_900_300_1s"]


class ActivityGateError(Exception):
    pass


def _volspread_glob(exchange: str, symbol: str, execution_size: str, param_id: str) -> str:
    return str(
        _MAKALU_DATA_ROOT / "gold" / "dataset=VolSpread" / f"exchange={exchange}" / f"symbol={symbol}"
        / f"execution_size={execution_size}" / f"param_id={param_id}" / "date=*" / "*.parquet"
    )


def _leadlag_param_ids(market_a: str, market_b: str) -> list[str]:
    base = _MAKALU_DATA_ROOT / "gold" / "dataset=LeadLag" / f"market_a={market_a}" / f"market_b={market_b}"
    if not base.is_dir():
        return []
    return sorted(p.name.split("=", 1)[1] for p in base.glob("param_id=*") if p.is_dir())


def _leadlag_glob(market_a: str, market_b: str, param_id: str) -> str:
    return str(
        _MAKALU_DATA_ROOT / "gold" / "dataset=LeadLag" / f"market_a={market_a}" / f"market_b={market_b}"
        / f"param_id={param_id}" / "date=*" / "*.parquet"
    )


def _zscore_normalize(series: pd.Series) -> pd.Series | None:
    series = series.resample("1min").mean().dropna()
    if series.empty or series.std() == 0:
        return None
    return (series - series.mean()) / series.std()


def _day_type_and_hour(index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    jst = index + pd.Timedelta(seconds=_JST_OFFSET_SECONDS)
    is_holiday = jst.dayofweek.isin([5, 6])  # Saturday, Sunday in JST
    day_type = np.where(is_holiday, "holiday", "workday")
    return day_type, jst.hour.to_numpy()


def _aggregate_day_type_hour(series_list: list[pd.Series]) -> pd.DataFrame:
    """Pools every normalized sample across all input series into 2 x 24
    (day_type, hour) buckets and computes mean/std/count per bucket --
    coarser equivalent of weekly_curve.py's aggregate_weekly on the Makalu
    side, deliberately not min-max normalized (no single bucket's score
    is forced onto an absolute [0,1] scale relative to the week's most
    extreme bin, which is exactly what made the fine curve fragile)."""
    combined = pd.concat(series_list)
    day_type, hour = _day_type_and_hour(combined.index)
    df = pd.DataFrame({"day_type": day_type, "hour": hour, "value": combined.to_numpy()})
    grouped = df.groupby(["day_type", "hour"])["value"].agg(["mean", "std", "count"]).reset_index()
    return grouped.sort_values(["day_type", "hour"]).reset_index(drop=True)


def build_volspread_gate_table(
    exchange: str, symbol: str, execution_size: str, start_date: str, end_date: str,
    param_ids: list[str] = _VOLSPREAD_PARAM_IDS,
) -> pd.DataFrame:
    con = duckdb.connect()
    series_list = []
    for param_id in param_ids:
        glob = _volspread_glob(exchange, symbol, execution_size, param_id)
        try:
            df = con.sql(
                f"SELECT ts, volatility, spread_pct_ema FROM read_parquet('{glob}', hive_partitioning=true) "
                f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'"
            ).df()
        except duckdb.IOException:
            continue
        if df.empty:
            continue
        index = pd.to_datetime(df["ts"], unit="s", utc=True)
        for col in ("volatility", "spread_pct_ema"):
            z = _zscore_normalize(pd.Series(df[col].values, index=index))
            if z is not None:
                series_list.append(z)

    if not series_list:
        raise ActivityGateError(f"no VolSpread data found for {exchange}/{symbol} (execution_size={execution_size})")
    return _aggregate_day_type_hour(series_list)


def build_leadlag_gate_table(market_a: str, market_b: str, start_date: str, end_date: str) -> pd.DataFrame:
    con = duckdb.connect()
    series_list = []
    for param_id in _leadlag_param_ids(market_a, market_b):
        glob = _leadlag_glob(market_a, market_b, param_id)
        try:
            df = con.sql(
                f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
                f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'"
            ).df()
        except duckdb.IOException:
            continue
        if df.empty:
            continue
        index = pd.to_datetime(df["ts"], unit="s", utc=True)
        for col in [c for c in df.columns if c.startswith("ll_max_corr")]:
            z = _zscore_normalize(pd.Series(df[col].abs().values, index=index))
            if z is not None:
                series_list.append(z)

    if not series_list:
        raise ActivityGateError(f"no LeadLag data found for {market_a}/{market_b}")
    return _aggregate_day_type_hour(series_list)


def apply_threshold(gate_table: pd.DataFrame, percentile: float = 50.0) -> pd.DataFrame:
    """Marks each bucket 'active' if its mean exceeds the given percentile
    of all bucket means -- the simplest possible learned threshold, per
    direct instruction to start with a boolean trade/no-trade decision."""
    cutoff = np.percentile(gate_table["mean"], percentile)
    out = gate_table.copy()
    out["active"] = out["mean"] > cutoff
    out.attrs["threshold_percentile"] = percentile
    out.attrs["threshold_value"] = cutoff
    return out


def lookup(gate_table: pd.DataFrame, at_ts: float | None = None) -> dict:
    """The 'what does the gate say right now' query: resolves at_ts (or
    now) to (day_type, hour) in JST and returns that bucket's row."""
    ts = at_ts if at_ts is not None else datetime.now(timezone.utc).timestamp()
    jst = datetime.fromtimestamp(ts, tz=timezone.utc) + pd.Timedelta(seconds=_JST_OFFSET_SECONDS)
    day_type = "holiday" if jst.weekday() >= 5 else "workday"
    hour = jst.hour

    row = gate_table[(gate_table["day_type"] == day_type) & (gate_table["hour"] == hour)]
    if row.empty:
        raise ActivityGateError(f"no bucket found for day_type={day_type} hour={hour}")
    result = row.iloc[0].to_dict()
    result["jst_time"] = jst.strftime("%Y-%m-%d %H:%M JST")
    return result


def combined_decision(volspread_row: dict, leadlag_row: dict) -> bool:
    """Trade only when BOTH metrics' buckets are active -- "we only want
    to test/trade when the 2 metrics are high", per direct instruction."""
    return bool(volspread_row.get("active")) and bool(leadlag_row.get("active"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Learn a workday/holiday x hour trade/no-trade gate from Makalu's raw VolSpread/LeadLag")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Build and print the gate tables for a market pair")
    p_build.add_argument("--exchange", required=True)
    p_build.add_argument("--symbol", required=True)
    p_build.add_argument("--execution-size", required=True)
    p_build.add_argument("--lead-exchange", default=None)
    p_build.add_argument("--lead-symbol", default=None)
    p_build.add_argument("--start", required=True)
    p_build.add_argument("--end", required=True)
    p_build.add_argument("--percentile", type=float, default=50.0)
    p_build.add_argument("--out-dir", default=None, help="If set, write the gate tables as parquet here")

    p_check = sub.add_parser("check", help="Look up the gate's current decision from saved gate tables")
    p_check.add_argument("--volspread-table", required=True)
    p_check.add_argument("--leadlag-table", required=True)

    args = parser.parse_args()

    if args.command == "build":
        vs_table = apply_threshold(
            build_volspread_gate_table(args.exchange, args.symbol, args.execution_size, args.start, args.end),
            args.percentile,
        )
        print("VolSpread gate table:")
        print(vs_table.to_string(index=False))

        if args.lead_exchange and args.lead_symbol:
            market_a = f"{args.lead_exchange}:{args.lead_symbol}"
            market_b = f"{args.exchange}:{args.symbol}"
            ll_table = apply_threshold(build_leadlag_gate_table(market_a, market_b, args.start, args.end), args.percentile)
            print("\nLeadLag gate table:")
            print(ll_table.to_string(index=False))
        else:
            ll_table = None

        if args.out_dir:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            vs_path = out_dir / f"volspread_gate_{args.exchange}_{args.symbol}.parquet"
            vs_table.to_parquet(vs_path, index=False)
            print(f"\nWrote {vs_path}")
            if ll_table is not None:
                ll_path = out_dir / f"leadlag_gate_{args.lead_exchange}_{args.lead_symbol}_{args.exchange}_{args.symbol}.parquet"
                ll_table.to_parquet(ll_path, index=False)
                print(f"Wrote {ll_path}")
    else:
        vs_table = pd.read_parquet(args.volspread_table)
        ll_table = pd.read_parquet(args.leadlag_table)
        vs_row = lookup(vs_table)
        ll_row = lookup(ll_table)
        decision = combined_decision(vs_row, ll_row)
        print(f"As of {vs_row['jst_time']} ({vs_row['day_type']}, hour {vs_row['hour']}):")
        print(f"  VolSpread: active={vs_row['active']} mean={vs_row['mean']:.4f}")
        print(f"  LeadLag:   active={ll_row['active']} mean={ll_row['mean']:.4f}")
        print(f"  Decision: {'TRADE' if decision else 'NO-TRADE'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
