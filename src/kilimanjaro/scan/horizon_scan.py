"""Fixed-horizon precision-style scanner anchored on order book imbalance (OBI).

Unlike the flip-anchored skew scanner, this anchors on SIGNAL events — rows where
abs(OBI) exceeds a threshold — and measures the return over a fixed forward window H.
That matches the precision style: high hit rate, small gains per trade.

OBI source:  OBIFeature dataset (1s-resampled, EMA-smoothed)
             Path: feature/dataset=OBIFeature/exchange=.../symbol=.../date=*
Target:      GMO MarketPrice  (mid = (bid + ask) / 2)

Signal column
-------------
  obi_halflife=None  → raw vol_imb_k{depth}   (last tick per second, no smoothing)
  obi_halflife=N     → ema_k{depth}_{N}s      (exponential MA with halflife=N seconds)

EMA is preferred over raw OBI: it captures sustained imbalance pressure rather
than momentary tick noise, reducing autocorrelated signal bursts and producing
a more actionable signal. Available halflives: 10, 30, 60, 120, 300.

Signal definition
-----------------
  direction = sign(obi_value)
    +1 → long  (bid pressure > ask → expect price up)
    -1 → short (ask pressure > bid → expect price down)

Return definition
-----------------
  signed_return_bps = direction × (exit_mid − entry_mid) / entry_mid × 10000
  entry_mid: GMO mid at the first MarketPrice tick at or after signal timestamp
  exit_mid:  GMO mid at the first MarketPrice tick at or after signal_ts + horizon_seconds
  net_return_bps = signed_return_bps − costs_bps

Available depth levels: 1, 3, 5, 10
Available EMA halflives: 10, 30, 60, 120, 300 seconds

Output dict keys
----------------
signal_style         "precision"
signal_col           the OBIFeature column used as signal (e.g. "ema_k5_60s")
n_signals            signal events after schedule filters
signals_per_day      n_signals / calendar days
fixed_horizon        per-signal return stats (see below)
by_month             monthly breakdown
by_jst_hour          JST hour-of-day breakdown

fixed_horizon keys:
  n                    signals with valid exit
  avg_signed_return_bps
  avg_net_return_bps   avg_signed_return_bps − costs_bps
  hit_rate             fraction where signed_return > 0
  hit_rate_net         fraction where signed_return > costs_bps
  std_bps
  sharpe_per_trade     avg_net / std_net
  t_stat               avg_net / (std_net / √n)
  p_value_1sided       P(mean ≤ 0 | data)
  skewness
  ic                   Pearson correlation between obi value and return_bps
  total_net_bps        n × avg_net_return_bps
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from kilimanjaro.op.age import threshold_age as _threshold_age

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_COSTS_BPS = 1.0

_JST_OFFSET = 9 * 3600

_VALID_DEPTHS = {1, 3, 5, 10}
_VALID_HALFLIVES = {10, 30, 60, 120, 300}

_GMO_MAINTENANCE_SAT_HOURS: list[int] = [9, 10, 11]


def _obi_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "feature" / "dataset=OBIFeature"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / "date=*" / "*.parquet"
    )


def _mp_glob(exchange: str, symbol: str, execution_size: str, data_root: Path) -> str:
    return str(
        data_root / "silver" / "dataset=MarketPrice"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / f"execution_size={execution_size}"
        / "date=*" / "*.parquet"
    )


def _signal_col(depth: int, obi_halflife: int | None) -> str:
    if obi_halflife is None:
        return f"vol_imb_k{depth}"
    return f"ema_k{depth}_{obi_halflife}s"



def _apply_schedule_filters(
    df: pd.DataFrame,
    target_exchange: str,
    block_weekday_hours: list[int] | None,
    block_weekend_hours: list[int] | None,
) -> tuple[pd.DataFrame, int, int]:
    jst_ts = (df["ts"] + _JST_OFFSET).astype("int64")
    df = df.copy()
    df["jst_hour"] = (jst_ts % 86400 // 3600).astype(int)
    df["jst_weekday"] = pd.to_datetime(jst_ts, unit="s", utc=True).dt.dayofweek
    df["jst_date"] = pd.to_datetime(jst_ts, unit="s", utc=True).dt.date.astype(str)

    n_weekday_blocked = 0
    if block_weekday_hours:
        mask = (df["jst_weekday"] <= 4) & df["jst_hour"].isin(set(block_weekday_hours))
        n_weekday_blocked = int(mask.sum())
        df = df[~mask].copy()

    n_sat_blocked = 0
    if block_weekend_hours:
        mask = (df["jst_weekday"] >= 5) & df["jst_hour"].isin(set(block_weekend_hours))
        n_sat_blocked += int(mask.sum())
        df = df[~mask].copy()
    if target_exchange.upper() == "GMO":
        mask = (df["jst_weekday"] == 5) & df["jst_hour"].isin(_GMO_MAINTENANCE_SAT_HOURS)
        n_sat_blocked += int(mask.sum())
        df = df[~mask].copy()

    return df, n_weekday_blocked, n_sat_blocked


def _agg_stats(returns: pd.Series, obi: pd.Series, costs_bps: float) -> dict:
    n = len(returns)
    if n == 0:
        nan = float("nan")
        return {k: nan for k in [
            "avg_signed_return_bps", "avg_net_return_bps", "hit_rate", "hit_rate_net",
            "std_bps", "sharpe_per_trade", "t_stat", "p_value_1sided", "skewness", "ic",
            "total_net_bps",
        ]} | {"n": 0}

    net = returns - costs_bps
    avg_gross = float(returns.mean())
    avg_net = float(net.mean())
    std_net = float(net.std())

    if n > 1 and std_net > 0:
        from scipy import stats as _stats
        t_stat = float(avg_net / (std_net / np.sqrt(n)))
        p_value = float(_stats.t.sf(t_stat, df=n - 1))
        sharpe = avg_net / std_net
    else:
        t_stat = p_value = sharpe = float("nan")

    return {
        "n": n,
        "avg_signed_return_bps": avg_gross,
        "avg_net_return_bps": avg_net,
        "hit_rate": float((returns > 0).mean()),
        "hit_rate_net": float((returns > costs_bps).mean()),
        "std_bps": float(returns.std()),
        "sharpe_per_trade": sharpe,
        "t_stat": t_stat,
        "p_value_1sided": p_value,
        "skewness": float(net.skew()) if n > 2 else float("nan"),
        "ic": float(obi.corr(returns)) if n > 2 else float("nan"),
        "total_net_bps": float(n * avg_net),
    }


def load_obi_series(
    exchange: str,
    symbol: str,
    start_date: str,
    end_date: str,
    depth: int,
    obi_halflife: int | None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Load the full 1s-resampled OBI series for one (depth, halflife) combination.

    Returns a DataFrame with columns [ts, obi], covering the full date range
    without any threshold filtering. Pass the result to scan() via `_obi_series`
    to avoid re-loading from parquet on every threshold/horizon combo in a grid sweep.
    """
    if depth not in _VALID_DEPTHS:
        raise ValueError(f"depth must be one of {sorted(_VALID_DEPTHS)}, got {depth}")
    if obi_halflife is not None and obi_halflife not in _VALID_HALFLIVES:
        raise ValueError(f"obi_halflife must be one of {sorted(_VALID_HALFLIVES)} or None")

    col = _signal_col(depth, obi_halflife)
    obi_glob = _obi_glob(exchange, symbol, data_root)
    con = duckdb.connect()
    return con.sql(f"""
        SELECT ts, {col} AS obi
        FROM read_parquet('{obi_glob}', hive_partitioning=true)
        WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ORDER BY ts
    """).df()


