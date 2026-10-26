"""The Himalaya backtest as a data source — verify against it.

Unlike the parquet dataset modules, the backtest is produced by Himalaya, not
from here, so this module only **verifies** (no populate). `verify()` is the
scan↔backtest fidelity check — the last honest gut-check before trusting a
mechanism, and the anchor for `doc/calibration.md`'s calibration registry.

It compares a flip-anchored SAR scan's entry fills and per-trade PnL against the
backtest's recorded ORDER (EXEC_PRICE) / EVAL (pnl_change0) streams via the
general engine (`framework.verify.compare_frames`), then reports the scan's own
risk-adjusted performance (Sharpe/Calmar/Martin, `framework.performance`).
"""

from __future__ import annotations

import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from kilimanjaro.data.root import data_root as _resolve_data_root
from kilimanjaro.framework.costs import ROUND_TRIP_COST_BPS
from kilimanjaro.framework.performance import (
    compute_path_metrics,
    compute_path_metrics_jpy,
)
from kilimanjaro.framework.verify import (
    compare_frames,
    influx_client,
    influx_range_jst,
    load_influx,
    render_compare,
)

_DEFAULT_DATA_ROOT = None

# BINANCE ETH raw-data outage (missing days) — blocked in every flip scan.
DEFAULT_BLOCK_JST_DATES: list[str] = [
    "2026-04-02",
    "2026-04-03",
    "2026-04-04",
    "2026-04-05",
    "2026-04-06",
    "2026-04-07",
    "2026-04-08",
    "2026-04-09",
]
_DEFAULT_BLOCK_JST_DATES = DEFAULT_BLOCK_JST_DATES  # backward-compat alias

_GMO_MAINTENANCE_SAT_HOURS: list[int] = [9, 10, 11]


# ── flip-scan helpers ─────────────────────────────────────────────────────────


def _features_root() -> Path:
    from kilimanjaro.data.root import data_root

    return data_root() / "feature"


def _flip_glob(exchange, symbol, box_size_rate, sar_step, features_root):
    return str(
        features_root
        / "dataset=ChartRangeSAR"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / f"box_size_rate={box_size_rate}"
        / f"step={sar_step}"
        / "date=*"
        / "*.parquet"
    )


def _mp_glob(exchange, symbol, execution_size, data_root):
    return str(
        data_root
        / "silver"
        / "dataset=MarketPrice"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / f"execution_size={execution_size}"
        / "date=*"
        / "*.parquet"
    )


def _apply_schedule_filters(
    flips_df,
    target_exchange,
    block_weekday_hours,
    block_weekend_hours,
    exclude_jst_dates,
):
    """Local helper for verify(): adds JST columns and applies date/schedule filters."""
    _JST = 9 * 3600
    has_exit = flips_df["exit_ts"].notna()
    all_dates = set(_DEFAULT_BLOCK_JST_DATES)
    if exclude_jst_dates:
        all_dates |= set(exclude_jst_dates)

    n_date_blocked = 0
    if all_dates:
        mask = flips_df["jst_date"].isin(all_dates)
        n_date_blocked = int(mask.sum())
        flips_df = flips_df[~mask].copy()

    n_weekday_blocked = 0
    if block_weekday_hours:
        mask = (
            has_exit
            & (flips_df["jst_weekday"] <= 4)
            & flips_df["jst_hour"].isin(set(block_weekday_hours))
        )
        n_weekday_blocked = int(mask.sum())
        flips_df = flips_df[~mask].copy()
        has_exit = has_exit[~mask]

    n_sat_blocked = 0
    if block_weekend_hours:
        mask = (
            has_exit
            & (flips_df["jst_weekday"] >= 5)
            & flips_df["jst_hour"].isin(set(block_weekend_hours))
        )
        n_sat_blocked += int(mask.sum())
        flips_df = flips_df[~mask].copy()
        has_exit = has_exit[~mask]
    if target_exchange.upper() == "GMO":
        mask = (
            has_exit
            & (flips_df["jst_weekday"] == 5)
            & flips_df["jst_hour"].isin(_GMO_MAINTENANCE_SAT_HOURS)
        )
        n_sat_blocked += int(mask.sum())
        flips_df = flips_df[~mask].copy()

    return flips_df, n_date_blocked, n_weekday_blocked, n_sat_blocked


