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
_DEFAULT_COSTS_BPS = 1.0  # round-trip slippage only; spread baked in via bid/ask
_DEFAULT_ROLLING_DD_WINDOW_SECONDS = 21600.0
_DEFAULT_ROLLING_DD_THRESHOLD_BPS = 0.0  # disabled by default; scan starts fresh unlike backtest

_INFLUX_URL = "http://localhost:8086"
_INFLUX_TOKEN = "panda-auth-token"
_INFLUX_ORG = "panda-org"

_ALL_SAT = list(range(24))

# Dates to always block (GMO system downtime, market closures, etc.)
_DEFAULT_BLOCK_JST_DATES: list[str] = [
    "2026-04-02",
    "2026-04-03",
    "2026-04-04",
    "2026-04-05",
    "2026-04-06",
    "2026-04-07",
    "2026-04-08",
    "2026-04-09",
]

# GMO exchange Saturday maintenance window (JST hours, inclusive)
_GMO_MAINTENANCE_SAT_HOURS: list[int] = [9, 10, 11]

# Named schedule presets.  "block" means skip these flips; the complement trades.
# block_weekday_hours applies Mon–Fri; block_weekend_hours applies Sat–Sun.
SCHEDULES: dict[str, dict] = {
    "nofilter": {
        "block_weekday_hours": [],
        "block_weekend_hours": [],
    },
    # XRP_JPY: active JST 07–21, inactive JST 22–06
    "xrp_active": {
        "block_weekday_hours": [22, 23, 0, 1, 2, 3, 4, 5, 6],
        "block_weekend_hours": _ALL_SAT,
    },
    "xrp_inactive": {
        "block_weekday_hours": [
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
        ],
        "block_weekend_hours": [],
    },
    # ETH_JPY / BTC_JPY: active JST 06–21, inactive JST 22–05
    "default_active": {
        "block_weekday_hours": [22, 23, 0, 1, 2, 3, 4, 5],
        "block_weekend_hours": _ALL_SAT,
    },
    "default_inactive": {
        "block_weekday_hours": [
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
        ],
        "block_weekend_hours": [],
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


def _apply_schedule_filters(
    flips_df: pd.DataFrame,
    target_exchange: str,
    block_weekday_hours: list[int] | None,
    block_weekend_hours: list[int] | None,
    exclude_jst_dates: list[str] | None,
) -> tuple[pd.DataFrame, int, int, int]:
    """Filter flips by schedule rules. Expects jst_hour, jst_weekday, jst_date columns.

    Returns (filtered_df, n_date_blocked, n_weekday_blocked, n_sat_blocked).
    n_sat_blocked combines block_weekend_hours (Sat+Sun) and GMO maintenance (Sat).
    Flips with null exit_ts (last flip) are never hour/weekday blocked.
    """
    has_exit = flips_df["exit_ts"].notna()

    # Merge caller-supplied dates with hardcoded defaults
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


def _apply_risk_filters(
    flips_df: pd.DataFrame,
    rolling_dd_window_seconds: float,
    rolling_dd_threshold_bps: float,
) -> tuple[pd.DataFrame, int]:
    """Rolling drawdown gate.

    Block flip[i] if the cumulative PnL within the last rolling_dd_window_seconds
    has drawn down more than rolling_dd_threshold_bps from its in-window peak.
    Only active when both parameters are > 0.

    Requires columns: ts, exit_ts (NaN for last flip), pnl_bps (NaN for last flip).
    Returns (filtered_df, n_blocked).
    """
    if flips_df.empty:
        return flips_df, 0

    if rolling_dd_threshold_bps <= 0 or rolling_dd_window_seconds <= 0:
        return flips_df, 0

    ts_arr = flips_df["ts"].values
    exit_ts_arr = flips_df["exit_ts"].values
    pnl_bps_arr = flips_df["pnl_bps"].values

    window_trades: deque[tuple[float, float]] = deque()  # (exit_ts, pnl_bps)
    kept = np.ones(len(ts_arr), dtype=bool)

    for i in range(len(ts_arr)):
        t_exit = exit_ts_arr[i] if not np.isnan(exit_ts_arr[i]) else ts_arr[i]

        while window_trades and window_trades[0][0] < t_exit - rolling_dd_window_seconds:
            window_trades.popleft()

        running = 0.0
        peak = 0.0
        for _, r in window_trades:
            running += r
            if running > peak:
                peak = running
        current_dd = peak - running

        if current_dd > rolling_dd_threshold_bps:
            kept[i] = False
            continue

        r = pnl_bps_arr[i]
        if not np.isnan(r):
            window_trades.append((t_exit, r))

    n_blocked = int((~kept).sum())
    return flips_df[kept].copy(), n_blocked


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
    costs_bps: float = _DEFAULT_COSTS_BPS,
    block_weekday_hours: list[int] | None = None,
    block_weekend_hours: list[int] | None = None,
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

    # ── Step 1: load all flips with entry and exit execution prices ───────────
    # One SQL builds each row as a complete trade: entry flip → ASOF JOIN for
    # entry exec → LEAD for the next flip (exit) → ASOF LEFT JOIN for exit exec.
    # exit_ts is the first overall SAR cross (NO_ENTRY close behaviour); the
    # exit is from ALL flips, so schedule-filtered flips still act as exits.
    # BUY (sar_up=+1): enter at ask, exit at bid.  SELL: enter at bid, exit at ask.
    flips_df = (
        con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        flips_paired AS (
            SELECT ts, sar_up,
                LEAD(ts) OVER (ORDER BY ts) AS exit_ts
            FROM flips
        ),
        mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        with_entry AS (
            SELECT
                f.ts, f.sar_up, f.exit_ts,
                o.ts AS entry_exec_ts,
                o.bid AS entry_bid,
                o.ask AS entry_ask,
                CASE WHEN f.sar_up > 0 THEN o.ask ELSE o.bid END AS entry_price
            FROM flips_paired f ASOF JOIN mp o ON o.ts >= f.ts
        ),
        with_exit AS (
            SELECT
                e.ts, e.sar_up, e.entry_exec_ts, e.entry_price, e.entry_bid, e.entry_ask,
                e.exit_ts,
                x.ts AS exit_exec_ts,
                CASE WHEN e.sar_up > 0 THEN x.bid ELSE x.ask END AS exit_price
            FROM with_entry e ASOF LEFT JOIN mp x ON x.ts >= e.exit_ts
        )
        SELECT *,
            sar_up * (exit_price - entry_price)                       AS pnl,
            sar_up * (exit_price - entry_price) / entry_price * 10000 AS pnl_bps
        FROM with_exit
        ORDER BY ts
    """)
        .df()
        .dropna(subset=["entry_price"])
    )

    if flips_df.empty:
        raise ValueError(
            f"no flip data for {flip_exchange}/{flip_symbol} in {start_date}..{end_date}"
        )

    # ── JST columns (used for schedule filtering and time-of-day breakdown) ───
    # jst_hour/weekday from exit_ts (backtest re-entry decision point).
    # jst_date from ts (entry date for date blocking).
    _JST = 9 * 3600
    exit_jst = (flips_df["exit_ts"].fillna(flips_df["ts"]) + _JST).astype("int64")
    flips_df["jst_hour"] = (exit_jst % 86400 // 3600).astype(int)
    flips_df["jst_weekday"] = pd.to_datetime(exit_jst, unit="s", utc=True).dt.dayofweek
    ts_jst = (flips_df["ts"] + _JST).astype("int64")
    flips_df["jst_date"] = pd.to_datetime(ts_jst, unit="s", utc=True).dt.date.astype(
        str
    )

    # ── Schedule filters ──────────────────────────────────────────────────────
    flips_df, n_date_blocked, n_weekday_blocked, n_sat_blocked = (
        _apply_schedule_filters(
            flips_df,
            target_exchange,
            block_weekday_hours,
            block_weekend_hours,
            exclude_jst_dates,
        )
    )

    # ── Rolling drawdown gate ─────────────────────────────────────────────────
    flips_df, n_rolling_dd_blocked = _apply_risk_filters(
        flips_df,
        rolling_dd_window_seconds if rolling_dd_window_seconds is not None else _DEFAULT_ROLLING_DD_WINDOW_SECONDS,
        rolling_dd_threshold_bps if rolling_dd_threshold_bps is not None else _DEFAULT_ROLLING_DD_THRESHOLD_BPS,
    )

    n_flips = len(flips_df)
    n_days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days

    # ── Step 2: between-flip return ───────────────────────────────────────────
    flips_df = flips_df.reset_index(drop=True)
    bf = flips_df.dropna(subset=["pnl_bps"]).copy()
    bf["holding_seconds"] = bf["exit_exec_ts"] - bf["entry_exec_ts"]

    between_flip = {
        "n": len(bf),
        "avg_holding_seconds": float(bf["holding_seconds"].mean()),
        "median_holding_seconds": float(bf["holding_seconds"].median()),
        "avg_signed_return_bps": float(bf["pnl_bps"].mean()),
        "avg_net_return_bps": float(bf["pnl_bps"].mean() - costs_bps),
        "hit_rate": float((bf["pnl_bps"] > 0).mean()),
        "hit_rate_net": float((bf["pnl_bps"] > costs_bps).mean()),
        "median_signed_return_bps": float(bf["pnl_bps"].median()),
        "p25_bps": float(bf["pnl_bps"].quantile(0.25)),
        "p75_bps": float(bf["pnl_bps"].quantile(0.75)),
        "std_bps": float(bf["pnl_bps"].std()),
    }

    # ── Step 4: monthly breakdown (between-flip) ──────────────────────────────
    bf["month"] = pd.to_datetime(bf["exit_ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    monthly = []
    for month, g in bf.groupby("month"):
        monthly.append(
            {
                "month": month,
                "n_flips": len(g),
                "avg_signed_return_bps": float(g["pnl_bps"].mean()),
                "avg_net_return_bps": float(g["pnl_bps"].mean() - costs_bps),
                "hit_rate": float((g["pnl_bps"] > 0).mean()),
            }
        )

    # ── Step 5: time-of-day breakdown (between-flip, JST) ────────────────────
    by_hour = []
    for hour, g in bf.groupby("jst_hour"):
        by_hour.append(
            {
                "jst_hour": int(hour),
                "n_flips": len(g),
                "avg_signed_return_bps": float(g["pnl_bps"].mean()),
                "avg_net_return_bps": float(g["pnl_bps"].mean() - costs_bps),
                "hit_rate": float((g["pnl_bps"] > 0).mean()),
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
            "block_weekday_hours": block_weekday_hours or [],
            "block_weekend_hours": block_weekend_hours or [],
            "exclude_jst_dates": exclude_jst_dates or [],
            "rolling_dd_window_seconds": rolling_dd_window_seconds,
            "rolling_dd_threshold_bps": rolling_dd_threshold_bps,
            "n_weekday_blocked": n_weekday_blocked,
            "n_sat_blocked": n_sat_blocked,
            "n_date_blocked": n_date_blocked,
            "n_rolling_dd_blocked": n_rolling_dd_blocked,
        },
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
    if rg.get("block_weekday_hours"):
        parts.append(f"block_weekday={rg['block_weekday_hours']}")
    if rg.get("block_weekend_hours"):
        parts.append(f"block_weekend={rg['block_weekend_hours']}")
    if rg.get("exclude_jst_dates"):
        parts.append(f"exclude_jst_dates={rg['exclude_jst_dates']}")
    if rg.get("rolling_dd_threshold_bps"):
        parts.append(
            f"rolling_dd<={rg['rolling_dd_threshold_bps']}bps/{rg['rolling_dd_window_seconds']}s"
        )
    if parts:
        print(f"Gates: {' / '.join(parts)}")
        total_blocked = (
            rg.get("n_weekday_blocked", 0)
            + rg.get("n_sat_blocked", 0)
            + rg.get("n_date_blocked", 0)
            + rg.get("n_rolling_dd_blocked", 0)
        )
        print(
            f"  Blocked: {rg.get('n_weekday_blocked', 0)} weekday"
            f" + {rg.get('n_sat_blocked', 0)} weekend/sat"
            f" + {rg.get('n_date_blocked', 0)} date"
            f" + {rg.get('n_rolling_dd_blocked', 0)} rolling_dd"
            f" = {total_blocked} blocked"
        )
    print(f"N flips: {report['n_flips']}  ({report['flips_per_day']:.1f}/day)")
    print(f"Round-trip cost assumption: {costs} bps")

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
    start_utc = (datetime.datetime.strptime(start_date, "%Y-%m-%d") - _jst).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    end_utc = (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        + datetime.timedelta(days=1)
        - _jst
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
    out: Path | str | None = None,
) -> dict:
    """Compare scan flip prices and PnL against Himalaya backtest InfluxDB data.

    Compares:
      order   — flip time vs ORDER._time, flip price vs ORDER.EXEC_PRICE
      pnl     — scan between-flip PnL (JPY) vs EVAL.pnl_change0

    GMO Saturday maintenance (JST) is blocked automatically when
    target_exchange == "GMO". Default block dates (_DEFAULT_BLOCK_JST_DATES) are
    always applied.

    Returns a result dict and prints a summary.
    """
    from influxdb_client import InfluxDBClient

    if features_root is None:
        features_root = _features_root()

    flip_glob = _flip_glob(
        flip_exchange, flip_symbol, box_size_rate, sar_step, features_root
    )
    mp_glob = _mp_glob(target_exchange, target_symbol, execution_size, data_root)
    size_f = float(execution_size)
    con = duckdb.connect()

    # ── 1. Scan: compute all flips with entry prices and LEAD-based exit_ts ───
    flips_df = (
        con.sql(f"""
        WITH flips AS (
            SELECT ts, sar_up
            FROM read_parquet('{flip_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        flips_paired AS (
            SELECT ts, sar_up,
                LEAD(ts) OVER (ORDER BY ts) AS exit_ts
            FROM flips
        ),
        mp AS (
            SELECT ts, bid, ask
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        )
        SELECT f.ts, f.sar_up, f.exit_ts,
            CASE WHEN f.sar_up > 0 THEN o.ask ELSE o.bid END AS flip_price,
            o.bid AS entry_bid, o.ask AS entry_ask,
            o.ts AS mp_ts
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

    # ── JST columns ───────────────────────────────────────────────────────────
    _JST = 9 * 3600
    exit_jst = (flips_df["exit_ts"].fillna(flips_df["ts"]) + _JST).astype("int64")
    flips_df["jst_hour"] = (exit_jst % 86400 // 3600).astype(int)
    flips_df["jst_weekday"] = pd.to_datetime(exit_jst, unit="s", utc=True).dt.dayofweek
    ts_jst = (flips_df["ts"] + _JST).astype("int64")
    flips_df["jst_date"] = pd.to_datetime(ts_jst, unit="s", utc=True).dt.date.astype(
        str
    )

    # ── Schedule filters ──────────────────────────────────────────────────────
    flips_df, _, _, _ = _apply_schedule_filters(
        flips_df,
        target_exchange,
        None,
        None,
        None,
    )
    flips_df = flips_df.drop(
        columns=[
            c for c in ["jst_hour", "jst_weekday", "jst_date"] if c in flips_df.columns
        ]
    )

    # exit prices for between-flip PnL
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
        SELECT p.ts, CASE WHEN p.sar_up > 0 THEN o.bid ELSE o.ask END AS exit_price
        FROM _pairs p ASOF JOIN mp o ON o.ts >= p.next_ts
    """)
        .df()
        .dropna()
    )

    bf = flips_df.merge(exit_ob, on="ts", how="inner")
    # scan PnL in JPY: sar_up * (exit - entry) * size
    bf["scan_pnl"] = bf["sar_up"] * (bf["exit_price"] - bf["flip_price"]) * size_f

    # ── 2. Query InfluxDB ORDER OPEN and EVAL pnl_change0 ────────────────────
    start_utc, end_utc = _influx_range(start_date, end_date)
    client = InfluxDBClient(
        url=_INFLUX_URL, token=_INFLUX_TOKEN, org=_INFLUX_ORG, timeout=120_000
    )
    qapi = client.query_api()

    # OPEN orders: one per flip, EXEC_PRICE = actual fill price
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

    # EVAL pnl_change0: one per closed position, stamped at close time
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

    # ── 3. Order vs flip comparison ───────────────────────────────────────────
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

        # Match on mp_ts (= GMO MarketPrice ts) vs order_ts with a 2s threshold.
        # True matches have mp_ts == order_ts exactly (same record); wrong cascade
        # matches from gaps would differ by minutes or hours.  Using mp_ts instead
        # of flip_ts avoids cascading when many gaps are spread across the period.
        _MATCH_THRESHOLD = 2.0
        s_to_o = np.full(len(mp_ts_arr), -1, dtype=int)  # scan idx → order idx
        o_to_s = np.full(len(order_ts_arr), -1, dtype=int)  # order idx → scan idx
        i, j = 0, 0
        while i < len(mp_ts_arr) and j < len(order_ts_arr):
            d = mp_ts_arr[i] - order_ts_arr[j]
            if abs(d) <= _MATCH_THRESHOLD:
                s_to_o[i] = j
                o_to_s[j] = i
                i += 1
                j += 1
            elif d < 0:  # scan mp_ts is earlier; no matching order close enough
                i += 1
            else:  # order is earlier; no matching scan flip close enough
                j += 1

        matched_mask_s = s_to_o >= 0
        matched_mask_o = o_to_s >= 0
        n_matched = int(matched_mask_s.sum())

        time_diff = order_ts_arr[s_to_o[matched_mask_s]] - mp_ts_arr[matched_mask_s]
        fp_matched = scan_sorted["flip_price"].values[matched_mask_s]
        ep_matched = order_df["EXEC_PRICE"].values[s_to_o[matched_mask_s]]
        price_diff = ep_matched - fp_matched
        price_diff_bps = price_diff / fp_matched * 10000

        # Collect gap rows for printing
        gap_scan_idx = np.where(~matched_mask_s)[0]  # scan flips with no order
        gap_order_idx = np.where(~matched_mask_o)[0]  # orders with no scan flip

        gap_rows: list[dict] = []
        for idx in gap_scan_idx:
            row = scan_sorted.iloc[idx]
            ts_jst = datetime.datetime.fromtimestamp(row["ts"], tz=_jst)
            gap_rows.append(
                {
                    "kind": "scan_only",
                    "ts_jst": ts_jst,
                    "dir": f"sar_up={int(row['sar_up']):+d}",
                    "price": f"flip={row['flip_price']:.0f}¥",
                }
            )
        for idx in gap_order_idx:
            row = order_df.iloc[idx]
            ts_jst = datetime.datetime.fromtimestamp(row["order_ts"], tz=_jst)
            gap_rows.append(
                {
                    "kind": "order_only",
                    "ts_jst": ts_jst,
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

    # ── 4. PnL comparison (scan between-flip vs EVAL pnl_change0) ────────────
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

        # Two-pointer threshold match on exit_ts vs eval_ts (same 2s threshold)
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
        n_scan_only_p = int((~matched_mask_p).sum())
        n_eval_only_p = int((~matched_mask_e).sum())

        _jst = datetime.timezone(datetime.timedelta(hours=9))
        matched_bf = bf_sorted[matched_mask_p].reset_index(drop=True)
        scan_pnl_arr = matched_bf["scan_pnl"].values
        eval_pnl_arr = eval_df["pnl_change0"].values[p_to_e[matched_mask_p]]
        pnl_diff = scan_pnl_arr - eval_pnl_arr

        # scan_only rows (scan position with no matching eval record)
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

        # eval_only rows (backtest record with no matching scan position)
        eval_only_df = eval_df[~matched_mask_e].reset_index(drop=True)
        eval_only_rows = [
            {
                "eval_jst": datetime.datetime.fromtimestamp(row["eval_ts"], tz=_jst),
                "eval_pnl": float(row["pnl_change0"]),
            }
            for _, row in eval_only_df.iterrows()
        ]

        # Top-10 largest absolute PnL diff rows, excluding near-zero diffs
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
                "n_scan_only": n_scan_only_p,
                "n_eval_only": n_eval_only_p,
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

    # ── 5. Print report ───────────────────────────────────────────────────────
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_verify_report(
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
    order_result: dict,
    pnl_result: dict,
) -> None:
    W = 66

    # ── header ────────────────────────────────────────────────────────────────
    print(f"\n{'─' * W}")
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

    # ── coverage ──────────────────────────────────────────────────────────────
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

    # ── execution quality (matched pairs) ─────────────────────────────────────
    if order_result["time_diff"] and order_result["price_diff"]:
        td = order_result["time_diff"]
        prd = order_result["price_diff"]
        print("\n  execution  (matched pairs, entry time = GMO mp_ts → backtest fill)")
        print(f"    {'':16}  {'mean':>10}  {'std':>10}  {'max':>10}")
        print(
            f"    {'time diff (s)':<16}  {td['mean_s']:>+10.3f}  {td['std_s']:>10.3f}"
            f"  {td['max_abs_s']:>10.3f}"
        )
        print(
            f"    {'price diff (¥)':<16}  {prd['mean_jpy']:>+10.2f}  {prd['std_jpy']:>10.2f}"
            f"  {prd['max_abs_jpy']:>10.2f}"
        )
        print(
            f"    {'price diff (bps)':<16}  {prd['mean_bps']:>+10.3f}  {prd['std_bps']:>10.3f}"
        )

    # ── pnl comparison ────────────────────────────────────────────────────────
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

    # ── scan_only / eval_only PnL rows ───────────────────────────────────────
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
                    f"  {r['sar_up']:>+4}"
                    f"  {r['scan_pnl']:>10.2f}"
                )
        if eval_only_rows:
            print(f"    eval_only ({len(eval_only_rows)}):")
            print(f"    {'exit (JST)':<21}  {'eval_pnl':>10}")
            for r in eval_only_rows:
                print(
                    f"    {r['eval_jst'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                    f"  {r['eval_pnl']:>10.2f}"
                )

    # ── top PnL diff (matched pairs, largest |diff|, excluding <0.01¥) ───────
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
                f"  {r['sar_up']:>+4}"
                f"  {r['scan_pnl']:>10.2f}"
                f"  {r['eval_pnl']:>10.2f}"
                f"  {r['diff']:>+10.2f}"
            )

    # ── unmatched orders ──────────────────────────────────────────────────────
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
