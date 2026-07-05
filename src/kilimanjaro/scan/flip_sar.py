"""Flip-anchored scan for event-driven signals like orion's Parabolic SAR.

Unlike a continuous signal scanner, this anchors on FLIP events — one row per
trade decision.  That matches how orion actually works: enter at the flip, hold
until the next flip.

Two return definitions are computed for each flip:
  - fixed-horizon: signed mid return at H seconds after the flip
  - between-flip:  signed mid return from this flip to the next SAR cross from
    ALL flips (scheduled or not), matching orion's NO_ENTRY close behaviour.

Both are expressed in bps and compared against a configurable round-trip cost.

## Output field definitions

All return fields are signed by signal direction: sar_up=+1 (expect up) or
sar_up=-1 (expect down). A positive signed return means the signal was correct.
1 bps = 0.01% = 1/10000 of price.

Top-level:
  n_flips              Total number of SAR flip events in the window.
  flips_per_day        n_flips / calendar days in window.
  costs_bps            Residual round-trip cost not captured by bid/ask prices.
                       Default 1 bps (slippage only: ~0.5 bps entry + ~0.5 bps exit).
                       Spread is already baked into returns via actual bid/ask execution
                       prices (BUY enters at ask, exits at bid; SELL enters at bid, exits
                       at ask), so only slippage remains here. Fee is 0 for GMO margin.

fixed_horizon[] — one entry per horizon H:
  horizon_seconds      H in seconds (e.g. 30, 120, 300, 600).
  n                    Number of flips with a valid exit price at ts+H.
  avg_signed_return_bps  Mean of (sar_up × mid_return) across all flips.
  avg_net_return_bps   avg_signed_return_bps − costs_bps.
  hit_rate             Fraction of flips where signed return > 0.
  hit_rate_net         Fraction of flips where signed return > costs_bps.
  median_signed_return_bps  Median signed return.
  p25_bps / p75_bps    25th / 75th percentile of signed returns.
  std_bps              Standard deviation of signed returns per flip.

between_flip — hold from this flip until the next SAR cross from ALL flips:
  n                    Number of flips with a valid exit (last flip excluded).
  avg_holding_seconds  Mean hold time (entry → first overall exit flip).
  median_holding_seconds  Median holding period.
  avg_signed_return_bps  Mean signed return over the holding period.
  avg_net_return_bps   avg_signed_return_bps − costs_bps.
  hit_rate             Fraction of flips where the holding-period return > 0.
  hit_rate_net         Fraction of flips where holding-period return > costs_bps.
  median_signed_return_bps  Median signed return over holding period.
  p25_bps / p75_bps    25th / 75th percentile.
  std_bps              Standard deviation.

by_month[] — between-flip stats grouped by calendar month (YYYY-MM).
by_jst_hour[] — between-flip stats grouped by JST hour of the flip.
"""

from __future__ import annotations

import datetime
from collections import deque
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_HORIZONS = [30, 120, 300, 600]
_DEFAULT_COSTS_BPS = 1.0  # round-trip slippage only; spread baked in via bid/ask

_INFLUX_URL = "http://localhost:8086"
_INFLUX_TOKEN = "panda-auth-token"
_INFLUX_ORG = "panda-org"

_ALL_SAT = list(range(24))

