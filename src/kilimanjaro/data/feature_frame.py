"""Multi-market 1-second feature frame for expression signals.

Builds one DataFrame on a shared, contiguous 1s ts grid where each column is
"{alias}_{feature}" for one of the configured markets. This is the substrate
ExpressionSignal formulas evaluate over — a new signal idea should cost a
new expression, not a new loader.

Feature name → source routing (per market):

    mid, spread_bps          silver MarketPrice (needs execution_size),
                             last tick in each 1s bucket
    obi_k{d}                 feature OBIFeature vol_imb_k{d}   (d ∈ 1,3,5,10)
    obi_ema_k{d}_{hl}s       feature OBIFeature ema_k{d}_{hl}s (hl ∈ 10..300)
    ta_{column}              silver TradeAggression {column}, 1s buckets
    bs_{column}              feature BookState {column}, 1s buckets
                             (microprice_k{d}, weighted_imb_k{d}, best_mid, ...)
    bc_{column}              feature BookChange {column}, 1s buckets
                             (pull_bid_rate_k{d}, delta_depth_bid_rate_k{d}, ...)

Values are forward-filled onto the grid with a bounded staleness
(ffill_limit_s, default 300): beyond that, NaN — expressions propagate the
NaN and the signal mask goes quiet instead of trading stale data.

Coverage differs per dataset (e.g. OBIFeature only exists for some markets
and windows) — use available_features() to see what a market actually has.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_FFILL_LIMIT_S = 300

_OBI_RAW_RE = re.compile(r"^obi_k(\d+)$")
_OBI_EMA_RE = re.compile(r"^obi_ema_(k\d+_\d+s)$")
_OBI_EMA_PARTS_RE = re.compile(r"^obi_ema_k(\d+)_(\d+)s$")
_TA_RE = re.compile(r"^ta_(\w+)$")
# BookState: bs_<column>, e.g. bs_microprice_k1, bs_best_mid, bs_weighted_imb_k5.
_BS_RE = re.compile(r"^bs_(\w+)$")
# BookChange: bc_<column>, e.g. bc_pull_ask_rate_k1, bc_delta_depth_bid_rate_k1.
_BC_RE = re.compile(r"^bc_(\w+)$")
_MP_FEATURES = {"mid", "spread_bps"}


def obi_column(depth: int, halflife: int | None, alias: str | None = None) -> str:
    """Feature-frame OBI column name for a (depth, halflife).

    halflife=None -> raw ``obi_k{depth}``; else the pre-EMA'd
    ``obi_ema_k{depth}_{halflife}s``. With ``alias`` -> ``{alias}_{name}``.
    This is the bridge that lets an OBI-age scan be a plain ExpressionSignal.
    """
    name = f"obi_k{depth}" if halflife is None else f"obi_ema_k{depth}_{halflife}s"
    return f"{alias}_{name}" if alias else name


def parse_obi_column(col: str) -> tuple[int, int | None]:
    """Inverse of :func:`obi_column` (alias prefix tolerated).

    ``obi_k5`` -> (5, None); ``a_obi_ema_k5_60s`` -> (5, 60).
    """
    i = col.find("obi_")
    base = col[i:] if i != -1 else col
    m = _OBI_RAW_RE.match(base)
    if m:
        return int(m.group(1)), None
    m = _OBI_EMA_PARTS_RE.match(base)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError(f"not an OBI column: {col!r}")


def _mp_glob(exchange: str, symbol: str, execution_size: str, data_root: Path) -> str:
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


def _obi_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root
        / "feature"
        / "dataset=OBIFeature"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / "date=*"
        / "*.parquet"
    )


def _ta_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root
        / "silver"
        / "dataset=TradeAggression"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / "date=*"
        / "*.parquet"
    )


def _bs_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root
        / "silver"
        / "dataset=BookState"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / "date=*"
        / "*.parquet"
    )


def _bc_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root
        / "silver"
        / "dataset=BookChange"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / "date=*"
        / "*.parquet"
    )


def _query(sql: str) -> pd.DataFrame:
    # Hold the connection until .df() materializes (premature-GC bug otherwise)
    con = duckdb.connect()
    try:
        return con.sql(sql).df()
    finally:
        con.close()


def _route(features: list[str]) -> dict[str, list[str]]:
    """Split requested feature names by source dataset."""
    routes: dict[str, list[str]] = {"mp": [], "obi": [], "ta": [], "bs": [], "bc": []}
    for f in features:
        if f in _MP_FEATURES:
            routes["mp"].append(f)
        elif _OBI_RAW_RE.match(f) or _OBI_EMA_RE.match(f):
            routes["obi"].append(f)
        elif _BS_RE.match(f):  # before _TA_RE: bs_ is its own dataset
            routes["bs"].append(f)
        elif _BC_RE.match(f):  # BookChange
            routes["bc"].append(f)
        elif _TA_RE.match(f):
            routes["ta"].append(f)
        else:
            raise ValueError(
                f"unknown feature '{f}' — expected mid, spread_bps, obi_k*,"
                f" obi_ema_k*_*s, ta_<column>, bs_<column>, or bc_<column>"
            )
    return routes


def _obi_source_col(f: str) -> str:
    m = _OBI_RAW_RE.match(f)
    if m:
        return f"vol_imb_k{m.group(1)}"
    return f"ema_{_OBI_EMA_RE.match(f).group(1)}"


def _load_market(
    exchange: str,
    symbol: str,
    execution_size: str | None,
    features: list[str],
    start: str,
    end: str,
    data_root: Path,
) -> pd.DataFrame:
    """Load one market's features, bucketed to 1s (last value per bucket).

    Returns a DataFrame indexed by integer second ts.

    "Last value per bucket" = the LAST source row of the second in physical (event)
    order — `last(col ORDER BY ts, file_row_number)`. The `file_row_number` tie-break
    is essential for venues that timestamp at 1-second resolution (e.g. COINCHECK sends
    ~3 books/second, all with the same integer ts): ordering by ts alone leaves ties
    that DuckDB breaks arbitrarily (it picked the FIRST row), whereas the online qate
    engine snapshots the LAST book seen before the second rolls over. Ordering by the
    physical row number reproduces that exactly (verified 100% vs aquila's b_mid) — the
    "last available price at the second boundary" both offline and online must agree on.
    """
    routes = _route(features)
    parts: list[pd.DataFrame] = []

    if routes["mp"]:
        if execution_size is None:
            raise ValueError(f"{exchange}/{symbol}: mid/spread need execution_size")
        cols = ", ".join(f"last({c} ORDER BY ts, file_row_number) AS {c}" for c in routes["mp"])
        df = _query(f"""
            SELECT CAST(floor(ts) AS BIGINT) AS sec, {cols}
            FROM read_parquet('{_mp_glob(exchange, symbol, execution_size, data_root)}', hive_partitioning=true, file_row_number=true)
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
            GROUP BY 1
        """).set_index("sec")
        parts.append(df)

    if routes["obi"]:
        cols = ", ".join(
            f"last({_obi_source_col(f)} ORDER BY ts, file_row_number) AS {f}" for f in routes["obi"]
        )
        df = _query(f"""
            SELECT CAST(floor(ts) AS BIGINT) AS sec, {cols}
            FROM read_parquet('{_obi_glob(exchange, symbol, data_root)}', hive_partitioning=true, file_row_number=true)
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
            GROUP BY 1
        """).set_index("sec")
        parts.append(df)

    if routes["ta"]:
        cols = ", ".join(
            f"last({_TA_RE.match(f).group(1)} ORDER BY ts, file_row_number) AS {f}" for f in routes["ta"]
        )
        df = _query(f"""
            SELECT CAST(floor(ts) AS BIGINT) AS sec, {cols}
            FROM read_parquet('{_ta_glob(exchange, symbol, data_root)}', hive_partitioning=true, file_row_number=true)
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
            GROUP BY 1
        """).set_index("sec")
        parts.append(df)

    if routes["bs"]:
        cols = ", ".join(
            f"last({_BS_RE.match(f).group(1)} ORDER BY ts, file_row_number) AS {f}" for f in routes["bs"]
        )
        df = _query(f"""
            SELECT CAST(floor(ts) AS BIGINT) AS sec, {cols}
            FROM read_parquet('{_bs_glob(exchange, symbol, data_root)}', hive_partitioning=true, file_row_number=true)
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
            GROUP BY 1
        """).set_index("sec")
        parts.append(df)

    if routes["bc"]:
        cols = ", ".join(
            f"last({_BC_RE.match(f).group(1)} ORDER BY ts, file_row_number) AS {f}" for f in routes["bc"]
        )
        df = _query(f"""
            SELECT CAST(floor(ts) AS BIGINT) AS sec, {cols}
            FROM read_parquet('{_bc_glob(exchange, symbol, data_root)}', hive_partitioning=true, file_row_number=true)
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
            GROUP BY 1
        """).set_index("sec")
        parts.append(df)

    if not parts:
        raise ValueError(f"{exchange}/{symbol}: no features requested")

    out = pd.concat(parts, axis=1)
    if out.empty:
        raise ValueError(
            f"{exchange}/{symbol}: no data for {features} in {start}..{end}"
        )
    return out.sort_index()


def load_feature_frame(
    markets: dict[str, dict],
    columns: list[str],
    start: str,
    end: str,
    data_root: Path = _DEFAULT_DATA_ROOT,
    ffill_limit_s: int = _DEFAULT_FFILL_LIMIT_S,
) -> pd.DataFrame:
    """Build the aligned multi-market frame.

    Parameters
    ----------
    markets : dict alias → {"exchange": ..., "symbol": ..., "execution_size": ...}
        e.g. {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT", "execution_size": "0.1"},
              "b": {"exchange": "GMO", "symbol": "ETH_JPY", "execution_size": "0.1"}}
    columns : list of "{alias}_{feature}" names, e.g. ["a_mid", "b_mid", "a_obi_ema_k5_60s"]
    ffill_limit_s : max staleness carried forward onto the grid; beyond → NaN.

    Returns
    -------
    DataFrame with a float "ts" column plus the requested columns, on a
    contiguous 1s grid covering the intersection of the markets' spans.
    """
    per_alias: dict[str, list[str]] = {}
    for col in columns:
        alias, _, feature = col.partition("_")
        if alias not in markets or not feature:
            raise ValueError(
                f"column '{col}' does not match any market alias {sorted(markets)}"
            )
        per_alias.setdefault(alias, []).append(feature)

    loaded: dict[str, pd.DataFrame] = {}
    for alias, feats in per_alias.items():
        m = markets[alias]
        loaded[alias] = _load_market(
            m["exchange"],
            m["symbol"],
            m.get("execution_size"),
            feats,
            start,
            end,
            data_root,
        )

    grid_start = max(df.index.min() for df in loaded.values())
    grid_end = min(df.index.max() for df in loaded.values())
    if grid_start >= grid_end:
        raise ValueError("markets have no overlapping time range")
    grid = np.arange(grid_start, grid_end + 1, dtype=np.int64)

    out = pd.DataFrame(index=grid)
    for alias, df in loaded.items():
        aligned = df.reindex(grid).ffill(limit=ffill_limit_s)
        for feature in df.columns:
            out[f"{alias}_{feature}"] = aligned[feature]

    out.insert(0, "ts", grid.astype(np.float64))
    return out.reset_index(drop=True)


def available_features(
    exchange: str,
    symbol: str,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict[str, dict]:
    """What features (and date coverage) does this market actually have?"""
    out: dict[str, dict] = {}

    mp_root = (
        data_root
        / "silver"
        / "dataset=MarketPrice"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    if mp_root.exists():
        for size_dir in sorted(mp_root.iterdir()):
            dates = sorted(
                d.name[5:] for d in size_dir.iterdir() if d.name.startswith("date=")
            )
            if dates:
                out[f"mid, spread_bps ({size_dir.name})"] = {
                    "n_days": len(dates),
                    "first": dates[0],
                    "last": dates[-1],
                }

    obi_root = (
        data_root
        / "feature"
        / "dataset=OBIFeature"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    if obi_root.exists():
        dates = sorted(
            d.name[5:] for d in obi_root.iterdir() if d.name.startswith("date=")
        )
        if dates:
            sample = next((obi_root / f"date={dates[-1]}").glob("*.parquet"), None)
            cols = []
            if sample is not None:
                import pyarrow.parquet as pq

                names = pq.read_schema(sample).names
                cols = [
                    f"obi_k{n[len('vol_imb_k') :]}"
                    for n in names
                    if n.startswith("vol_imb_k")
                ]
                cols += [f"obi_{n}" for n in names if n.startswith("ema_k")]
            out["obi (OBIFeature)"] = {
                "n_days": len(dates),
                "first": dates[0],
                "last": dates[-1],
                "columns": cols,
            }

    ta_root = (
        data_root
        / "silver"
        / "dataset=TradeAggression"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    if ta_root.exists():
        dates = sorted(
            d.name[5:] for d in ta_root.iterdir() if d.name.startswith("date=")
        )
        if dates:
            out["ta_* (TradeAggression)"] = {
                "n_days": len(dates),
                "first": dates[0],
                "last": dates[-1],
            }

    bs_root = (
        data_root
        / "silver"
        / "dataset=BookState"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    if bs_root.exists():
        dates = sorted(
            d.name[5:] for d in bs_root.iterdir() if d.name.startswith("date=")
        )
        if dates:
            out["bs_* (BookState)"] = {
                "n_days": len(dates),
                "first": dates[0],
                "last": dates[-1],
            }

    bc_root = (
        data_root
        / "silver"
        / "dataset=BookChange"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    if bc_root.exists():
        dates = sorted(
            d.name[5:] for d in bc_root.iterdir() if d.name.startswith("date=")
        )
        if dates:
            out["bc_* (BookChange)"] = {
                "n_days": len(dates),
                "first": dates[0],
                "last": dates[-1],
            }

    return out
