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
from zoneinfo import ZoneInfo

import pandas as pd
from orion.parabolic_sar import ParabolicSarIndicator
from qate.core.model import Market

# These were Makalu storage APIs, not part of Hase or its qate dependency.
# Keep this module importable for historical verification code; the producer
# itself fails explicitly below until ChartRangeSAR is ported to Hase.
SingleMarketStore = object
Layer = ParquetStore = TradeStore = RangeBarBuilder = DtRange = None
_QATE_STORE_ERROR = RuntimeError("ChartRangeSAR production has not been ported to Hase")

from kilimanjaro.data.root import data_root
from kilimanjaro.framework.verify import (
    compare_frames,
    influx_client,
    influx_range_jst,
    load_influx,
)

LOG = logging.getLogger(__name__)

_CHART_LEN = 5  # matches orion's BarChart(max_len=5)
_CHUNK_DAYS = 7  # process this many days per memory chunk
_JST = ZoneInfo("Asia/Tokyo")


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
        if _QATE_STORE_ERROR is not None:
            raise RuntimeError(
                "ChartRangeSAR production requires qate's legacy store API; "
                "Hase does not provide this dataset."
            ) from _QATE_STORE_ERROR
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
    warmup_days: int = 90,
) -> int:
    """Populate ChartRangeSAR for the given market and date range.

    Processes ``warmup_days`` of trades before ``start_date`` to converge the
    SAR state (matching the backtest which runs from an earlier start date).
    Existing daily parquet files are skipped unless recreate=True.

    Returns the total number of flip events written.
    """
    if _QATE_STORE_ERROR is not None:
        raise RuntimeError(
            "ChartRangeSAR production requires a Hase port; the legacy Makalu store API is unavailable."
        ) from _QATE_STORE_ERROR
    parquet_store = ParquetStore(str(data_root()))
    feature_root = parquet_store.layer_dir[Layer.FEATURE]
    out_store = ChartRangeSARStore(feature_root, market, box_size_rate, step)
    trade_store = TradeStore(str(parquet_store.layer_dir[Layer.BRONZE]), market)

    full_range = DtRange.from_strings(start_date, end_date)
    skip_dates: set[str] = set() if recreate else _existing_dates(out_store, full_range)
    if skip_dates:
        LOG.info(
            f"Skipping {len(skip_dates)} existing date(s) (pass --recreate to overwrite)"
        )

    warmup_start = (
        datetime.date.fromisoformat(start_date) - datetime.timedelta(days=warmup_days)
    ).isoformat()
    process_range = DtRange.from_strings(warmup_start, end_date)
    # Unix timestamp of the first moment of start_date in local (JST) time
    cutoff_ts = datetime.datetime.combine(
        datetime.date.fromisoformat(start_date), datetime.time(), _JST
    ).timestamp()

    bar_builder = RangeBarBuilder(box_size_rate)
    sar = ParabolicSarIndicator([acceleration, step, maximum])
    bar_deque: deque = deque(maxlen=_CHART_LEN)
    sar_up: bool | None = None
    sar_ns: float | None = None
    position_side: bool | None = None
    total_flips = 0

    for chunk in process_range.chunks(_CHUNK_DAYS):
        trades = trade_store.read_trades(chunk)
        LOG.info(f"Chunk {chunk.start_str}..{chunk.end_str}: {len(trades):,} trades")

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
                            position_side = sar_up

            if sar_up is None:
                continue

            if sar_up is True and trade.price < sar_ns:
                desired_side = False
            elif sar_up is False and trade.price > sar_ns:
                desired_side = True
            else:
                continue

            if desired_side == position_side:
                continue

            position_side = desired_side

            if ts < cutoff_ts:
                continue  # warmup phase: update state but don't write

            date_str = datetime.datetime.fromtimestamp(ts, _JST).strftime("%Y-%m-%d")
            if date_str in skip_dates:
                continue

            out_store.add(
                ts,
                {
                    "sar_up": 1.0 if desired_side else -1.0,
                    "sar_flip": 1.0,
                    "sar_level": sar_ns,
                },
            )
            total_flips += 1

    out_store.close()
    LOG.info(
        f"ChartRangeSAR populate done: {total_flips} flips written"
        f" ({market.exchange_name.name}/{market.symbol.name}"
        f" box={box_size_rate} step={step} {start_date}..{end_date})"
    )
    return total_flips