def scan(
    signal_exchange: str,
    signal_symbol: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    depth: int = 5,
    threshold: float = 0.3,
    horizon_seconds: int = 60,
    obi_halflife: int | None = 60,
    max_age_seconds: int | None = None,
    execution_size: str = "0.3",
    costs_bps: float = _DEFAULT_COSTS_BPS,
    block_weekday_hours: list[int] | None = None,
    block_weekend_hours: list[int] | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    _obi_series: pd.DataFrame | None = None,
) -> dict:
    """Run a fixed-horizon OBI scan using the pre-computed OBIFeature dataset.

    Parameters
    ----------
    signal_exchange : str
        Exchange for OBIFeature source, e.g. "BINANCE".
    signal_symbol : str
        Symbol for OBIFeature, e.g. "ETH_USDT".
    target_exchange : str
        Exchange for execution price lookup, e.g. "GMO".
    target_symbol : str
        Symbol for MarketPrice, e.g. "ETH_JPY".
    depth : int
        OBI depth level: 1, 3, 5, or 10.
    threshold : float
        Minimum |OBI| to qualify as a signal event.
    horizon_seconds : int
        Fixed forward window H in seconds.
    obi_halflife : int | None
        EMA halflife in seconds (10, 30, 60, 120, 300), or None for raw OBI.
        EMA is preferred — it captures sustained imbalance rather than tick noise.
    max_age_seconds : int | None
        Maximum steps since the EMA last crossed `threshold` from below.
        age=0 = the crossing instant (EMA just reached threshold).
        age=1 = one second after crossing, etc.
        None = no filter (level signal — every second above threshold fires).
        Computed dynamically against the threshold, not the zero-crossing, so
        age=0 and abs(obi)>threshold always co-occur and produce real data.
    execution_size : str
        MarketPrice execution_size partition key.
    """
    if depth not in _VALID_DEPTHS:
        raise ValueError(f"depth must be one of {sorted(_VALID_DEPTHS)}, got {depth}")
    if obi_halflife is not None and obi_halflife not in _VALID_HALFLIVES:
        raise ValueError(f"obi_halflife must be one of {sorted(_VALID_HALFLIVES)} or None, got {obi_halflife}")

    col = _signal_col(depth, obi_halflife)

    mp_glob = _mp_glob(target_exchange, target_symbol, execution_size, data_root)
    con = duckdb.connect()

    # Use pre-loaded series if provided (grid scans), else load from parquet.
    # Full series needed so threshold_age can detect crossings from below threshold.
    if _obi_series is not None:
        obi_df = _obi_series
    else:
        obi_glob = _obi_glob(signal_exchange, signal_symbol, data_root)
        obi_df = con.sql(f"""
            SELECT ts, {col} AS obi
            FROM read_parquet('{obi_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
            ORDER BY ts
        """).df()

    # Apply threshold + dynamic age filter in pandas.
    if max_age_seconds is not None:
        obi_df["_age"] = _threshold_age(obi_df["obi"], threshold)
        obi_df = obi_df[
            (obi_df["obi"].abs() > threshold) & (obi_df["_age"] < max_age_seconds)
        ][["ts", "obi"]]
    else:
        obi_df = obi_df[obi_df["obi"].abs() > threshold][["ts", "obi"]]

    con.register("_signals", obi_df)

    signals_df = con.sql(f"""
        WITH mp AS (
            SELECT ts, (bid + ask) / 2.0 AS mid
            FROM read_parquet('{mp_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        ),
        with_entry AS (
            SELECT s.ts, s.obi,
                   e.ts  AS entry_exec_ts,
                   e.mid AS entry_mid
            FROM _signals s ASOF JOIN mp e ON e.ts >= s.ts
        ),
        with_exit AS (
            SELECT e.ts, e.obi, e.entry_exec_ts, e.entry_mid,
                   x.ts  AS exit_exec_ts,
                   x.mid AS exit_mid
            FROM with_entry e ASOF LEFT JOIN mp x ON x.ts >= e.ts + {horizon_seconds}
        )
        SELECT ts, obi, entry_exec_ts, entry_mid, exit_exec_ts, exit_mid,
               SIGN(obi) AS direction,
               SIGN(obi) * (exit_mid - entry_mid) / entry_mid * 10000.0 AS return_bps
        FROM with_exit
        ORDER BY ts
    """).df().dropna(subset=["entry_mid"])

    if signals_df.empty:
        raise ValueError(
            f"no OBIFeature data for {signal_exchange}/{signal_symbol}"
            f" in {start_date}..{end_date} with depth={depth} halflife={obi_halflife} threshold={threshold}"
        )

    signals_df, n_weekday_blocked, n_sat_blocked = _apply_schedule_filters(
        signals_df, target_exchange, block_weekday_hours, block_weekend_hours
    )

    n_signals = len(signals_df)
    n_days = max((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days, 1)

    valid = signals_df.dropna(subset=["return_bps"])
    fh = _agg_stats(valid["return_bps"], valid["obi"], costs_bps)

    valid = valid.copy()
    valid["month"] = pd.to_datetime(valid["ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    by_month = []
    for month, g in valid.groupby("month"):
        s = _agg_stats(g["return_bps"], g["obi"], costs_bps)
        by_month.append({"month": month, "n": s["n"], "avg_gross": s["avg_signed_return_bps"],
                         "avg_net": s["avg_net_return_bps"], "hit_rate": s["hit_rate"], "ic": s["ic"]})

    by_hour = []
    for hour, g in valid.groupby("jst_hour"):
        s = _agg_stats(g["return_bps"], g["obi"], costs_bps)
        by_hour.append({"jst_hour": int(hour), "n": s["n"], "avg_gross": s["avg_signed_return_bps"],
                        "avg_net": s["avg_net_return_bps"], "hit_rate": s["hit_rate"], "ic": s["ic"]})

    return {
        "signal_style": "precision",
        "signal_exchange": signal_exchange,
        "signal_symbol": signal_symbol,
        "target_exchange": target_exchange,
        "target_symbol": target_symbol,
        "start_date": start_date,
        "end_date": end_date,
        "depth": depth,
        "signal_col": col,
        "obi_halflife": obi_halflife,
        "max_age_seconds": max_age_seconds,
        "threshold": threshold,
        "horizon_seconds": horizon_seconds,
        "execution_size": execution_size,
        "costs_bps": costs_bps,
        "n_signals": n_signals,
        "signals_per_day": round(n_signals / n_days, 1),
        "risk_gates": {
            "block_weekday_hours": block_weekday_hours or [],
            "block_weekend_hours": block_weekend_hours or [],
            "n_weekday_blocked": n_weekday_blocked,
            "n_sat_blocked": n_sat_blocked,
        },
        "fixed_horizon": fh,
        "by_month": by_month,
        "by_jst_hour": by_hour,
    }


def print_summary(report: dict) -> None:
    costs = report["costs_bps"]
    fh = report["fixed_horizon"]
    rg = report["risk_gates"]
    print(f"\n{'=' * 60}")
    print(
        f"HORIZON SCAN: {report['signal_exchange']}/{report['signal_symbol']}"
        f" → {report['target_exchange']}/{report['target_symbol']}"
    )
    print(
        f"Window: {report['start_date']} .. {report['end_date']}"
        f"  signal={report['signal_col']}  threshold={report['threshold']}"
        f"  H={report['horizon_seconds']}s"
    )
    if rg.get("block_weekday_hours") or rg.get("block_weekend_hours"):
        print(f"Gates: block_weekday={rg['block_weekday_hours']}  block_weekend={rg['block_weekend_hours']}")
    print(f"N signals: {report['n_signals']}  ({report['signals_per_day']:.1f}/day)")
    print(f"Round-trip cost: {costs} bps")

    print("\n--- Fixed-horizon returns ---")
    print(f"  N with valid exit:  {fh['n']}")
    print(f"  Avg gross:          {fh['avg_signed_return_bps']:+.3f} bps")
    print(f"  Avg net:            {fh['avg_net_return_bps']:+.3f} bps  (after {costs} bps)")
    print(f"  Hit rate:           {fh['hit_rate']:.1%} gross  /  {fh['hit_rate_net']:.1%} net")
    print(f"  Std:                {fh['std_bps']:.3f} bps")
    print(f"  Sharpe/trade:       {fh['sharpe_per_trade']:+.4f}")
    print(f"  t-stat / p-value:   {fh['t_stat']:+.2f}  /  {fh['p_value_1sided']:.5f}")
    print(f"  IC (OBI↔return):    {fh['ic']:+.4f}")
    print(f"  Skewness:           {fh['skewness']:+.3f}")
    print(f"  Total net:          {fh['total_net_bps']:+.0f} bps")

    print(f"\n--- Monthly (net of {costs} bps) ---")
    print(f"  {'Month':>8}  {'N':>7}  {'Avg gross':>10}  {'Avg net':>10}  {'Hit%':>7}  {'IC':>7}")
    for m in report["by_month"]:
        print(
            f"  {m['month']:>8}  {m['n']:>7}"
            f"  {m['avg_gross']:>+9.3f}  {m['avg_net']:>+9.3f}"
            f"  {m['hit_rate']:>6.1%}  {m['ic']:>+6.4f}"
        )
    print()
