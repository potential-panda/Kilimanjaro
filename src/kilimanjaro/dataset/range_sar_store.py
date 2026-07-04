"""ChartRangeSAR dataset: Parabolic SAR flip events from BINANCE range bars.

Reproduces orion v601's live architecture exactly:
  BINANCE Trade ticks → RangeBarBuilder → ParabolicSarIndicator → flip event

Partition path (feature layer, consistent with all other qate datasets):
  {PARQUET_ROOT}/feature/dataset=ChartRangeSAR
    /exchange={exchange}/symbol={symbol}
    /box_size_rate={box_size_rate}/step={step}
    /date={YYYY-MM-DD}/data.parquet

Date partitions use local (JST) calendar days, matching the convention in
every other store produced by the qate ParquetStore.

Schema (one row per SAR flip):
  ts         float  Unix timestamp of the BINANCE trade tick that crossed the SAR
  sar_up     float  +1.0 = flipped to bullish (buy signal), -1.0 = bearish (sell)
  sar_flip   float  Always 1.0 (existence flag for easy aggregation)
  sar_level  float  The SAR stop level that was crossed
"""

from __future__ import annotations

import datetime
import logging
from collections import deque
from pathlib import Path

import qate.env.sys_env as sys_env
from orion.parabolic_sar import ParabolicSarIndicator
from qate.core.model import Market
from qate.store.parquet_store import Layer, ParquetStore, SingleMarketStore, TradeStore
from qate.trading.chart import RangeBarBuilder
from qate.util.dt_range import DtRange

LOG = logging.getLogger(__name__)

_CHART_LEN = 5   # matches orion's BarChart(max_len=5)
_CHUNK_DAYS = 7  # process this many days per memory chunk


class ChartRangeSARStore(SingleMarketStore):
    """Parquet store for ChartRangeSAR flip events.

    Partition keys: exchange / symbol / box_size_rate / step / date
    """

    def __init__(
        self,
        feature_root_dir: str | Path,
        market: Market,
        box_size_rate: float,
        step: float,
    ):
        super().__init__(str(feature_root_dir), "ChartRangeSAR", market)
        self._box_size_rate = box_size_rate
        self._step = step

    def get_partition_keys(self) -> dict[str, str]:
        return {
            "exchange": self.market.exchange_name.name,
            "symbol": self.market.symbol.name,
            "box_size_rate": str(self._box_size_rate),
            "step": str(self._step),
        }


def _existing_dates(store: ChartRangeSARStore, dt_range: DtRange) -> set[str]:
    """Return the set of date strings that already have a data.parquet file."""
    return {day for day in dt_range.days if store._get_parquet_path(day).exists()}


def populate(
    market: Market,
    start_date: str,
    end_date: str,
    box_size_rate: float,
    step: float = 0.02,
    acceleration: float = 0.02,
    maximum: float = 0.2,
    recreate: bool = False,
) -> int:
    """Populate ChartRangeSAR for the given market and date range.

    SAR state (bar builder, indicator, position side) accumulates across the
    entire range so results are identical to a single-pass run.  Existing
    daily parquet files are skipped unless recreate=True.

    Returns the total number of flip events written.
    """
    parquet_store = ParquetStore(sys_env.PARQUET_ROOT_DIR)
    feature_root = parquet_store.layer_dir[Layer.FEATURE]
    out_store = ChartRangeSARStore(feature_root, market, box_size_rate, step)
    trade_store = TradeStore(str(parquet_store.layer_dir[Layer.BRONZE]), market)

    full_range = DtRange.from_strings(start_date, end_date)
    skip_dates: set[str] = set() if recreate else _existing_dates(out_store, full_range)
    if skip_dates:
        LOG.info(f"Skipping {len(skip_dates)} existing date(s) (pass --recreate to overwrite)")

    # SAR state — intentionally carried across all chunks so the computation
    # is equivalent to a single sequential pass over the full date range.
    bar_builder = RangeBarBuilder(box_size_rate)
    sar = ParabolicSarIndicator([acceleration, step, maximum])
    bar_deque: deque = deque(maxlen=_CHART_LEN)
    sar_up: bool | None = None
    sar_ns: float | None = None
    position_side: bool | None = None  # None until warmup produces first SAR
    total_flips = 0

    for chunk in full_range.chunks(_CHUNK_DAYS):
        trades = trade_store.read_trades(chunk)
        LOG.info(
            f"Chunk {chunk.start_str}..{chunk.end_str}: {len(trades):,} trades"
        )

        for trade in trades:
            ts = trade.get_ts()

            bar = bar_builder.on_update(trade)
            if bar is not None:
                bar_deque.append(bar)
                if len(bar_deque) == _CHART_LEN:
                    result = sar.compute(bar_deque)
                    if result.get("up") is not None:
                        sar_up = result["up"]
                        sar_ns = result["ns"]
                        if position_side is None:
                            # First SAR computed: align position without firing a flip
                            position_side = sar_up

            if sar_up is None:
                continue

            # Tick-level cross check — identical to orion's template.py place_orders()
            if sar_up is True and trade.price < sar_ns:
                desired_side = False
            elif sar_up is False and trade.price > sar_ns:
                desired_side = True
            else:
                continue

            if desired_side == position_side:
                continue

            position_side = desired_side

            # Determine the JST date partition key for this flip
            date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if date_str in skip_dates:
                continue  # skip write but state is already updated above

            out_store.add(ts, {
                "sar_up":    1.0 if desired_side else -1.0,
                "sar_flip":  1.0,
                "sar_level": sar_ns,
            })
            total_flips += 1

    out_store.close()
    LOG.info(
        f"ChartRangeSAR populate done: {total_flips} flips written"
        f" ({market.exchange_name.name}/{market.symbol.name}"
        f" box={box_size_rate} step={step} {start_date}..{end_date})"
    )
    return total_flips
