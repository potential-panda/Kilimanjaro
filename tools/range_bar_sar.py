#!/usr/bin/env python3
"""Precomputed Parabolic SAR signal from raw Trade ticks (bronze layer).

v3 (this version): builds range bars directly from bronze Trade ticks of the
signal-source exchange (BINANCE). This matches orion v601's live architecture
exactly: the ref_market (BINANCE XRP_USDT / ETH_USDT / BTC_USDT) Trade events
drive RangeBarBuilder → ParabolicSarIndicator; the resulting flip is then
executed on the target market (GMO). Output is stored under
dataset=ChartRangeSAR/exchange=BINANCE/symbol=XRP_USDT etc. so the scanner
can join it against GMO OrderBook forward returns.

v2 (prior): read Makalu's Chart_RANGE gold data (built from GMO trades) for
the bars, then used GMO Trade ticks for cross detection. Wrong on two counts:
wrong exchange (GMO instead of BINANCE) and wrong bar source (Makalu
precomputed vs. live RangeBarBuilder). The old ChartRangeSAR data under
exchange=GMO is superseded by this version.

The flip is emitted at the exact trade tick where price first crosses the SAR
stop level — not at the next bar close. This matches orion's real live logic:
orion's place_orders() checks every BINANCE trade tick against self.sar.ns
(the stop computed as of the last *closed* bar), and fires immediately on the
first crossing trade.

Production params (orion-01 QIN_TENGFEI / orion-02 QASIM_TARIQ):
    box_size_rate=0.0025, acceleration=0.02, step=0.02, maximum=0.2
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, "/home/lulurun/workspace/potential-panda-orion/src")
sys.path.insert(0, "/home/lulurun/workspace/potential-panda-core/src")
from orion.parabolic_sar import ParabolicSarIndicator  # noqa: E402
from qate.trading.chart import Bar, RangeBarBuilder  # noqa: E402

_MAKALU_DATA_ROOT = Path("/panda/makalu-data")
_ROOT = Path(__file__).parent.parent
_DEFAULT_FEATURES_ROOT = _ROOT / "markets" / "features"
_CHART_LEN = 5  # matches orion's BarChart(max_len=5)


class RangeBarSarError(Exception):
    pass


class _Trade:
    """Lightweight Trade-like wrapper for raw DuckDB rows."""
    __slots__ = ("price", "size", "_ts")

    def __init__(self, price: float, size: float, ts: float):
        self.price = price
        self.size = size
        self._ts = ts

    def get_ts(self) -> float:
        return self._ts


def _trade_glob(exchange: str, symbol: str) -> str:
    return str(
        _MAKALU_DATA_ROOT / "bronze" / "dataset=Trade"
        / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet"
    )


def compute_sar_signal(
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    box_size_rate: float,
    acceleration: float = 0.02,
    step: float = 0.02,
    maximum: float = 0.2,
    data_root: Path = _MAKALU_DATA_ROOT,
) -> pd.DataFrame:
    """Build range bars from `exchange/symbol` trade ticks and detect SAR flips.

    Returns one row per flip: ts, sar_up (+1.0 = up/buy side, -1.0 = down/sell),
    sar_flip (always 1.0), sar_level (the stop that was crossed).
    """
    glob = str(
        data_root / "bronze" / "dataset=Trade"
        / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet"
    )
    con = duckdb.connect()
    trades_df = con.sql(
        f"SELECT ts, price, size FROM read_parquet('{glob}', hive_partitioning=true) "
        f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}' ORDER BY ts"
    ).df()
    if trades_df.empty:
        raise RangeBarSarError(f"no Trade data for {exchange}/{symbol} {start_date}..{end_date}")

    bar_builder = RangeBarBuilder(box_size_rate)
    sar = ParabolicSarIndicator([acceleration, step, maximum])
    bar_deque: deque[Bar] = deque(maxlen=_CHART_LEN)

    sar_up: bool | None = None
    sar_ns: float | None = None
    position_side: bool | None = None  # None until warmup completes
    rows = []

    for row in trades_df.itertuples(index=False):
        trade = _Trade(float(row.price), float(row.size), float(row.ts))

        bar = bar_builder.on_update(trade)
        if bar is not None:
            bar_deque.append(bar)
            if len(bar_deque) == _CHART_LEN:
                result = sar.compute(bar_deque)
                if result.get("up") is not None:
                    sar_up, sar_ns = result["up"], result["ns"]
                    if position_side is None:
                        # First warmup: assume already aligned so we don't
                        # spuriously fire on the very first computed SAR.
                        position_side = sar_up

        if sar_up is None:
            continue

        # Tick-level cross check — identical to orion's template.py place_orders()
        if sar_up is True and trade.price < sar_ns:
            desired_side = False
        elif sar_up is False and trade.price > sar_ns:
            desired_side = True
        else:
            desired_side = None

        if desired_side is not None and desired_side != position_side:
            rows.append({
                "ts": trade.get_ts(),
                "sar_up": 1.0 if desired_side else -1.0,
                "sar_flip": 1.0,
                "sar_level": sar_ns,
            })
            position_side = desired_side

    return pd.DataFrame(rows)


def write_sar_signal(
    df: pd.DataFrame,
    exchange: str,
    symbol: str,
    box_size_rate: float,
    features_root: Path = _DEFAULT_FEATURES_ROOT,
) -> list[Path]:
    df = df.copy()
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.date.astype(str)
    written = []
    for d, day_df in df.groupby("date"):
        path = (
            features_root / "dataset=ChartRangeSAR"
            / f"exchange={exchange}" / f"symbol={symbol}"
            / f"box_size_rate={box_size_rate}"
            / f"date={d}" / "data.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        day_df.drop(columns=["date"]).to_parquet(path, index=False)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute Parabolic SAR flips from bronze Trade ticks (range-bar method)"
    )
    parser.add_argument("--exchange", required=True, help="Trade source exchange (e.g. BINANCE)")
    parser.add_argument("--symbol", required=True, help="Trade source symbol (e.g. XRP_USDT)")
    parser.add_argument("--box-size-rate", type=float, required=True,
                        help="Range bar size as fraction of price (e.g. 0.0025 = 0.25%%)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--acceleration", type=float, default=0.02)
    parser.add_argument("--step", type=float, default=0.02)
    parser.add_argument("--maximum", type=float, default=0.2)
    parser.add_argument("--out-dir", default=str(_DEFAULT_FEATURES_ROOT))
    args = parser.parse_args()

    df = compute_sar_signal(
        args.exchange, args.symbol,
        args.start, args.end,
        args.box_size_rate,
        args.acceleration, args.step, args.maximum,
    )
    paths = write_sar_signal(df, args.exchange, args.symbol, args.box_size_rate, Path(args.out_dir))
    flips = int(df["sar_flip"].sum()) if not df.empty else 0
    print(f"Wrote {len(paths)} daily partitions, {flips} flips total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
