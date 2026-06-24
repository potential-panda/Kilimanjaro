#!/usr/bin/env python3
"""Precomputed Parabolic SAR signal over Makalu's Chart_RANGE range bars,
reusing orion's real, already-live ParabolicSarIndicator (the same v601
variant trading GMO BTC_JPY/ETH_JPY/XRP_JPY in production) rather than
reimplementing the indicator.

This writes a Kilimanjaro-owned precomputed feature dataset under
markets/features/ -- never into Makalu's own data root, since this is a
hypothesis-specific derived signal, not a reusable Makalu warehouse
dataset (Makalu owns Chart_RANGE itself; this only reads it). scanner.py
reads it the same way it reads bronze OrderBook for any other feature:
via a trivial feature.sql_expression that just selects the precomputed
column, since the actual SAR computation is inherently stateful (each
bar's SAR depends on the previous bar's trend/extreme/acceleration) and
cannot be expressed as the stateless SQL the scanner otherwise evaluates.

v2: emits one row per FLIP, timestamped at the exact trade tick where
price first crosses the SAR stop -- not at the next bar's close. This
matters: orion's real live logic (template.py::place_orders) checks every
trade tick against the SAR level computed as of the *last closed* bar
(self.sar.ns), not the bar that's still forming. A bar can take an
arbitrary amount of time to close; sampling sar_up only at bar-close (v1,
since corrected) is systematically late by however long that takes after
the real crossing already happened -- the flip is the edge, and it
happens inside the still-open bar, not at the next close.

orion's self.sar.up/.ns themselves are recomputed only on bar close (this
matches that exactly); what changes here is the separately-tracked
position-side state (matching self.parabolic.side, not self.sar.up) that
determines whether a given tick is a genuinely *new* flip worth emitting,
versus price just continuing to sit on the same side of an already-acted-
on stop.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, "/home/lulurun/workspace/potential-panda-core/src")
from orion.parabolic_sar import ParabolicSarIndicator  # noqa: E402
from qate.trading.chart import Bar  # noqa: E402

_MAKALU_DATA_ROOT = Path("/panda/makalu-data")
_ROOT = Path(__file__).parent.parent
_DEFAULT_FEATURES_ROOT = _ROOT / "markets" / "features"
_CHART_LEN = 5  # matches orion's BarChart(max_len=5) in template.py


class RangeBarSarError(Exception):
    pass


def _chart_range_glob(exchange: str, symbol: str, param_id: str) -> str:
    return str(
        _MAKALU_DATA_ROOT / "gold" / "dataset=Chart_RANGE" / f"exchange={exchange}" / f"symbol={symbol}"
        / f"param_id={param_id}" / "date=*" / "*.parquet"
    )


def _trade_glob(exchange: str, symbol: str) -> str:
    return str(_MAKALU_DATA_ROOT / "bronze" / "dataset=Trade" / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet")


def compute_sar_signal(
    exchange: str, symbol: str, param_id: str, start_date: str, end_date: str,
    acceleration: float, step: float, maximum: float,
) -> pd.DataFrame:
    """Merges Chart_RANGE bar closes with the underlying Trade tick stream
    in chronological order. sar_up/sar_ns (orion's self.sar.up/.ns) update
    only on a bar close, exactly as in live trading. Separately, a
    position_side state (matching self.parabolic.side) tracks which side
    a tick-level cross has most recently signaled -- a flip is only
    emitted when a tick's cross direction actually differs from the
    currently-signaled side, the same idempotency real orion's
    `if self.parabolic.side != side` guard gives (otherwise every
    subsequent tick sitting on the same side of an already-crossed stop
    would re-fire as a "new" flip)."""
    con = duckdb.connect()
    bars_df = con.sql(
        f"SELECT ts, open, high, low, close FROM read_parquet('{_chart_range_glob(exchange, symbol, param_id)}', hive_partitioning=true) "
        f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}' ORDER BY ts"
    ).df()
    if bars_df.empty:
        raise RangeBarSarError(f"no Chart_RANGE data found for {exchange}/{symbol} param_id={param_id}")

    trades_df = con.sql(
        f"SELECT ts, price FROM read_parquet('{_trade_glob(exchange, symbol)}', hive_partitioning=true) "
        f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}' ORDER BY ts"
    ).df()
    if trades_df.empty:
        raise RangeBarSarError(f"no Trade data found for {exchange}/{symbol}")

    sar = ParabolicSarIndicator([acceleration, step, maximum])
    bar_deque: deque[Bar] = deque(maxlen=_CHART_LEN)
    bars_records = bars_df.to_records(index=False)
    n_bars = len(bars_records)
    bar_idx = 0

    sar_up: bool | None = None
    sar_ns: float | None = None
    position_side: bool | None = None  # matches self.parabolic.side; None until warmup completes
    rows = []

    for trade in trades_df.itertuples(index=False):
        while bar_idx < n_bars and bars_records[bar_idx]["ts"] <= trade.ts:
            b = bars_records[bar_idx]
            bar_deque.append(Bar(open=b["open"], high=b["high"], low=b["low"], close=b["close"], close_ts=b["ts"]))
            if len(bar_deque) == _CHART_LEN:
                result = sar.compute(bar_deque)
                if result.get("up") is not None:
                    sar_up, sar_ns = result["up"], result["ns"]
                    if position_side is None:
                        position_side = sar_up  # first warmup: assume already aligned, don't spuriously fire
            bar_idx += 1

        if sar_up is None:
            continue

        if sar_up is True and trade.price < sar_ns:
            desired_side = False
        elif sar_up is False and trade.price > sar_ns:
            desired_side = True
        else:
            desired_side = None

        if desired_side is not None and desired_side != position_side:
            rows.append({"ts": trade.ts, "sar_up": 1.0 if desired_side else -1.0, "sar_flip": 1.0, "sar_level": sar_ns})
            position_side = desired_side

    return pd.DataFrame(rows)


def write_sar_signal(df: pd.DataFrame, exchange: str, symbol: str, features_root: Path = _DEFAULT_FEATURES_ROOT) -> list[Path]:
    df = df.copy()
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.date.astype(str)
    written = []
    for d, day_df in df.groupby("date"):
        path = features_root / "dataset=ChartRangeSAR" / f"exchange={exchange}" / f"symbol={symbol}" / f"date={d}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        day_df.drop(columns=["date"]).to_parquet(path, index=False)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute a precomputed Parabolic SAR signal from Makalu's Chart_RANGE bars")
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--box-size-rate", type=float, required=True, help="Must match an already-populated Chart_RANGE param_id, e.g. 0.0015 -> rate_150")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--acceleration", type=float, default=0.02)
    parser.add_argument("--step", type=float, default=0.02)
    parser.add_argument("--maximum", type=float, default=0.2)
    parser.add_argument("--out-dir", default=str(_DEFAULT_FEATURES_ROOT))
    args = parser.parse_args()

    param_id = f"rate_{int(round(args.box_size_rate * 100_000))}"
    df = compute_sar_signal(
        args.exchange, args.symbol, param_id, args.start, args.end,
        args.acceleration, args.step, args.maximum,
    )
    paths = write_sar_signal(df, args.exchange, args.symbol, Path(args.out_dir))
    flips = int(df["sar_flip"].sum())
    print(f"Wrote {len(paths)} daily partitions, {len(df)} bars, {flips} flips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