# Named schedule presets.  "block" means skip these flips; the complement trades.
# block_jst_hours applies weekdays (Mon–Fri); block_sat_jst_hours applies Saturdays.
SCHEDULES: dict[str, dict] = {
    "nofilter": {
        "block_jst_hours": [],
        "block_sat_jst_hours": [],
    },
    # XRP_JPY: active JST 07–21, inactive JST 22–06
    "xrp_active": {
        "block_jst_hours": [22, 23, 0, 1, 2, 3, 4, 5, 6],
        "block_sat_jst_hours": _ALL_SAT,
    },
    "xrp_inactive": {
        "block_jst_hours": [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
        "block_sat_jst_hours": [],
    },
    # ETH_JPY / BTC_JPY: active JST 06–21, inactive JST 22–05
    "default_active": {
        "block_jst_hours": [22, 23, 0, 1, 2, 3, 4, 5],
        "block_sat_jst_hours": _ALL_SAT,
    },
    "default_inactive": {
        "block_jst_hours": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
        "block_sat_jst_hours": [],
    },
}


def _features_root() -> Path:
    import qate.env.sys_env as sys_env
    from qate.store.parquet_store import Layer, ParquetStore

    return ParquetStore(sys_env.PARQUET_ROOT_DIR).layer_dir[Layer.FEATURE]


def _flip_glob(
    flip_exchange: str,
    flip_symbol: str,
    box_size_rate: float,
    sar_step: float,
    features_root: Path,
) -> str:
    return str(
        features_root
        / "dataset=ChartRangeSAR"
        / f"exchange={flip_exchange}"
        / f"symbol={flip_symbol}"
        / f"box_size_rate={box_size_rate}"
        / f"step={sar_step}"
        / "date=*"
        / "*.parquet"
    )


def _mp_glob(
    exchange: str,
    symbol: str,
    execution_size: str,
    data_root: Path,
) -> str:
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


def scan(
    flip_exchange: str,
    flip_symbol: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    box_size_rate: float = 0.0025,
    sar_step: float = 0.02,
    execution_size: str = "150",
    horizons: list[int] = _DEFAULT_HORIZONS,
    costs_bps: float = _DEFAULT_COSTS_BPS,
    block_jst_hours: list[int] | None = None,
    block_sat_jst_hours: list[int] | None = None,
    exclude_jst_dates: list[str] | None = None,
    rolling_dd_window_seconds: float | None = None,
    rolling_dd_threshold_bps: float | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    features_root: Path | None = None,
) -> dict:
    """Run a flip-anchored SAR scan.

    Returns a report dict with per-flip statistics before and after costs,
    for both fixed-horizon and between-flip return definitions.
    """
    if features_root is None:
        features_root = _features_root()

    flip_glob = _flip_glob(
        flip_exchange, flip_symbol, box_size_rate, sar_step, features_root
    )
    mp_glob = _mp_glob(target_exchange, target_symbol, execution_size, data_root)
    con = duckdb.connect()

    # ── Step 1: load all flips with actual entry execution price ─────────────
    # Uses silver MarketPrice: bid/ask are VWAP at execution_size depth (more
    # realistic than best bid0/ask0), and spread > 10 bps rows are pre-filtered.
    # BUY (sar_up=+1): enter at ask  SELL (sar_up=-1): enter at bid
    flips_df = (
        con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT f.ts, f.sar_up,
            CASE WHEN f.sar_up > 0 THEN o.ask ELSE o.bid END AS entry_price,
            (o.bid + o.ask) / 2.0 AS entry_mid,
            o.bid AS entry_bid,
            o.ask AS entry_ask
        FROM flips f ASOF JOIN mp o ON o.ts >= f.ts
        ORDER BY f.ts
    """)
        .df()
        .dropna()
    )

    if flips_df.empty:
        raise ValueError(
            f"no flip data for {flip_exchange}/{flip_symbol} in {start_date}..{end_date}"
        )

    # Save ALL flip timestamps before schedule filtering so exit_ts uses the
    # first overall SAR cross (not just the next scheduled flip), matching
    # orion's NO_ENTRY close behaviour.
    all_flip_ts = flips_df["ts"].values.copy()

    # ── Schedule filters ──────────────────────────────────────────────────────
    _JST_OFFSET = 9 * 3600
    jst_shifted = (flips_df["ts"] + _JST_OFFSET).astype("int64")
    flips_df["jst_hour"] = (jst_shifted % 86400 // 3600).astype(int)
    flips_df["jst_weekday"] = pd.to_datetime(
        jst_shifted, unit="s", utc=True
    ).dt.dayofweek
    flips_df["jst_date"] = pd.to_datetime(
        jst_shifted, unit="s", utc=True
    ).dt.date.astype(str)
    n_hour_blocked = 0
    n_sat_blocked = 0
    n_date_blocked = 0

    if exclude_jst_dates:
        mask = flips_df["jst_date"].isin(set(exclude_jst_dates))
        n_date_blocked = int(mask.sum())
        flips_df = flips_df[~mask].copy()

    if block_jst_hours:
        mask = (flips_df["jst_weekday"] <= 4) & flips_df["jst_hour"].isin(
            set(block_jst_hours)
        )
        n_hour_blocked = int(mask.sum())
        flips_df = flips_df[~mask].copy()

    if block_sat_jst_hours:
        mask = (flips_df["jst_weekday"] == 5) & flips_df["jst_hour"].isin(
            set(block_sat_jst_hours)
        )
        n_sat_blocked = int(mask.sum())
        flips_df = flips_df[~mask].copy()

    # exit_ts = first flip from ALL flips strictly after this entry's ts
    _exit_idx = np.searchsorted(all_flip_ts, flips_df["ts"].values, side="right")
    _valid_exit = _exit_idx < len(all_flip_ts)
    flips_df = flips_df.copy()
    flips_df["exit_ts"] = np.where(
        _valid_exit,
        all_flip_ts[np.clip(_exit_idx, 0, len(all_flip_ts) - 1)],
        np.nan,
    )

    # ── Rolling drawdown gate ─────────────────────────────────────────────────
    n_rolling_dd_blocked = 0
    if (
        rolling_dd_threshold_bps is not None
        and rolling_dd_window_seconds is not None
        and not flips_df.empty
    ):
        ts_arr = flips_df["ts"].values
        ep_ask_arr = flips_df["entry_ask"].values
        ep_bid_arr = flips_df["entry_bid"].values
        sar_up_arr = flips_df["sar_up"].values
        exit_ts_col = flips_df["exit_ts"].values
        entry_p = np.where(sar_up_arr > 0, ep_ask_arr, ep_bid_arr)

        _dd_exit_req = pd.DataFrame(
            {
                "entry_ts": ts_arr,
                "exit_ts": exit_ts_col,
                "sar_up": sar_up_arr,
            }
        )
        con.register("_dd_exit_req", _dd_exit_req.dropna(subset=["exit_ts"]))
        _dd_exit_mp = con.sql(f"""
            SELECT e.entry_ts,
                CASE WHEN e.sar_up > 0 THEN o.bid ELSE o.ask END AS prov_exit_price
            FROM _dd_exit_req e ASOF JOIN (
                SELECT ts, bid, ask
                FROM read_parquet('{mp_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ) o ON o.ts >= e.exit_ts
        """).df()
        _exit_price_map = dict(
            zip(_dd_exit_mp["entry_ts"], _dd_exit_mp["prov_exit_price"])
        )
        prov_exit = np.array([_exit_price_map.get(t, np.nan) for t in ts_arr])

        with np.errstate(invalid="ignore"):
            prov_ret = sar_up_arr * (prov_exit - entry_p) / entry_p * 10000
        buf = deque()
        cum_pnl = 0.0
        kept = np.ones(len(ts_arr), dtype=bool)
        for i in range(len(ts_arr)):
            t = ts_arr[i]
            while buf and buf[0][0] < t - rolling_dd_window_seconds:
                buf.popleft()
            peak = max(v for _, v in buf) if buf else cum_pnl
            if (peak - cum_pnl) > rolling_dd_threshold_bps:
                kept[i] = False
            else:
                r = prov_ret[i]
                if not np.isnan(r):
                    cum_pnl += r
                    buf.append((t, cum_pnl))
        n_rolling_dd_blocked = int((~kept).sum())
        flips_df = flips_df[kept].copy()

    n_flips_raw = (
        len(flips_df)
        + n_hour_blocked
        + n_sat_blocked
        + n_date_blocked
        + n_rolling_dd_blocked
    )
    n_flips = len(flips_df)
    n_days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days

    # ── Step 2: fixed-horizon returns ─────────────────────────────────────────
    horizon_results = []
    for h in horizons:
        exit_df = (
            con.sql(f"""
            WITH flips AS (
                SELECT ts, sar_up FROM read_parquet('{flip_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ),
            mp AS (
                SELECT ts, bid, ask
                FROM read_parquet('{mp_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
                    + INTERVAL {max(horizons) // 3600 + 1} DAY
            )
            SELECT f.ts,
                CASE WHEN f.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
            FROM flips f ASOF JOIN mp o ON o.ts >= f.ts + {h}
            ORDER BY f.ts
        """)
            .df()
            .dropna()
        )

        merged = flips_df.merge(exit_df, on="ts", how="inner")
        raw_ret = (
            (merged["exit_price"] - merged["entry_price"])
            / merged["entry_price"]
            * 10000
        )
        signed = merged["sar_up"] * raw_ret

        horizon_results.append(
            {
                "horizon_seconds": h,
                "n": len(signed),
                "avg_signed_return_bps": float(signed.mean()),
                "avg_net_return_bps": float(signed.mean() - costs_bps),
                "hit_rate": float((signed > 0).mean()),
                "hit_rate_net": float((signed > costs_bps).mean()),
                "median_signed_return_bps": float(signed.median()),
                "p25_bps": float(signed.quantile(0.25)),
                "p75_bps": float(signed.quantile(0.75)),
                "std_bps": float(signed.std()),
            }
        )

    # ── Step 3: between-flip return ───────────────────────────────────────────
    flips_df = flips_df.reset_index(drop=True)
    pairs = flips_df[["ts", "sar_up", "exit_ts"]].dropna(subset=["exit_ts"]).copy()
    pairs = pairs.rename(columns={"exit_ts": "next_ts"})
    con.register("_pairs", pairs)
    exit_ob = (
        con.sql(f"""
        WITH mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT p.ts,
            CASE WHEN p.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
        FROM _pairs p ASOF JOIN mp o ON o.ts >= p.next_ts
    """)
        .df()
        .dropna()
    )

    bf = flips_df.merge(exit_ob, on="ts", how="inner")
    bf["holding_seconds"] = bf["exit_ts"] - bf["ts"]
    raw_bf = (bf["exit_price"] - bf["entry_price"]) / bf["entry_price"] * 10000
    signed_bf = bf["sar_up"] * raw_bf

    between_flip = {
        "n": len(signed_bf),
        "avg_holding_seconds": float(bf["holding_seconds"].mean()),
        "median_holding_seconds": float(bf["holding_seconds"].median()),
        "avg_signed_return_bps": float(signed_bf.mean()),
        "avg_net_return_bps": float(signed_bf.mean() - costs_bps),
        "hit_rate": float((signed_bf > 0).mean()),
        "hit_rate_net": float((signed_bf > costs_bps).mean()),
        "median_signed_return_bps": float(signed_bf.median()),
        "p25_bps": float(signed_bf.quantile(0.25)),
        "p75_bps": float(signed_bf.quantile(0.75)),
        "std_bps": float(signed_bf.std()),
    }

    # ── Step 4: monthly breakdown (between-flip) ──────────────────────────────
    bf["month"] = pd.to_datetime(bf["ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    monthly = []
    for month, g in bf.groupby("month"):
        sr = (
            g["sar_up"]
            * (g["exit_price"] - g["entry_price"])
            / g["entry_price"]
            * 10000
        )
        monthly.append(
            {
                "month": month,
                "n_flips": len(g),
                "avg_signed_return_bps": float(sr.mean()),
                "avg_net_return_bps": float(sr.mean() - costs_bps),
                "hit_rate": float((sr > 0).mean()),
            }
        )

    # ── Step 5: time-of-day breakdown (between-flip, JST) ────────────────────
    by_hour = []
    for hour, g in bf.groupby("jst_hour"):
        sr = (
            g["sar_up"]
            * (g["exit_price"] - g["entry_price"])
            / g["entry_price"]
            * 10000
        )
        by_hour.append(
            {
                "jst_hour": int(hour),
                "n_flips": len(g),
                "avg_signed_return_bps": float(sr.mean()),
                "avg_net_return_bps": float(sr.mean() - costs_bps),
                "hit_rate": float((sr > 0).mean()),
            }
        )

    return {
        "signal_style": "skew",
        "flip_exchange": flip_exchange,
        "flip_symbol": flip_symbol,
        "target_exchange": target_exchange,
        "target_symbol": target_symbol,
        "start_date": start_date,
        "end_date": end_date,
        "costs_bps": costs_bps,
        "n_flips": n_flips,
        "flips_per_day": round(n_flips / n_days, 2),
        "box_size_rate": box_size_rate,
        "sar_step": sar_step,
        "execution_size": execution_size,
        "risk_gates": {
            "block_jst_hours": block_jst_hours or [],
            "block_sat_jst_hours": block_sat_jst_hours or [],
            "exclude_jst_dates": exclude_jst_dates or [],
            "rolling_dd_window_seconds": rolling_dd_window_seconds,
            "rolling_dd_threshold_bps": rolling_dd_threshold_bps,
            "n_hour_blocked": n_hour_blocked,
            "n_sat_blocked": n_sat_blocked,
            "n_date_blocked": n_date_blocked,
            "n_rolling_dd_blocked": n_rolling_dd_blocked,
            "n_raw_flips": n_flips_raw,
        },
        "fixed_horizon": horizon_results,
        "between_flip": between_flip,
        "by_month": monthly,
        "by_jst_hour": by_hour,
    }


def print_summary(report: dict) -> None:
    costs = report["costs_bps"]
    print(f"\n{'=' * 60}")
    print(
        f"FLIP SCAN: {report['flip_exchange']}/{report['flip_symbol']}"
        f" → {report['target_exchange']}/{report['target_symbol']}"
    )
    print(
        f"Window: {report['start_date']} .. {report['end_date']}"
        f"  box_size_rate={report['box_size_rate']}  execution_size={report['execution_size']}"
    )
    rg = report["risk_gates"]
    parts = []
    if rg.get("block_jst_hours"):
        parts.append(f"block_hours={rg['block_jst_hours']}")
    if rg.get("block_sat_jst_hours"):
        parts.append(f"block_sat_hours={rg['block_sat_jst_hours']}")
    if rg.get("exclude_jst_dates"):
        parts.append(f"exclude_jst_dates={rg['exclude_jst_dates']}")
    if rg.get("rolling_dd_threshold_bps"):
        parts.append(
            f"rolling_dd<={rg['rolling_dd_threshold_bps']}bps/{rg['rolling_dd_window_seconds']}s"
        )
    if parts:
        print(f"Gates: {' / '.join(parts)}")
        total_blocked = (
            rg.get("n_hour_blocked", 0)
            + rg.get("n_sat_blocked", 0)
            + rg.get("n_date_blocked", 0)
            + rg.get("n_rolling_dd_blocked", 0)
        )
        print(
            f"  Blocked: {rg.get('n_hour_blocked', 0)} hour"
            f" + {rg.get('n_sat_blocked', 0)} sat"
            f" + {rg.get('n_date_blocked', 0)} date"
            f" + {rg.get('n_rolling_dd_blocked', 0)} rolling_dd"
            f" = {total_blocked} of {rg['n_raw_flips']} raw flips"
        )
    print(f"N flips: {report['n_flips']}  ({report['flips_per_day']:.1f}/day)")
    print(f"Round-trip cost assumption: {costs} bps")

    print("\n--- Fixed-horizon returns (signed, per flip) ---")
    print(
        f"{'Horizon':>10}  {'Avg gross':>10}  {'Avg net':>10}  {'Hit%':>7}  {'Hit% net':>9}"
    )
    for r in report["fixed_horizon"]:
        print(
            f"{r['horizon_seconds']:>9}s  {r['avg_signed_return_bps']:>+9.2f}"
            f"  {r['avg_net_return_bps']:>+9.2f}  {r['hit_rate']:>6.1%}  {r['hit_rate_net']:>8.1%}"
        )

    bf = report["between_flip"]
    print("\n--- Between-flip returns (hold until next SAR cross) ---")
    print(
        f"  Avg holding:   {bf['avg_holding_seconds']:.0f}s  (median {bf['median_holding_seconds']:.0f}s)"
    )
    print(f"  Avg gross:     {bf['avg_signed_return_bps']:+.2f} bps")
    print(
        f"  Avg net:       {bf['avg_net_return_bps']:+.2f} bps  (after {costs} bps cost)"
    )
    print(
        f"  Hit rate:      {bf['hit_rate']:.1%} gross  /  {bf['hit_rate_net']:.1%} net"
    )
    print(
        f"  p25/med/p75:   {bf['p25_bps']:+.1f} / {bf['median_signed_return_bps']:+.1f} / {bf['p75_bps']:+.1f} bps"
    )

    print(f"\n--- Monthly breakdown (between-flip, net of {costs} bps) ---")
    print(
        f"{'Month':>8}  {'N flips':>8}  {'Avg gross':>10}  {'Avg net':>10}  {'Hit%':>7}"
    )
    for m in report["by_month"]:
        print(
            f"{m['month']:>8}  {m['n_flips']:>8}  {m['avg_signed_return_bps']:>+9.2f}"
            f"  {m['avg_net_return_bps']:>+9.2f}  {m['hit_rate']:>6.1%}"
        )
    print()


# backward-compat alias
run_flip_scan = scan


def _influx_range(start_date: str, end_date: str) -> tuple[str, str]:
    _jst = datetime.timedelta(hours=9)
    start_utc = (
        datetime.datetime.strptime(start_date, "%Y-%m-%d") - _jst
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        + datetime.timedelta(days=1) - _jst
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start_utc, end_utc


def _query_df(qapi, flux: str) -> pd.DataFrame:
    raw = qapi.query_data_frame(flux)
    if isinstance(raw, list):
        return pd.concat(raw, ignore_index=True) if raw else pd.DataFrame()
    return raw


def verify(
    flip_exchange: str,
    flip_symbol: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    box_size_rate: float,
    sar_step: float,
    execution_size: str,
    bucket: str,
    param_set_id: str,
    data_root: Path = _DEFAULT_DATA_ROOT,
    features_root: Path | None = None,
    block_jst_hours: list[int] | None = None,
    block_sat_jst_hours: list[int] | None = None,
) -> dict:
    """Compare scan flip prices and PnL against Himalaya backtest InfluxDB data.

    Compares:
      order   — flip time vs ORDER._time, flip price vs ORDER.EXEC_PRICE
      pnl     — scan between-flip PnL (JPY) vs EVAL.pnl_change0

    Returns a result dict and prints a summary.
    """
    from influxdb_client import InfluxDBClient

    if features_root is None:
        features_root = _features_root()

    flip_glob = _flip_glob(flip_exchange, flip_symbol, box_size_rate, sar_step, features_root)
    mp_glob   = _mp_glob(target_exchange, target_symbol, execution_size, data_root)
    size_f    = float(execution_size)
    con       = duckdb.connect()

    # ── 1. Scan: compute all flips with entry & exit prices ───────────────────
    flips_df = con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT f.ts, f.sar_up,
            CASE WHEN f.sar_up > 0 THEN o.ask ELSE o.bid END AS flip_price,
            o.bid AS entry_bid, o.ask AS entry_ask,
            o.ts AS mp_ts
        FROM flips f ASOF JOIN mp o ON o.ts >= f.ts
        ORDER BY f.ts
    """).df().dropna()

    if flips_df.empty:
        raise ValueError(f"no flip data for {flip_exchange}/{flip_symbol} in {start_date}..{end_date}")

    # exit_ts uses ALL flips (same as scan()'s NO_ENTRY close behaviour)
    all_ts = flips_df["ts"].values

    # ── Schedule filters (mirror scan() logic) ────────────────────────────────
    _JST_OFFSET = 9 * 3600
    if block_jst_hours or block_sat_jst_hours:
        jst_shifted = (flips_df["ts"] + _JST_OFFSET).astype("int64")
        flips_df["_jst_hour"] = (jst_shifted % 86400 // 3600).astype(int)
        flips_df["_jst_wday"] = pd.to_datetime(
            jst_shifted, unit="s", utc=True
        ).dt.dayofweek
        if block_jst_hours:
            mask = (flips_df["_jst_wday"] <= 4) & flips_df["_jst_hour"].isin(set(block_jst_hours))
            flips_df = flips_df[~mask].copy()
        if block_sat_jst_hours:
            mask = (flips_df["_jst_wday"] == 5) & flips_df["_jst_hour"].isin(set(block_sat_jst_hours))
            flips_df = flips_df[~mask].copy()
        flips_df = flips_df.drop(columns=["_jst_hour", "_jst_wday"])

    # exit_ts = first overall flip (from unfiltered all_ts) strictly after each entry
    exit_idx = np.searchsorted(all_ts, flips_df["ts"].values, side="right")
    valid = exit_idx < len(all_ts)
    flips_df["exit_ts"] = np.where(valid, all_ts[np.clip(exit_idx, 0, len(all_ts) - 1)], np.nan)

    # exit prices for between-flip PnL
    pairs = flips_df[["ts", "sar_up", "exit_ts"]].dropna(subset=["exit_ts"]).copy()
    pairs = pairs.rename(columns={"exit_ts": "next_ts"})
    con.register("_pairs", pairs)
    exit_ob = con.sql(f"""
        WITH mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT p.ts, CASE WHEN p.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
        FROM _pairs p ASOF JOIN mp o ON o.ts >= p.next_ts
    """).df().dropna()

    bf = flips_df.merge(exit_ob, on="ts", how="inner")
    # scan PnL in JPY: sar_up * (exit - entry) * size
    bf["scan_pnl"] = bf["sar_up"] * (bf["exit_price"] - bf["flip_price"]) * size_f

    # ── 2. Query InfluxDB ORDER OPEN and EVAL pnl_change0 ────────────────────
    start_utc, end_utc = _influx_range(start_date, end_date)
    client = InfluxDBClient(url=_INFLUX_URL, token=_INFLUX_TOKEN, org=_INFLUX_ORG, timeout=120_000)
    qapi = client.query_api()

    # OPEN orders: one per flip, EXEC_PRICE = actual fill price
    order_df = _query_df(qapi, f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r["_measurement"] == "ORDER")
  |> filter(fn: (r) => r["param_set_id"] == "{param_set_id}")
  |> filter(fn: (r) => r["SETTLE_TYPE"] == "OPEN")
  |> filter(fn: (r) => r["_field"] == "EXEC_PRICE")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
""")

    # EVAL pnl_change0: one per closed position, stamped at close time
    eval_df = _query_df(qapi, f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r["_measurement"] == "EVAL")
  |> filter(fn: (r) => r["param_set_id"] == "{param_set_id}")
  |> filter(fn: (r) => r["_field"] == "pnl_change0")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
""")
    client.close()

    # ── 3. Order vs flip comparison ───────────────────────────────────────────
    n_scan   = len(flips_df)
    n_orders = len(order_df)

    order_result = {"n_scan": n_scan, "n_orders": n_orders, "n_matched": 0,
                    "time_diff": {}, "price_diff": {}}

    if not order_df.empty:
        _jst = datetime.timezone(datetime.timedelta(hours=9))
        order_df["order_ts"] = order_df["_time"].apply(lambda t: t.timestamp())
        order_df = order_df.sort_values("order_ts").reset_index(drop=True)
        scan_sorted = flips_df.sort_values("ts").reset_index(drop=True)

        order_ts_arr = order_df["order_ts"].values
        scan_ts_arr  = scan_sorted["ts"].values
        mp_ts_arr    = scan_sorted["mp_ts"].values

        # Match on mp_ts (= GMO MarketPrice ts) vs order_ts with a 2s threshold.
        # True matches have mp_ts == order_ts exactly (same record); wrong cascade
        # matches from gaps would differ by minutes or hours.  Using mp_ts instead
        # of flip_ts avoids cascading when many gaps are spread across the period.
        _MATCH_THRESHOLD = 2.0
        s_to_o = np.full(len(mp_ts_arr), -1, dtype=int)    # scan idx → order idx
        o_to_s = np.full(len(order_ts_arr), -1, dtype=int)  # order idx → scan idx
        i, j = 0, 0
        while i < len(mp_ts_arr) and j < len(order_ts_arr):
            d = mp_ts_arr[i] - order_ts_arr[j]
            if abs(d) <= _MATCH_THRESHOLD:
                s_to_o[i] = j
                o_to_s[j] = i
                i += 1
                j += 1
            elif d < 0:   # scan mp_ts is earlier; no matching order close enough
                i += 1
            else:         # order is earlier; no matching scan flip close enough
                j += 1

        matched_mask_s = s_to_o >= 0
        matched_mask_o = o_to_s >= 0
        n_matched = int(matched_mask_s.sum())

        time_diff  = order_ts_arr[s_to_o[matched_mask_s]] - mp_ts_arr[matched_mask_s]
        fp_matched = scan_sorted["flip_price"].values[matched_mask_s]
        ep_matched = order_df["EXEC_PRICE"].values[s_to_o[matched_mask_s]]
        price_diff     = ep_matched - fp_matched
        price_diff_bps = price_diff / fp_matched * 10000

        # Collect gap rows for printing
        gap_scan_idx  = np.where(~matched_mask_s)[0]   # scan flips with no order
        gap_order_idx = np.where(~matched_mask_o)[0]   # orders with no scan flip

        gap_rows: list[dict] = []
        for idx in gap_scan_idx:
            row = scan_sorted.iloc[idx]
            ts_jst = datetime.datetime.fromtimestamp(row["ts"], tz=_jst)
            gap_rows.append({
                "kind":   "scan_only",
                "ts_jst": ts_jst,
                "dir":    f"sar_up={int(row['sar_up']):+d}",
                "price":  f"flip={row['flip_price']:.0f}¥",
            })
        for idx in gap_order_idx:
            row = order_df.iloc[idx]
            ts_jst = datetime.datetime.fromtimestamp(row["order_ts"], tz=_jst)
            gap_rows.append({
                "kind":   "order_only",
                "ts_jst": ts_jst,
                "dir":    row.get("SIDE", "?"),
                "price":  f"exec={row['EXEC_PRICE']:.0f}¥",
            })
        gap_rows.sort(key=lambda r: r["ts_jst"])

        order_result.update({
            "n_matched":    n_matched,
            "n_gap":        len(gap_scan_idx) + len(gap_order_idx),
            "n_scan_only":  int(len(gap_scan_idx)),
            "n_order_only": int(len(gap_order_idx)),
            "gap_rows":     gap_rows,
            "time_diff": {
                "mean_s":    float(np.mean(time_diff)),
                "std_s":     float(np.std(time_diff)),
                "max_abs_s": float(np.max(np.abs(time_diff))),
            },
            "price_diff": {
                "mean_jpy":    float(np.mean(price_diff)),
                "std_jpy":     float(np.std(price_diff)),
                "max_abs_jpy": float(np.max(np.abs(price_diff))),
                "mean_bps":    float(np.mean(price_diff_bps)),
                "std_bps":     float(np.std(price_diff_bps)),
            },
        })

    # ── 4. PnL comparison (scan between-flip vs EVAL pnl_change0) ────────────
    n_bf_scan = len(bf)
    n_eval    = len(eval_df)

    pnl_result = {"n_scan_positions": n_bf_scan, "n_eval": n_eval, "n_matched": 0,
                  "pnl_diff": {}}

    if not eval_df.empty and not bf.empty:
        eval_df["eval_ts"] = eval_df["_time"].apply(lambda t: t.timestamp())
        eval_df = eval_df.sort_values("eval_ts").reset_index(drop=True)

        # match each scan position's exit_ts to nearest EVAL timestamp
        eval_ts_arr  = eval_df["eval_ts"].values
        exit_ts_arr  = bf["exit_ts"].values
        idx = np.searchsorted(eval_ts_arr, exit_ts_arr, side="left")
        idx = np.clip(idx, 0, len(eval_ts_arr) - 1)
        idx_prev = np.clip(idx - 1, 0, len(eval_ts_arr) - 1)
        d_next = np.abs(eval_ts_arr[idx] - exit_ts_arr)
        d_prev = np.abs(eval_ts_arr[idx_prev] - exit_ts_arr)
        best_idx = np.where(d_prev < d_next, idx_prev, idx)

        scan_pnl_arr = bf["scan_pnl"].values
        eval_pnl_arr = eval_df["pnl_change0"].values[best_idx]
        pnl_diff     = scan_pnl_arr - eval_pnl_arr

        pnl_result.update({
            "n_matched": len(bf),
            "n_gap":     abs(n_bf_scan - n_eval),
            "pnl_diff": {
                "mean_jpy":    float(np.mean(pnl_diff)),
                "std_jpy":     float(np.std(pnl_diff)),
                "max_abs_jpy": float(np.max(np.abs(pnl_diff))),
                "pct_exact":   float(np.mean(np.abs(pnl_diff) < 0.01)) * 100,
            },
        })

    # ── 5. Print report ───────────────────────────────────────────────────────
    _print_verify_report(
        flip_exchange, flip_symbol, target_exchange, target_symbol,
        box_size_rate, sar_step, execution_size,
        start_date, end_date, param_set_id,
        block_jst_hours, block_sat_jst_hours,
        order_result, pnl_result,
    )

    return {"order": order_result, "pnl": pnl_result}


def _print_verify_report(
    flip_exchange: str,
    flip_symbol: str,
    target_exchange: str,
    target_symbol: str,
    box_size_rate: float,
    sar_step: float,
    execution_size: str,
    start_date: str,
    end_date: str,
    param_set_id: str,
    block_jst_hours: list[int] | None,
    block_sat_jst_hours: list[int] | None,
    order_result: dict,
    pnl_result: dict,
) -> None:
    W = 66

    # ── header ────────────────────────────────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  {flip_exchange}/{flip_symbol}  →  {target_exchange}/{target_symbol}"
          f"   box={box_size_rate}  step={sar_step}  size={execution_size}")
    print(f"  {start_date} .. {end_date}")
    print(f"  {param_set_id}")
    schedule_parts = []
    if block_jst_hours:
        schedule_parts.append(f"wday={sorted(block_jst_hours)}")
    if block_sat_jst_hours:
        schedule_parts.append(f"sat={sorted(block_sat_jst_hours)}")
    if schedule_parts:
        print(f"  block: {', '.join(schedule_parts)}")
    print(f"{'─' * W}")

    # ── coverage ──────────────────────────────────────────────────────────────
    n_scan      = order_result["n_scan"]
    n_orders    = order_result["n_orders"]
    n_matched   = order_result.get("n_matched", 0)
    n_gap       = order_result.get("n_gap", 0)
    n_scan_only = order_result.get("n_scan_only", 0)
    n_ord_only  = order_result.get("n_order_only", 0)
    match_rate  = n_matched / n_scan * 100 if n_scan else 0.0

    print(f"\n  coverage")
    print(f"    {'scan flips':<14} {n_scan:>6}    {'backtest orders':<16} {n_orders:>6}"
          f"    {'matched':<8} {n_matched:>6}  ({match_rate:.1f}%)")
    print(f"    {'gap':<14} {n_gap:>6}    {'scan_only':<16} {n_scan_only:>6}"
          f"    {'order_only':<8} {n_ord_only:>6}")

    # ── execution quality (matched pairs) ─────────────────────────────────────
    if order_result["time_diff"] and order_result["price_diff"]:
        td  = order_result["time_diff"]
        prd = order_result["price_diff"]
        print(f"\n  execution  (matched pairs, entry time = GMO mp_ts → backtest fill)")
        print(f"    {'':16}  {'mean':>10}  {'std':>10}  {'max':>10}")
        print(f"    {'time diff (s)':<16}  {td['mean_s']:>+10.3f}  {td['std_s']:>10.3f}"
              f"  {td['max_abs_s']:>10.3f}")
        print(f"    {'price diff (¥)':<16}  {prd['mean_jpy']:>+10.2f}  {prd['std_jpy']:>10.2f}"
              f"  {prd['max_abs_jpy']:>10.2f}")
        print(f"    {'price diff (bps)':<16}  {prd['mean_bps']:>+10.3f}  {prd['std_bps']:>10.3f}")

    # ── pnl comparison ────────────────────────────────────────────────────────
    n_pos     = pnl_result["n_scan_positions"]
    n_eval    = pnl_result["n_eval"]
    n_pnl_gap = pnl_result.get("n_gap", 0)
    print(f"\n  pnl  (scan between-flip JPY  vs  backtest pnl_change0)")
    print(f"    {'scan positions':<14} {n_pos:>6}    {'eval records':<16} {n_eval:>6}"
          f"    {'gap':<8} {n_pnl_gap:>6}")
    if pnl_result["pnl_diff"]:
        pd2 = pnl_result["pnl_diff"]
        print(f"    {'':16}  {'mean':>10}  {'std':>10}  {'max':>10}  {'pct_exact':>9}")
        print(f"    {'pnl diff (¥)':<16}  {pd2['mean_jpy']:>+10.2f}  {pd2['std_jpy']:>10.2f}"
              f"  {pd2['max_abs_jpy']:>10.2f}  {pd2['pct_exact']:>8.1f}%")

    # ── gap table ─────────────────────────────────────────────────────────────
    gap_rows = order_result.get("gap_rows", [])
    if gap_rows:
        print(f"\n  gaps  ({len(gap_rows)} items)")
        print(f"    {'#':<4}  {'kind':<12}  {'time (JST)':<21}  {'dir':<12}  price")
        for k, r in enumerate(gap_rows):
            print(f"    {k:<4}  {r['kind']:<12}"
                  f"  {r['ts_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                  f"  {r['dir']:<12}  {r['price']}")

    print(f"\n{'─' * W}\n")