def verify(
    market: Market,
    start_date: str,
    end_date: str,
    box_size_rate: float,
    step: float = 0.02,
    acceleration: float = 0.02,
    maximum: float = 0.2,
    bucket: str = "backtest_QASIM_TARIQ",
    param_set_id: str | None = None,
    warmup_days: int = 90,
) -> dict:
    """Compare Kilimanjaro bar+SAR computation against Himalaya backtest InfluxDB data.

    Re-computes bars and SAR locally from the same BINANCE trade parquet files,
    processing ``warmup_days`` before ``start_date`` to match the backtest's
    converged SAR state. Then queries the backtest BAR measurement from InfluxDB,
    joins on the bar-close timestamp (``ts`` field), and reports per-field diff
    statistics.

    Returns a dict with keys: param_set_id, n_local, n_remote, n_matched,
    n_local_only, n_remote_only, fields (per-field stats dict).
    """
    if _QATE_STORE_ERROR is not None:
        raise RuntimeError(
            "ChartRangeSAR verification requires a Hase port; the legacy Makalu store API is unavailable."
        ) from _QATE_STORE_ERROR
    # ── 1. Local computation with warmup ─────────────────────────────────────
    parquet_store = ParquetStore(str(data_root()))
    trade_store = TradeStore(str(parquet_store.layer_dir[Layer.BRONZE]), market)

    warmup_start = (
        datetime.date.fromisoformat(start_date) - datetime.timedelta(days=warmup_days)
    ).isoformat()
    process_range = DtRange.from_strings(warmup_start, end_date)
    cutoff_ts = datetime.datetime.combine(
        datetime.date.fromisoformat(start_date), datetime.time(), _JST
    ).timestamp()

    bar_builder = RangeBarBuilder(box_size_rate)
    sar_ind = ParabolicSarIndicator([acceleration, step, maximum])
    bar_deque: deque = deque(maxlen=_CHART_LEN)

    local_rows: list[dict] = []
    for chunk in process_range.chunks(_CHUNK_DAYS):
        trades = trade_store.read_trades(chunk)
        for trade in trades:
            bar = bar_builder.on_update(trade)
            if bar is None:
                continue
            bar_deque.append(bar)
            if len(bar_deque) < _CHART_LEN:
                continue
            result = sar_ind.compute(bar_deque)
            if result.get("up") is None:
                continue
            b = bar.as_dict()
            if b["ts"] < cutoff_ts:
                continue  # warmup: update state, don't collect
            local_rows.append(
                {
                    "ts": b["ts"],
                    "open": b["open"],
                    "high": b["high"],
                    "low": b["low"],
                    "close": b["close"],
                    "sar_s": result["s"],
                    "sar_ns": result["ns"],
                    "sar_up": 1.0 if result["up"] else 0.0,
                }
            )

    if not local_rows:
        raise ValueError(
            f"No bars computed locally for {market} {start_date}..{end_date}"
        )

    local_df = pd.DataFrame(local_rows)
    LOG.info(f"Local: {len(local_df)} bars computed")

    # ── 2. InfluxDB ──────────────────────────────────────────────────────────
    box_int = round(box_size_rate * 100_000)
    step_int = round(step * 1_000)
    symbol_str = market.symbol.name
    start_utc, end_utc = influx_range_jst(start_date, end_date)

    client = influx_client()
    qapi = client.query_api()

    # Auto-detect param_set_id: any matching box_int / step_int is fine because
    # bar building depends only on box_size_rate (not schedule / SAR params).
    if param_set_id is None:
        pid_query = f"""
import "strings"
from(bucket: "{bucket}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r["_measurement"] == "BAR")
  |> filter(fn: (r) => r["EXCHANGE_NAME"] == "BINANCE")
  |> filter(fn: (r) => r["SYMBOL"] == "{symbol_str}")
  |> filter(fn: (r) => r["_field"] == "ts")
  |> filter(fn: (r) => strings.containsStr(v: r["param_set_id"], substr: "_{box_int}_{step_int}_"))
  |> keep(columns: ["param_set_id"])
  |> distinct(column: "param_set_id")
  |> limit(n: 1)
"""
        tables = qapi.query(pid_query)
        candidates = [rec.get_value() for tbl in tables for rec in tbl.records]
        if not candidates:
            raise ValueError(
                f"No param_set_id in bucket={bucket} matching box={box_int} step={step_int}"
            )
        param_set_id = candidates[0]
        LOG.info(f"Auto-detected param_set_id: {param_set_id}")

    # Fetch pivoted BAR data for the matching param. BAR carries its own `ts`
    # field (bar-close unix ts) — the join key — so don't derive ts from _time.
    remote_df = load_influx(
        qapi,
        bucket=bucket,
        measurement="BAR",
        fields=["open", "high", "low", "close", "sar:s", "sar:ns", "sar:up", "ts"],
        start=start_utc,
        stop=end_utc,
        tags={
            "EXCHANGE_NAME": "BINANCE",
            "SYMBOL": symbol_str,
            "param_set_id": param_set_id,
        },
        sort=False,
        add_ts=False,
    )
    client.close()
    if remote_df.empty:
        raise ValueError(f"No BAR data in InfluxDB for param_set_id={param_set_id}")
    remote_df["ts"] = remote_df["ts"].astype(float)
    local_df["ts"] = local_df["ts"].astype(float)
    LOG.info(f"Remote: {len(remote_df)} bars from InfluxDB")

    # ── 3. Align on bar-close ts (ms) and compare each field ─────────────────
    cmp = compare_frames(
        local_df,
        remote_df,
        on="ts",
        fields=[
            ("open", "open", "open"),
            ("high", "high", "high"),
            ("low", "low", "low"),
            ("close", "close", "close"),
            ("sar_s", "sar_s", "sar:s"),
            ("sar_ns", "sar_ns", "sar:ns"),
            ("sar_up", "sar_up", "sar:up"),
        ],
        tolerance=None,
        round_ndigits=3,
    )
    cov = cmp["coverage"]
    result = {"param_set_id": param_set_id, **cov, "fields": cmp["fields"]}

    # ── 4. Summary print ─────────────────────────────────────────────────────
    print("\n=== ChartRangeSAR verify ===")
    print(f"  market    : {market.exchange_name.name}/{market.symbol.name}")
    print(
        f"  params    : box={box_size_rate}  step={step}  accel={acceleration}  max={maximum}"
    )
    print(f"  period    : {start_date} .. {end_date}")
    print(f"  param_id  : {param_set_id}")
    print(
        f"  bars      : local={cov['n_local']}  remote={cov['n_remote']}"
        f"  matched={cov['n_matched']}"
    )
    print(
        f"              local_only={cov['n_local_only']}  remote_only={cov['n_remote_only']}"
    )
    if cov["n_matched"] > 0:
        header = f"\n  {'field':<10} {'mean_abs':>14} {'std':>14} {'max_abs':>14} {'pct_exact':>10}"
        print(header)
        print("  " + "-" * (len(header) - 3))
        for field, s in cmp["fields"].items():
            print(
                f"  {field:<10} {s['mean_abs']:>14.8f} {s['std']:>14.8f}"
                f" {s['max_abs']:>14.8f} {s['pct_exact']:>9.2f}%"
            )
    print()
    return result
