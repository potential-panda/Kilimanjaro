"""Backtest fidelity check — compare a scan against the Himalaya/InfluxDB backtest.

This is the framework's scan↔backtest verification facility: the last honest
gut-check before trusting a mechanism, and the anchor for `doc/calibration.md`'s
calibration registry. It runs a scan's own entry/exit prices and PnL against the
backtest's recorded ORDER (EXEC_PRICE) and EVAL (pnl_change0) streams and reports
where they agree and where they diverge.

`verify_flip_scan()` is the first (currently only) verifier — flip-anchored SAR +
between-signal returns, the one CALIBRATED mechanism. As other mechanisms get
calibrated, their verifiers join this module (e.g. an expression/fixed-horizon
verifier) so backtest verification stays one framework concern, not per-scan code.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

_INFLUX_URL = "http://localhost:8086"
_INFLUX_TOKEN = "panda-auth-token"
_INFLUX_ORG = "panda-org"

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
    import qate.env.sys_env as sys_env
    from qate.store.parquet_store import Layer, ParquetStore

    return ParquetStore(sys_env.PARQUET_ROOT_DIR).layer_dir[Layer.FEATURE]


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
    """Local helper for verify_flip_scan(): adds JST columns and applies date/schedule filters."""
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


def _influx_range(start_date, end_date):
    _jst = datetime.timedelta(hours=9)
    start_utc = (datetime.datetime.strptime(start_date, "%Y-%m-%d") - _jst).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    end_utc = (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        + datetime.timedelta(days=1)
        - _jst
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start_utc, end_utc


def _query_df(qapi, flux):
    raw = qapi.query_data_frame(flux)
    if isinstance(raw, list):
        return pd.concat(raw, ignore_index=True) if raw else pd.DataFrame()
    return raw


def verify_flip_scan(
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
    """Compare scan flip prices and PnL against Himalaya backtest InfluxDB data."""
    from influxdb_client import InfluxDBClient

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

    start_utc, end_utc = _influx_range(start_date, end_date)
    client = InfluxDBClient(
        url=_INFLUX_URL, token=_INFLUX_TOKEN, org=_INFLUX_ORG, timeout=120_000
    )
    qapi = client.query_api()

    order_df = _query_df(
        qapi,
        f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r["_measurement"] == "ORDER")
  |> filter(fn: (r) => r["param_set_id"] == "{param_set_id}")
  |> filter(fn: (r) => r["SETTLE_TYPE"] == "OPEN")
  |> filter(fn: (r) => r["_field"] == "EXEC_PRICE")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
""",
    )

    eval_df = _query_df(
        qapi,
        f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r["_measurement"] == "EVAL")
  |> filter(fn: (r) => r["param_set_id"] == "{param_set_id}")
  |> filter(fn: (r) => r["_field"] == "pnl_change0")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
