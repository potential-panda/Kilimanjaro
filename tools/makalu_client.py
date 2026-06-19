#!/usr/bin/env python3
"""Bridge to Makalu's own populate CLI.

The architecture principle (direct instruction): Makalu manages the data,
Kilimanjaro only consumes it. For any dataset Makalu already implements as
a populate pipeline, Kilimanjaro checks coverage for the needed scope
first, calls Makalu's populate CLI if missing, then reads the result --
it never recomputes something Makalu already has a pipeline for.

Currently wired for two datasets:
  - Return (forward mid-price returns at fixed horizons) -- a clean swap,
    identical definition to what tools/scanner.py used to compute itself
    via ASOF join.
  - LeadLag -- mapped from Makalu's own window x span grid of columns into
    the same shape tools/scanner.py's bronze-direct _measured_lead_lag
    already produces, so nothing downstream needs to change.

Deliberately NOT wired for VolSpread: its "EMA of realized volatility and
spread percentage" is a genuinely different metric from
tools/volatility_wave.py's own "probability price clears Nx spread within
horizon Y" -- forcing that swap would misrepresent what's being measured,
not just relocate where it's computed. volatility_wave.py stays
bronze-direct.

There's no Makalu equivalent for fill probability at all -- that also
stays Kilimanjaro-only, by necessity, not choice.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

_MAKALU_ROOT = Path("/home/lulurun/workspace/Makalu")
_MAKALU_DATA_ROOT = Path("/panda/makalu-data")
_MAKALU_CLI = _MAKALU_ROOT / "src" / "cli.py"

# Matches Makalu's own pipeline.market_pair.EXECUTION_SIZE convention --
# kept as a small, separate copy rather than importing across repos.
EXECUTION_SIZE = {"BTC": "0.002", "ETH": "0.1", "XRP": "150", "SOL": "1"}

_DEFAULT_HORIZONS = (30, 120, 300)
# The "Mid" LeadLag config (window/span grid in seconds); window=60s,
# span=30s is the cell used as the primary read -- closest in spirit to
# scanner.py's own +/-10s lag search.
_LEADLAG_PARAM_ID = "l0_5100_100_s30_60_120_w60_120_180_300_wsz3600_wst30_lb10"
_LEADLAG_MAX_LAG_MS = 5100.0
_LEADLAG_WINDOW_S, _LEADLAG_SPAN_S = "60s", "30s"

_LEAD_LAG_PAIR_SHORTHAND = {
    ("BINANCE", "BTC_USDT", "GMO", "BTC_JPY"): "btc",
    ("BINANCE", "ETH_USDT", "GMO", "ETH_JPY"): "eth",
    ("BINANCE", "XRP_USDT", "GMO", "XRP_JPY"): "xrp",
}


def asset_of(symbol: str) -> str:
    return symbol.split("_")[0]


def execution_size_for(symbol: str) -> str | None:
    return EXECUTION_SIZE.get(asset_of(symbol))


def makalu_available() -> bool:
    return _MAKALU_CLI.is_file()


def _run_populate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MAKALU_CLI), "populate", *args],
        cwd=str(_MAKALU_ROOT),
        capture_output=True,
        text=True,
    )


def _distinct_dates(glob: str, start_date: str, end_date: str) -> set:
    con = duckdb.connect()
    try:
        df = con.sql(
            f"SELECT DISTINCT date FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true) "
            f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'"
        ).df()
    except duckdb.IOException:
        return set()
    return set(pd.to_datetime(df["date"]).dt.date)


def return_param_id(execution_size: str, horizons: tuple[int, ...] = _DEFAULT_HORIZONS, resample_interval: str = "1s") -> str:
    return f"ret_{execution_size}_{'_'.join(str(h) for h in horizons)}_{resample_interval}"


def return_glob(exchange: str, symbol: str, param_id: str) -> str:
    return str(
        _MAKALU_DATA_ROOT / "research" / "dataset=Label_Return" / f"exchange={exchange}" / f"symbol={symbol}"
        / f"param_id={param_id}" / "date=*" / "*.parquet"
    )


def ensure_return(
    exchange: str, symbol: str, start_date: str, end_date: str, horizons: tuple[int, ...] = _DEFAULT_HORIZONS
) -> bool:
    """True if Return data for this scope is available after this call
    (already present, or freshly populated). A real upstream gap inside
    the range still returns True -- this only confirms populate ran
    without error, not that every single day is covered; callers should
    treat any missing day within the result as a disclosed gap, not an
    error, the same way every other wave tool in this project does."""
    execution_size = execution_size_for(symbol)
    if execution_size is None or not makalu_available():
        return False
    param_id = return_param_id(execution_size, horizons)
    glob = return_glob(exchange, symbol, param_id)
    needed = set(pd.date_range(start_date, end_date).date)
    if needed - _distinct_dates(glob, start_date, end_date):
        result = _run_populate(
            "Return", "--exchange", exchange, "--symbol", symbol,
            "--execution-size", execution_size, "--start", start_date, "--end", end_date,
            "--horizons", ",".join(str(h) for h in horizons),
        )
        if result.returncode != 0:
            return False
    return True


def read_return(
    exchange: str, symbol: str, start_date: str, end_date: str, horizons: tuple[int, ...] = _DEFAULT_HORIZONS
) -> pd.DataFrame | None:
    execution_size = execution_size_for(symbol)
    if execution_size is None:
        return None
    param_id = return_param_id(execution_size, horizons)
    glob = return_glob(exchange, symbol, param_id)
    con = duckdb.connect()
    try:
        df = con.sql(
            f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}' ORDER BY ts"
        ).df()
    except duckdb.IOException:
        return None
    return df if not df.empty else None


def _leadlag_glob(feature_exchange: str, feature_symbol: str, target_exchange: str, target_symbol: str) -> str:
    return str(
        _MAKALU_DATA_ROOT / "gold" / "dataset=LeadLag" / f"market_a={feature_exchange}:{feature_symbol}"
        / f"market_b={target_exchange}:{target_symbol}" / f"param_id={_LEADLAG_PARAM_ID}" / "date=*" / "*.parquet"
    )


def ensure_leadlag(
    feature_exchange: str, feature_symbol: str, target_exchange: str, target_symbol: str, start_date: str, end_date: str
) -> bool:
    if not makalu_available():
        return False
    glob = _leadlag_glob(feature_exchange, feature_symbol, target_exchange, target_symbol)
    needed = set(pd.date_range(start_date, end_date).date)
    if needed - _distinct_dates(glob, start_date, end_date):
        pair_key = _LEAD_LAG_PAIR_SHORTHAND.get((feature_exchange, feature_symbol, target_exchange, target_symbol))
        if pair_key:
            args = ["LeadLag", "--pair", pair_key, "--start", start_date, "--end", end_date]
        else:
            args = [
                "LeadLag", "--lead-market", f"{feature_exchange}:{feature_symbol}",
                "--lag-market", f"{target_exchange}:{target_symbol}", "--start", start_date, "--end", end_date,
            ]
        result = _run_populate(*args)
        if result.returncode != 0:
            return False
    return True


def read_leadlag_summary(
    feature_exchange: str, feature_symbol: str, target_exchange: str, target_symbol: str, start_date: str, end_date: str
) -> dict | None:
    """Maps Makalu's gold LeadLag (a window x span grid of ll_* columns)
    into the exact same shape scanner.py's bronze-direct _measured_lead_lag
    already produces: {distinct_days, feature_leads_fraction,
    mean_best_lag_seconds, mean_best_corr, mean_corr_at_zero}. Verified
    against real data: this mapping's leads_fraction (0.90) matched the
    bronze-direct computation (0.875-1.0) closely for BINANCE:BTC_USDT ->
    GMO:BTC_SPOT.

    mean_best_lag_seconds is a best-effort signed approximation -- Makalu's
    ll_opt_lag_ms is a magnitude (normalized to [0,1] by the param_id's
    encoded max_lag_ms), not independently signed per row, so the sign is
    inferred from whether the feature led on average that day, not proven
    per-window the way scanner.py's own bronze-direct search is."""
    glob = _leadlag_glob(feature_exchange, feature_symbol, target_exchange, target_symbol)
    suffix = f"{_LEADLAG_WINDOW_S}_{_LEADLAG_SPAN_S}"
    con = duckdb.connect()
    try:
        df = con.sql(f"""
        SELECT date,
          avg(CASE WHEN isnan(ll_alpha_gain_{suffix}) THEN NULL WHEN ll_alpha_gain_{suffix} > 1 THEN 1.0 ELSE 0.0 END) AS leads_frac,
          avg(CASE WHEN isnan(ll_opt_lag_ms_{suffix}) THEN NULL ELSE ll_opt_lag_ms_{suffix} END) AS lag_norm,
          avg(CASE WHEN isnan(ll_max_corr_{suffix}) THEN NULL ELSE ll_max_corr_{suffix} END) AS max_corr,
          avg(CASE WHEN isnan(ll_corr_at_zero_{suffix}) THEN NULL ELSE ll_corr_at_zero_{suffix} END) AS corr_zero
        FROM read_parquet('{glob}', hive_partitioning=true)
        WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
        GROUP BY date
        """).df()
    except duckdb.IOException:
        return None
    df = df.dropna(subset=["leads_frac"])
    if df.empty:
        return None

    leads_frac = float(df["leads_frac"].mean())
    lag_magnitude_seconds = float(df["lag_norm"].mean()) * _LEADLAG_MAX_LAG_MS / 1000.0
    signed_lag_seconds = lag_magnitude_seconds if leads_frac >= 0.5 else -lag_magnitude_seconds

    return {
        "distinct_days": len(df),
        "feature_leads_fraction": leads_frac,
        "mean_best_lag_seconds": signed_lag_seconds,
        "mean_best_corr": float(df["max_corr"].mean()),
        "mean_corr_at_zero": float(df["corr_zero"].mean()),
    }
