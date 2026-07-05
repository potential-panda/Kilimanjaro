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

import pandas as pd
import qate.env.sys_env as sys_env
from orion.parabolic_sar import ParabolicSarIndicator
from qate.core.model import Market
from qate.store.parquet_store import Layer, ParquetStore, SingleMarketStore, TradeStore
from qate.trading.chart import RangeBarBuilder
from qate.util.dt_range import DtRange

_INFLUX_URL = "http://localhost:8086"
_INFLUX_TOKEN = "panda-auth-token"
_INFLUX_ORG = "panda-org"

LOG = logging.getLogger(__name__)

_CHART_LEN = 5  # matches orion's BarChart(max_len=5)
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
    warmup_days: int = 90,
) -> int:
    """Populate ChartRangeSAR for the given market and date range.

    Processes ``warmup_days`` of trades before ``start_date`` to converge the
    SAR state (matching the backtest which runs from an earlier start date).
    Existing daily parquet files are skipped unless recreate=True.

    Returns the total number of flip events written.
    """
    parquet_store = ParquetStore(sys_env.PARQUET_ROOT_DIR)
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
        datetime.datetime.strptime(start_date, "%Y-%m-%d")
        - datetime.timedelta(days=warmup_days)
    ).strftime("%Y-%m-%d")
    process_range = DtRange.from_strings(warmup_start, end_date)
    # Unix timestamp of the first moment of start_date in local (JST) time
    cutoff_ts = datetime.datetime.strptime(start_date, "%Y-%m-%d").timestamp()

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

            date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
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
    from influxdb_client import InfluxDBClient

    # ── 1. Local computation with warmup ─────────────────────────────────────
    parquet_store = ParquetStore(sys_env.PARQUET_ROOT_DIR)
    trade_store = TradeStore(str(parquet_store.layer_dir[Layer.BRONZE]), market)

    warmup_start = (
        datetime.datetime.strptime(start_date, "%Y-%m-%d")
        - datetime.timedelta(days=warmup_days)
    ).strftime("%Y-%m-%d")
    process_range = DtRange.from_strings(warmup_start, end_date)
    cutoff_ts = datetime.datetime.strptime(start_date, "%Y-%m-%d").timestamp()

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

    # JST calendar dates → UTC RFC3339 window (JST = UTC+9)
    _jst = datetime.timedelta(hours=9)
    start_utc = (datetime.datetime.strptime(start_date, "%Y-%m-%d") - _jst).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    end_utc = (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        + datetime.timedelta(days=1)
        - _jst
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    client = InfluxDBClient(
        url=_INFLUX_URL, token=_INFLUX_TOKEN, org=_INFLUX_ORG, timeout=120_000
    )
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

    # Fetch pivoted BAR data for the matching param
    _fields = ["open", "high", "low", "close", "sar:s", "sar:ns", "sar:up", "ts"]
    _filt = " or ".join(f'r["_field"] == "{f}"' for f in _fields)
    bar_query = f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r["_measurement"] == "BAR")
  |> filter(fn: (r) => r["EXCHANGE_NAME"] == "BINANCE")
  |> filter(fn: (r) => r["SYMBOL"] == "{symbol_str}")
  |> filter(fn: (r) => r["param_set_id"] == "{param_set_id}")
  |> filter(fn: (r) => {_filt})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    remote_raw = qapi.query_data_frame(bar_query)
    client.close()

    remote_df = (
        pd.concat(remote_raw, ignore_index=True)
        if isinstance(remote_raw, list)
        else remote_raw
    )
    if remote_df.empty:
        raise ValueError(f"No BAR data in InfluxDB for param_set_id={param_set_id}")

    LOG.info(f"Remote: {len(remote_df)} bars from InfluxDB")

    remote_df = remote_df.rename(
        columns={"sar:s": "sar_s", "sar:ns": "sar_ns", "sar:up": "sar_up_bool"}
    )
    remote_df["sar_up"] = remote_df["sar_up_bool"].astype(float)
    remote_df["ts"] = remote_df["ts"].astype(float)

    # ── 3. Join on ts (bar-close Unix timestamp, rounded to ms) ─────────────
    local_df["_key"] = local_df["ts"].round(3)
    remote_df["_key"] = remote_df["ts"].round(3)
    merged = pd.merge(local_df, remote_df, on="_key", suffixes=("_L", "_R"))

    n_local = len(local_df)
    n_remote = len(remote_df)
    n_matched = len(merged)

    # ── 4. Per-field diff statistics ─────────────────────────────────────────
    _pairs = [
        ("open", "open_L", "open_R"),
        ("high", "high_L", "high_R"),
        ("low", "low_L", "low_R"),
        ("close", "close_L", "close_R"),
        ("sar_s", "sar_s_L", "sar_s_R"),
        ("sar_ns", "sar_ns_L", "sar_ns_R"),
        ("sar_up", "sar_up_L", "sar_up_R"),
    ]
    stats: dict[str, dict] = {}
    for field, lc, rc in _pairs:
        if lc not in merged.columns or rc not in merged.columns:
            continue
        diff = (merged[lc] - merged[rc]).abs()
        stats[field] = {
            "mean_diff": float(diff.mean()),
            "std_diff": float(diff.std()),
            "max_diff": float(diff.max()),
            "n_exact": int((diff == 0.0).sum()),
            "n_total": n_matched,
        }

    result = {
        "param_set_id": param_set_id,
        "n_local": n_local,
        "n_remote": n_remote,
        "n_matched": n_matched,
        "n_local_only": n_local - n_matched,
        "n_remote_only": n_remote - n_matched,
        "fields": stats,
    }

    # ── 5. Summary print ─────────────────────────────────────────────────────
    print("\n=== ChartRangeSAR verify ===")
    print(f"  market    : {market.exchange_name.name}/{market.symbol.name}")
    print(
        f"  params    : box={box_size_rate}  step={step}  accel={acceleration}  max={maximum}"
    )
    print(f"  period    : {start_date} .. {end_date}")
    print(f"  param_id  : {param_set_id}")
    print(f"  bars      : local={n_local}  remote={n_remote}  matched={n_matched}")
    print(
        f"              local_only={n_local - n_matched}  remote_only={n_remote - n_matched}"
    )
    if n_matched > 0:
        header = f"\n  {'field':<10} {'mean_diff':>14} {'std_diff':>14} {'max_diff':>14} {'pct_exact':>10}"
        print(header)
        print("  " + "-" * (len(header) - 3))
        for field, s in stats.items():
            pct = 100.0 * s["n_exact"] / s["n_total"] if s["n_total"] else 0.0
            print(
                f"  {field:<10} {s['mean_diff']:>14.8f} {s['std_diff']:>14.8f}"
                f" {s['max_diff']:>14.8f} {pct:>9.2f}%"
            )
    print()
    return result