def verify(
    flip_exchange,
    flip_symbol,
    target_exchange,
    target_symbol,
    start_date,
    end_date,
    box_size_rate,
    sar_step,
    execution_size,
    bucket,
    param_set_id,
    data_root=_DEFAULT_DATA_ROOT,
    features_root=None,
    out=None,
):
    """Compare scan flip prices and PnL against Himalaya backtest InfluxDB data.

    Runs the basic parquet↔InfluxDB compare (framework.verify.compare_frames) on
    entry fills and per-trade PnL, then layers the scan's own risk-adjusted
    performance (Sharpe/Calmar/Martin via framework.performance)."""
    data_root = _resolve_data_root(data_root)
    if features_root is None:
        features_root = _features_root()

    flip_glob = _flip_glob(
        flip_exchange, flip_symbol, box_size_rate, sar_step, features_root
    )
    mp_glob = _mp_glob(target_exchange, target_symbol, execution_size, data_root)
    size_f = float(execution_size)
    con = duckdb.connect()

    flips_df = (
        con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        flips_paired AS (
            SELECT ts, sar_up, LEAD(ts) OVER (ORDER BY ts) AS exit_ts FROM flips
        ),
        mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT f.ts, f.sar_up, f.exit_ts,
            CASE WHEN f.sar_up > 0 THEN o.ask ELSE o.bid END AS flip_price,
            o.bid AS entry_bid, o.ask AS entry_ask, o.ts AS mp_ts
        FROM flips_paired f ASOF JOIN mp o ON o.ts >= f.ts
        ORDER BY f.ts
    """)
        .df()
        .dropna(subset=["flip_price"])
    )

    if flips_df.empty:
        raise ValueError(
            f"no flip data for {flip_exchange}/{flip_symbol} in {start_date}..{end_date}"
        )

    _JST = 9 * 3600
    exit_jst = (flips_df["exit_ts"].fillna(flips_df["ts"]) + _JST).astype("int64")
    flips_df["jst_hour"] = (exit_jst % 86400 // 3600).astype(int)
    flips_df["jst_weekday"] = pd.to_datetime(exit_jst, unit="s", utc=True).dt.dayofweek
    ts_jst = (flips_df["ts"] + _JST).astype("int64")
    flips_df["jst_date"] = pd.to_datetime(ts_jst, unit="s", utc=True).dt.date.astype(
        str
    )

    flips_df, _, _, _ = _apply_schedule_filters(
        flips_df, target_exchange, None, None, None
    )
    flips_df = flips_df.drop(
        columns=[
            c for c in ["jst_hour", "jst_weekday", "jst_date"] if c in flips_df.columns
        ]
    )

    pairs = flips_df[["ts", "sar_up", "exit_ts"]].dropna(subset=["exit_ts"]).copy()
    pairs = pairs.rename(columns={"exit_ts": "next_ts"})
    con.register("_pairs", pairs)
    exit_ob = (
        con.sql(f"""
        WITH mp AS (
            SELECT ts, bid, ask FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT p.ts, CASE WHEN p.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
        FROM _pairs p ASOF JOIN mp o ON o.ts >= p.next_ts
    """)
        .df()
        .dropna()
    )

    bf = flips_df.merge(exit_ob, on="ts", how="inner")
    bf["scan_pnl"] = bf["sar_up"] * (bf["exit_price"] - bf["flip_price"]) * size_f

    # ── remote: backtest ORDER (entry fills) + EVAL (per-trade pnl) ──
    start_utc, end_utc = influx_range_jst(start_date, end_date)
    client = influx_client()
    qapi = client.query_api()
    order_df = load_influx(
        qapi,
        bucket=bucket,
        measurement="ORDER",
        fields=["EXEC_PRICE"],
        start=start_utc,
        stop=end_utc,
        tags={"param_set_id": param_set_id, "SETTLE_TYPE": "OPEN"},
    )
    eval_df = load_influx(
        qapi,
        bucket=bucket,
        measurement="EVAL",
        fields=["pnl_change0"],
        start=start_utc,
        stop=end_utc,
        tags={"param_set_id": param_set_id},
    )
    client.close()

    # ── basic verify via the general engine: entry price + pnl ──
    # scan is `local`, backtest is `remote`; every diff reads scan − backtest.
    order_local = flips_df[["mp_ts", "flip_price"]].rename(
        columns={"mp_ts": "match_ts"}
    )
    order_remote = (
        order_df[["ts", "EXEC_PRICE"]].rename(columns={"ts": "match_ts"})
        if not order_df.empty
        else pd.DataFrame(columns=["match_ts", "EXEC_PRICE"])
    )
    order_cmp = compare_frames(
        order_local,
        order_remote,
        on="match_ts",
        fields=[
            ("entry_price", "flip_price", "EXEC_PRICE"),
            ("entry_dt_s", "match_ts", "match_ts"),
        ],
        tolerance=2.0,
    )

    pnl_local = bf[["exit_ts", "scan_pnl"]].rename(columns={"exit_ts": "match_ts"})
    pnl_remote = (
        eval_df[["ts", "pnl_change0"]].rename(columns={"ts": "match_ts"})
        if not eval_df.empty
        else pd.DataFrame(columns=["match_ts", "pnl_change0"])
    )
    pnl_cmp = compare_frames(
        pnl_local,
        pnl_remote,
        on="match_ts",
        fields=[("pnl", "scan_pnl", "pnl_change0")],
        tolerance=2.0,
    )

    # ── plus: risk-adjusted performance of the scan's own positions ──
    if len(bf):
        ret_bps = (
            bf["sar_up"]
            * (bf["exit_price"] - bf["flip_price"])
            / bf["flip_price"]
            * 1e4
        )
        net_bps = (ret_bps - ROUND_TRIP_COST_BPS).to_numpy()
        exit_ts = bf["exit_ts"].to_numpy()
        notional = (size_f * bf["flip_price"]).to_numpy()
        perf = compute_path_metrics(net_bps, exit_ts)
        perf_jpy = compute_path_metrics_jpy(net_bps, exit_ts, notional)
    else:
        perf, perf_jpy = {}, {}

    result = {
        "order": {**order_cmp["coverage"], "fields": order_cmp["fields"]},
        "pnl": {**pnl_cmp["coverage"], "fields": pnl_cmp["fields"]},
        "performance": perf,
        "performance_jpy": perf_jpy,
    }

    text = _render_scan_verify(
        flip_exchange,
        flip_symbol,
        target_exchange,
        target_symbol,
        box_size_rate,
        sar_step,
        execution_size,
        start_date,
        end_date,
        param_set_id,
        order_cmp,
        pnl_cmp,
        perf,
        perf_jpy,
    )
    print(text, end="")
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text)
        print(f"Saved {out}")
    return result


def _render_scan_verify(
    flip_exchange,
    flip_symbol,
    target_exchange,
    target_symbol,
    box_size_rate,
    sar_step,
    execution_size,
    start_date,
    end_date,
    param_set_id,
    order_cmp,
    pnl_cmp,
    perf,
    perf_jpy,
):
    W = 72
    lines = [
        f"\n{'─' * W}",
        f"Generated: {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        (
            f"  {flip_exchange}/{flip_symbol}  →  {target_exchange}/{target_symbol}"
            f"   box={box_size_rate}  step={sar_step}  size={execution_size}"
        ),
        f"  {start_date} .. {end_date}",
        f"  {param_set_id}",
        f"{'─' * W}",
    ]

    lines.append(
        render_compare(
            order_cmp, title="\n  entry fills (scan − backtest):", value_fmt=".4f"
        )
    )
    m = order_cmp["matched"]
    if len(m):
        bps = (m["entry_price_diff"] / m["entry_price_remote"] * 1e4).to_numpy()
        absb = np.abs(bps)
        lines.append(
            f"    price diff: mean {bps.mean():+.3f} bps   "
            f"p50 {np.percentile(absb, 50):.3f}   p90 {np.percentile(absb, 90):.3f} bps"
        )

    lines.append(
        render_compare(
            pnl_cmp, title="\n  pnl (scan JPY − backtest pnl_change0):", value_fmt=".2f"
        )
    )

    if perf:
        lines.append("\n  scan performance (net of cost, path-aware):")
        lines.append(
            f"    sharpe {perf.get('sharpe_daily', float('nan')):>7.2f}   "
            f"calmar {perf.get('calmar', float('nan')):>7.2f}   "
            f"martin {perf.get('martin', float('nan')):>7.2f}   "
            f"ulcer {perf.get('ulcer_index', float('nan')):>7.3f}"
        )
        if perf_jpy:
            lines.append(
                f"    JPY: martin {perf_jpy.get('martin_jpy', float('nan')):>7.2f}   "
                f"calmar {perf_jpy.get('calmar_jpy', float('nan')):>7.2f}   "
                f"sharpe {perf_jpy.get('sharpe_jpy_daily', float('nan')):>7.2f}"
            )
    lines.append(f"\n{'─' * W}\n")
    return "\n".join(lines)