""",
    )
    client.close()

    n_scan = len(flips_df)
    n_orders = len(order_df)
    order_result = {
        "n_scan": n_scan,
        "n_orders": n_orders,
        "n_matched": 0,
        "time_diff": {},
        "price_diff": {},
    }

    if not order_df.empty:
        _jst = datetime.timezone(datetime.timedelta(hours=9))
        order_df["order_ts"] = order_df["_time"].apply(lambda t: t.timestamp())
        order_df = order_df.sort_values("order_ts").reset_index(drop=True)
        scan_sorted = flips_df.sort_values("ts").reset_index(drop=True)
        order_ts_arr = order_df["order_ts"].values
        mp_ts_arr = scan_sorted["mp_ts"].values

        _MATCH_THRESHOLD = 2.0
        s_to_o = np.full(len(mp_ts_arr), -1, dtype=int)
        o_to_s = np.full(len(order_ts_arr), -1, dtype=int)
        i, j = 0, 0
        while i < len(mp_ts_arr) and j < len(order_ts_arr):
            d = mp_ts_arr[i] - order_ts_arr[j]
            if abs(d) <= _MATCH_THRESHOLD:
                s_to_o[i] = j
                o_to_s[j] = i
                i += 1
                j += 1
            elif d < 0:
                i += 1
            else:
                j += 1

        matched_mask_s = s_to_o >= 0
        matched_mask_o = o_to_s >= 0
        n_matched = int(matched_mask_s.sum())
        time_diff = order_ts_arr[s_to_o[matched_mask_s]] - mp_ts_arr[matched_mask_s]
        fp_matched = scan_sorted["flip_price"].values[matched_mask_s]
        ep_matched = order_df["EXEC_PRICE"].values[s_to_o[matched_mask_s]]
        price_diff = ep_matched - fp_matched
        price_diff_bps = price_diff / fp_matched * 10000

        gap_scan_idx = np.where(~matched_mask_s)[0]
        gap_order_idx = np.where(~matched_mask_o)[0]
        gap_rows: list[dict] = []
        for idx in gap_scan_idx:
            row = scan_sorted.iloc[idx]
            gap_rows.append(
                {
                    "kind": "scan_only",
                    "ts_jst": datetime.datetime.fromtimestamp(row["ts"], tz=_jst),
                    "dir": f"sar_up={int(row['sar_up']):+d}",
                    "price": f"flip={row['flip_price']:.0f}¥",
                }
            )
        for idx in gap_order_idx:
            row = order_df.iloc[idx]
            gap_rows.append(
                {
                    "kind": "order_only",
                    "ts_jst": datetime.datetime.fromtimestamp(row["order_ts"], tz=_jst),
                    "dir": row.get("SIDE", "?"),
                    "price": f"exec={row['EXEC_PRICE']:.0f}¥",
                }
            )
        gap_rows.sort(key=lambda r: r["ts_jst"])

        order_result.update(
            {
                "n_matched": n_matched,
                "n_gap": len(gap_scan_idx) + len(gap_order_idx),
                "n_scan_only": int(len(gap_scan_idx)),
                "n_order_only": int(len(gap_order_idx)),
                "gap_rows": gap_rows,
                "time_diff": {
                    "mean_s": float(np.mean(time_diff)),
                    "std_s": float(np.std(time_diff)),
                    "max_abs_s": float(np.max(np.abs(time_diff))),
                },
                "price_diff": {
                    "mean_jpy": float(np.mean(price_diff)),
                    "std_jpy": float(np.std(price_diff)),
                    "max_abs_jpy": float(np.max(np.abs(price_diff))),
                    "mean_bps": float(np.mean(price_diff_bps)),
                    "std_bps": float(np.std(price_diff_bps)),
                },
            }
        )

    n_bf_scan = len(bf)
    n_eval = len(eval_df)
    pnl_result = {
        "n_scan_positions": n_bf_scan,
        "n_eval": n_eval,
        "n_matched": 0,
        "n_scan_only": 0,
        "n_eval_only": 0,
        "pnl_diff": {},
    }

    if not eval_df.empty and not bf.empty:
        eval_df["eval_ts"] = eval_df["_time"].apply(lambda t: t.timestamp())
        eval_df = eval_df.sort_values("eval_ts").reset_index(drop=True)
        bf_sorted = bf.sort_values("exit_ts").reset_index(drop=True)
        eval_ts_arr = eval_df["eval_ts"].values
        exit_ts_arr = bf_sorted["exit_ts"].values

        _PNL_THRESHOLD = 2.0
        p_to_e = np.full(len(exit_ts_arr), -1, dtype=int)
        e_to_p = np.full(len(eval_ts_arr), -1, dtype=int)
        i, j = 0, 0
        while i < len(exit_ts_arr) and j < len(eval_ts_arr):
            d = exit_ts_arr[i] - eval_ts_arr[j]
            if abs(d) <= _PNL_THRESHOLD:
                p_to_e[i] = j
                e_to_p[j] = i
                i += 1
                j += 1
            elif d < 0:
                i += 1
            else:
                j += 1

        matched_mask_p = p_to_e >= 0
        matched_mask_e = e_to_p >= 0
        n_pnl_matched = int(matched_mask_p.sum())
        _jst = datetime.timezone(datetime.timedelta(hours=9))
        matched_bf = bf_sorted[matched_mask_p].reset_index(drop=True)
        scan_pnl_arr = matched_bf["scan_pnl"].values
        eval_pnl_arr = eval_df["pnl_change0"].values[p_to_e[matched_mask_p]]
        pnl_diff = scan_pnl_arr - eval_pnl_arr

        scan_only_df = bf_sorted[~matched_mask_p].reset_index(drop=True)
        scan_only_rows = [
            {
                "entry_jst": datetime.datetime.fromtimestamp(row["ts"], tz=_jst),
                "exit_jst": datetime.datetime.fromtimestamp(row["exit_ts"], tz=_jst),
                "sar_up": int(row["sar_up"]),
                "scan_pnl": float(row["scan_pnl"]),
            }
            for _, row in scan_only_df.iterrows()
        ]
        eval_only_df = eval_df[~matched_mask_e].reset_index(drop=True)
        eval_only_rows = [
            {
                "eval_jst": datetime.datetime.fromtimestamp(row["eval_ts"], tz=_jst),
                "eval_pnl": float(row["pnl_change0"]),
            }
            for _, row in eval_only_df.iterrows()
        ]

        top_idx = np.argsort(np.abs(pnl_diff))[::-1]
        top_rows = []
        for idx in top_idx:
            d = float(pnl_diff[idx])
            if abs(d) < 0.01:
                continue
            entry_ts = matched_bf["ts"].values[idx]
            exit_ts = matched_bf["exit_ts"].values[idx]
            top_rows.append(
                {
                    "entry_jst": datetime.datetime.fromtimestamp(entry_ts, tz=_jst),
                    "exit_jst": datetime.datetime.fromtimestamp(exit_ts, tz=_jst),
                    "sar_up": int(matched_bf["sar_up"].values[idx]),
                    "scan_pnl": float(scan_pnl_arr[idx]),
                    "eval_pnl": float(eval_pnl_arr[idx]),
                    "diff": d,
                }
            )
            if len(top_rows) == 10:
                break

        pnl_result.update(
            {
                "n_matched": n_pnl_matched,
                "n_scan_only": int((~matched_mask_p).sum()),
                "n_eval_only": int((~matched_mask_e).sum()),
                "scan_only_rows": scan_only_rows,
                "eval_only_rows": eval_only_rows,
                "top_diff_rows": top_rows,
                "pnl_diff": {
                    "mean_jpy": float(np.mean(pnl_diff)),
                    "std_jpy": float(np.std(pnl_diff)),
                    "max_abs_jpy": float(np.max(np.abs(pnl_diff))),
                    "pct_exact": float(np.mean(np.abs(pnl_diff) < 0.1)) * 100,
                },
            }
        )

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_flip_verify_report(
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
            order_result,
            pnl_result,
        )
    text = buf.getvalue()
    print(text, end="")
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text)
        print(f"Saved {out}")

    return {"order": order_result, "pnl": pnl_result}


def _print_flip_verify_report(
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
    order_result,
    pnl_result,
):
    W = 66
    print(f"\n{'─' * W}")
    print(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"  {flip_exchange}/{flip_symbol}  →  {target_exchange}/{target_symbol}"
        f"   box={box_size_rate}  step={sar_step}  size={execution_size}"
    )
    print(f"  {start_date} .. {end_date}")
    print(f"  {param_set_id}")
    schedule_parts = []
    if target_exchange.upper() == "GMO":
        schedule_parts.append(f"sat(GMO maintenance)={_GMO_MAINTENANCE_SAT_HOURS}")
    all_dates = sorted(_DEFAULT_BLOCK_JST_DATES)
    schedule_parts.append(f"dates={all_dates[0]}..{all_dates[-1]}" if all_dates else "")
    print(f"  block: {', '.join(p for p in schedule_parts if p)}")
    print(f"{'─' * W}")

    n_scan = order_result["n_scan"]
    n_orders = order_result["n_orders"]
    n_matched = order_result.get("n_matched", 0)
    n_gap = order_result.get("n_gap", 0)
    n_scan_only = order_result.get("n_scan_only", 0)
    n_ord_only = order_result.get("n_order_only", 0)
    match_rate = n_matched / n_scan * 100 if n_scan else 0.0
    print("\n  coverage")
    print(
        f"    {'scan flips':<14} {n_scan:>6}    {'backtest orders':<16} {n_orders:>6}"
        f"    {'matched':<8} {n_matched:>6}  ({match_rate:.1f}%)"
    )
    print(
        f"    {'gap':<14} {n_gap:>6}    {'scan_only':<16} {n_scan_only:>6}"
        f"    {'order_only':<8} {n_ord_only:>6}"
    )

    if order_result["time_diff"] and order_result["price_diff"]:
        td = order_result["time_diff"]
        prd = order_result["price_diff"]
        print("\n  execution  (matched pairs, entry time = GMO mp_ts → backtest fill)")
        print(f"    {'':16}  {'mean':>10}  {'std':>10}  {'max':>10}")
        print(
            f"    {'time diff (s)':<16}  {td['mean_s']:>+10.3f}  {td['std_s']:>10.3f}  {td['max_abs_s']:>10.3f}"
        )
        print(
            f"    {'price diff (¥)':<16}  {prd['mean_jpy']:>+10.2f}  {prd['std_jpy']:>10.2f}  {prd['max_abs_jpy']:>10.2f}"
        )
        print(
            f"    {'price diff (bps)':<16}  {prd['mean_bps']:>+10.3f}  {prd['std_bps']:>10.3f}"
        )

    n_pos = pnl_result["n_scan_positions"]
    n_eval = pnl_result["n_eval"]
    n_pnl_match = pnl_result.get("n_matched", 0)
    n_pnl_sonly = pnl_result.get("n_scan_only", 0)
    n_pnl_eonly = pnl_result.get("n_eval_only", 0)
    pnl_match_rt = n_pnl_match / n_pos * 100 if n_pos else 0.0
    print("\n  pnl  (scan between-flip JPY  vs  backtest pnl_change0)")
    print(
        f"    {'scan positions':<14} {n_pos:>6}    {'eval records':<16} {n_eval:>6}"
        f"    {'matched':<8} {n_pnl_match:>6}  ({pnl_match_rt:.1f}%)"
    )
    print(
        f"    {'scan_only':<14} {n_pnl_sonly:>6}    {'eval_only':<16} {n_pnl_eonly:>6}"
    )
    if pnl_result["pnl_diff"]:
        pd2 = pnl_result["pnl_diff"]
        print(f"    {'':16}  {'mean':>10}  {'std':>10}  {'max':>10}  {'pct_exact':>9}")
        print(
            f"    {'pnl diff (¥)':<16}  {pd2['mean_jpy']:>+10.2f}  {pd2['std_jpy']:>10.2f}"
            f"  {pd2['max_abs_jpy']:>10.2f}  {pd2['pct_exact']:>8.1f}%"
        )

    scan_only_rows = pnl_result.get("scan_only_rows", [])
    eval_only_rows = pnl_result.get("eval_only_rows", [])
    if scan_only_rows or eval_only_rows:
        print("\n  unmatched pnl positions")
        if scan_only_rows:
            print(f"    scan_only ({len(scan_only_rows)}):")
            print(
                f"    {'entry (JST)':<21}  {'exit (JST)':<21}  {'dir':>4}  {'scan_pnl':>10}"
            )
            for r in scan_only_rows:
                print(
                    f"    {r['entry_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                    f"  {r['exit_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                    f"  {r['sar_up']:>+4}  {r['scan_pnl']:>10.2f}"
                )
        if eval_only_rows:
            print(f"    eval_only ({len(eval_only_rows)}):")
            print(f"    {'exit (JST)':<21}  {'eval_pnl':>10}")
            for r in eval_only_rows:
                print(
                    f"    {r['eval_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}  {r['eval_pnl']:>10.2f}"
                )

    top_rows = pnl_result.get("top_diff_rows", [])
    if top_rows:
        print(f"\n  top-{len(top_rows)} PnL diff  (matched, |diff|≥0.01¥)")
        print(
            f"    {'entry (JST)':<21}  {'exit (JST)':<21}  {'dir':>4}"
            f"  {'scan_pnl':>10}  {'eval_pnl':>10}  {'diff':>10}"
        )
        for r in top_rows:
            print(
                f"    {r['entry_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                f"  {r['exit_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                f"  {r['sar_up']:>+4}  {r['scan_pnl']:>10.2f}  {r['eval_pnl']:>10.2f}  {r['diff']:>+10.2f}"
            )

    gap_rows = order_result.get("gap_rows", [])
    if gap_rows:
        print(f"\n  unmatched orders  ({len(gap_rows)} items)")
        print(f"    {'#':<4}  {'kind':<12}  {'time (JST)':<21}  {'dir':<12}  price")
        for k, r in enumerate(gap_rows):
            print(
                f"    {k:<4}  {r['kind']:<12}"
                f"  {r['ts_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                f"  {r['dir']:<12}  {r['price']}"
            )

    print(f"\n{'─' * W}\n")
