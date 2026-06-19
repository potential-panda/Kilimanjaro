#!/usr/bin/env python3
"""Signal scanner (task 7): runs a hypothesis spec's feature expression
against real bronze data, joins it to the target market's future return,
and computes the metrics defined in requirements/requirements.md's
"Deterministic Scanner Metrics" -- IC, hit rate, bucketed returns, decay
by horizon, effective sample size, and block-bootstrap significance.

This is the generalized, automated version of the manual BINANCE-imbalance
walkthrough: same ASOF-join pattern, same statistics, now driven by a
spec's feature.sql_expression instead of a hand-written query.

v1 scope (documented limitations, not silently assumed):
  - Exactly one feature source dataset and one target source dataset --
    the Join policy in requirements.md limits hypothesis specs to "one
    feature source, one target source, and optional regime source"; this
    scanner implements the first two. Regime-bucket stability
    (stability_by_regime) is not yet computed -- always emitted empty.
  - target.type "research_label" is not implemented -- it requires joining
    to a research-layer label dataset (TripleBarrierLabel etc.), deferred.
  - Only feature/target source datasets that are bronze OrderBook are
    supported for now (mid = (bid0+ask0)/2, spread = ask0-bid0). Other
    dataset shapes can be added without changing the overall design.
  - threshold_verdict is always "not_evaluated" -- applying the v1 pass/
    fail thresholds is task 8's job, not this one's.
  - Does not validate the spec itself (schema/business rules/SQL safety) --
    call tools/validate_spec.py first; this module assumes a valid spec.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent))
from sql_safety import validate_expression  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_SCAN_REPORTS_DIR = _ROOT / "scan-reports"
_JST_OFFSET_SECONDS = 9 * 3600
_BOOTSTRAP_RESAMPLES = 1000
_BOOTSTRAP_SEED = 0
_N_BUCKETS = 5


class ScannerError(Exception):
    pass


def _ob_glob(data_root: Path, exchange: str, symbol: str) -> str:
    return str(data_root / "bronze" / "dataset=OrderBook" / f"exchange={exchange}" / f"symbol={symbol}" / "date=*" / "*.parquet")


def _max_horizon(spec_target: dict) -> int:
    return int(max([spec_target["primary_horizon_seconds"], *spec_target["comparison_horizons_seconds"]]))


def _measured_half_spread_bps(con: duckdb.DuckDBPyConnection, target_glob: str, start_date: str, end_date: str) -> float | None:
    """Real average half-spread on the target market over exactly the
    scanned window -- not a value declared in the spec ahead of time.
    Replaces the old execution_assumptions.half_spread_bps, which was
    found to drift stale relative to whatever window a spec was actually
    scanned against (see requirements.md's 2026-06-19 cost-buffer note)."""
    sql = f"""
    SELECT avg(ask0_price - bid0_price) AS avg_spread, avg((bid0_price + ask0_price) / 2.0) AS avg_mid
    FROM read_parquet('{target_glob}', hive_partitioning=true)
    WHERE ask0_price > bid0_price AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'
    """
    row = con.sql(sql).df().iloc[0]
    if row["avg_mid"] in (None, 0) or pd.isna(row["avg_mid"]):
        return None
    return float((row["avg_spread"] / 2.0) / row["avg_mid"] * 10000)


def _realized_volatility_bps(primary_df: pd.DataFrame) -> float | None:
    """Stdev of the target's realized return at the primary horizon, over
    exactly the scanned window -- the same "how much does price actually
    move" question markets/waves/ answers generically by time-of-day, but
    measured for this specific run instead of a separate precomputed wave."""
    if len(primary_df) < 2:
        return None
    return float(primary_df["target_return"].std() * 10000)


_LEAD_LAG_MAX_LAG_SECONDS = 10


def _peak_lag_corr(feature_ret: np.ndarray, target_ret: np.ndarray, max_lag_seconds: int) -> tuple[int, float, float] | None:
    """Cross-correlation of (feature_ret, target_ret) at integer-second
    lags in [-max_lag, +max_lag]. A peak at lag k>0 means target_ret[t+k]
    correlates best with feature_ret[t] -- the feature changed first and
    the target caught up k seconds later (feature leads). Returns
    (best_lag, best_corr, corr_at_zero), or None if there's too little
    data or every lag is degenerate (zero variance)."""
    corrs: dict[int, float] = {}
    for lag in range(-max_lag_seconds, max_lag_seconds + 1):
        a, b = (feature_ret[:-lag], target_ret[lag:]) if lag > 0 else (feature_ret[-lag:], target_ret[:lag]) if lag < 0 else (feature_ret, target_ret)
        if len(a) < 30 or np.std(a) == 0 or np.std(b) == 0:
            continue
        corrs[lag] = float(np.corrcoef(a, b)[0, 1])
    if not corrs:
        return None
    best_lag = max(corrs, key=lambda k: corrs[k])
    return best_lag, corrs[best_lag], corrs.get(0, float("nan"))


def _measured_lead_lag(
    con: duckdb.DuckDBPyConnection,
    feature_glob: str,
    target_glob: str,
    start_date: str,
    end_date: str,
    max_lag_seconds: int = _LEAD_LAG_MAX_LAG_SECONDS,
) -> dict | None:
    """Real lead-lag measurement computed directly from bronze OrderBook
    over exactly the scanned window -- not Makalu's precomputed gold
    LeadLag dataset, which was found to only cover 2025-12-30..2026-01-31
    while every hyp-20260618-* spec scans 2026-05-01..15 (a different
    period than the one actually tested). Per the project's Bronze-First
    Reliability Model, bronze is the only guaranteed dataset; this avoids
    depending on gold-layer coverage for a decision-relevant metric.

    Both markets are resampled to 1-second mid-price bars and joined on
    the second grid; cross-correlation of log returns at each integer lag
    is computed per day, then summarized across days. None when the
    feature and target are the same market (lead-lag isn't meaningful) or
    when there's too little overlapping data to say anything."""
    if feature_glob == target_glob:
        return None

    sql = f"""
    WITH f AS (
      SELECT CAST(floor(ts) AS BIGINT) AS t_sec, last((bid0_price + ask0_price) / 2.0 ORDER BY ts) AS mid
      FROM read_parquet('{feature_glob}', hive_partitioning=true)
      WHERE ask0_price > bid0_price AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'
      GROUP BY t_sec
    ),
    t AS (
      SELECT CAST(floor(ts) AS BIGINT) AS t_sec, last((bid0_price + ask0_price) / 2.0 ORDER BY ts) AS mid
      FROM read_parquet('{target_glob}', hive_partitioning=true)
      WHERE ask0_price > bid0_price AND date >= DATE '{start_date}' AND date <= DATE '{end_date}'
      GROUP BY t_sec
    )
    SELECT f.t_sec, CAST(DATE_TRUNC('day', TO_TIMESTAMP(f.t_sec + {_JST_OFFSET_SECONDS})) AS VARCHAR) AS jst_date,
      f.mid AS feature_mid, t.mid AS target_mid
    FROM f JOIN t ON f.t_sec = t.t_sec
    ORDER BY f.t_sec
    """
    df = con.sql(sql).df().dropna()
    if len(df) < (2 * max_lag_seconds + 100):
        return None

    daily = []
    for jst_date, day_df in df.groupby("jst_date"):
        feature_ret = np.diff(np.log(day_df["feature_mid"].to_numpy()))
        target_ret = np.diff(np.log(day_df["target_mid"].to_numpy()))
        result = _peak_lag_corr(feature_ret, target_ret, max_lag_seconds)
        if result is not None:
            best_lag, best_corr, corr_at_zero = result
            daily.append({"date": jst_date, "best_lag_seconds": best_lag, "best_corr": best_corr, "corr_at_zero": corr_at_zero})

    if not daily:
        return None

    return {
        "distinct_days": len(daily),
        "feature_leads_fraction": float(np.mean([d["best_lag_seconds"] > 0 for d in daily])),
        "mean_best_lag_seconds": float(np.mean([d["best_lag_seconds"] for d in daily])),
        "mean_best_corr": float(np.mean([d["best_corr"] for d in daily])),
        "mean_corr_at_zero": float(np.mean([d["corr_at_zero"] for d in daily])),
    }


def _pull_joined_table(
    con: duckdb.DuckDBPyConnection,
    feature_sql: str,
    feature_alias: str,
    feature_glob: str,
    target_glob: str,
    target_type: str,
    horizon_seconds: float,
    start_date: str,
    end_date: str,
    data_root: Path,
) -> pd.DataFrame:
    """One row per feature-source tick within [start_date, end_date]: the
    signal value, and the target market's return horizon_seconds later."""
    if target_type not in ("next_return", "spread_adjusted_return"):
        raise ScannerError(f"target.type '{target_type}' is not implemented in v1 (only next_return, spread_adjusted_return)")

    lookahead_pad_days = int(horizon_seconds // 86400) + 2
    target_end = (date.fromisoformat(end_date) + timedelta(days=lookahead_pad_days)).isoformat()

    spread_expr = "(ask0_price - bid0_price) / ((bid0_price + ask0_price) / 2.0)" if target_type == "spread_adjusted_return" else "0.0"

    sql = f"""
    WITH feature_src AS (
      SELECT ts, ({feature_sql}) AS signal
      FROM read_parquet('{feature_glob}', hive_partitioning=true) AS {feature_alias}
      WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
    ),
    target_ob AS (
      SELECT
        ts,
        (bid0_price + ask0_price) / 2.0 AS mid,
        {spread_expr} AS half_spread_frac
      FROM read_parquet('{target_glob}', hive_partitioning=true)
      WHERE ask0_price > bid0_price
        AND date >= DATE '{start_date}' AND date <= DATE '{target_end}'
    ),
    with_now AS (
      SELECT f.ts, f.signal, t.mid AS mid_now
      FROM feature_src f
      ASOF JOIN target_ob t ON t.ts <= f.ts
    ),
    joined AS (
      SELECT w.ts, w.signal, w.mid_now, t2.mid AS mid_future, t2.half_spread_frac
      FROM with_now w
      ASOF JOIN target_ob t2 ON t2.ts >= w.ts + {horizon_seconds}
    )
    SELECT
      ts,
      CAST(DATE_TRUNC('day', TO_TIMESTAMP(ts + {_JST_OFFSET_SECONDS})) AS VARCHAR) AS jst_date,
      signal,
      (mid_future - mid_now) / mid_now AS raw_return,
      half_spread_frac / 2.0 AS half_spread_cost
    FROM joined
    WHERE mid_future IS NOT NULL AND mid_now IS NOT NULL AND signal IS NOT NULL
    ORDER BY ts
    """
    df = con.sql(sql).df()
    if target_type == "spread_adjusted_return":
        df["target_return"] = df["raw_return"] - np.sign(df["raw_return"]) * df["half_spread_cost"] / 2.0
    else:
        df["target_return"] = df["raw_return"]
    return df[["ts", "jst_date", "signal", "target_return"]]


def _spearman_ic(signal: np.ndarray, target: np.ndarray) -> float | None:
    if len(signal) < 3 or np.std(signal) == 0 or np.std(target) == 0:
        return None
    ic, _ = spearmanr(signal, target)
    return float(ic) if not np.isnan(ic) else None


def _hit_rate(signal: np.ndarray, target: np.ndarray) -> float | None:
    sig_sign, tgt_sign = np.sign(signal), np.sign(target)
    mask = (sig_sign != 0) & (tgt_sign != 0)
    if mask.sum() == 0:
        return None
    return float((sig_sign[mask] == tgt_sign[mask]).mean())


def _lag1_autocorr(x: np.ndarray) -> float | None:
    if len(x) < 3:
        return None
    a, b = x[:-1], x[1:]
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    rho = np.corrcoef(a, b)[0, 1]
    return float(np.clip(rho, 0.0, 0.99)) if not np.isnan(rho) else None


def _effective_sample_count(df: pd.DataFrame) -> tuple[int, float]:
    """Per day: n_eff = n * (1 - rho1) / (1 + rho1), rho1 = lag-1
    autocorrelation of the signal within that day, clipped to [0, 0.99].
    Reports raw_sample_count (total rows) and summed day-level n_eff."""
    raw_n = len(df)
    total_eff = 0.0
    for _jst_date, day_df in df.groupby("jst_date"):
        n = len(day_df)
        rho1 = _lag1_autocorr(day_df["signal"].to_numpy())
        rho1 = rho1 if rho1 is not None else 0.0
        total_eff += n * (1 - rho1) / (1 + rho1)
    return raw_n, total_eff


def _bucketed_returns(df: pd.DataFrame, n_buckets: int = _N_BUCKETS) -> list[dict]:
    """5 quantile buckets by signal value, assigned per day (so bucket
    boundaries don't drift across the whole window), then aggregated
    globally across days per bucket index."""
    def _assign(signal: pd.Series) -> pd.Series:
        try:
            return pd.qcut(signal, n_buckets, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series([np.nan] * len(signal), index=signal.index)

    df = df.copy()
    df["bucket"] = df.groupby("jst_date")["signal"].transform(_assign)
    out = []
    for bucket, bucket_df in df.dropna(subset=["bucket"]).groupby("bucket"):
        out.append({"bucket": int(bucket), "n": int(len(bucket_df)), "mean_target_return": float(bucket_df["target_return"].mean())})
    return sorted(out, key=lambda r: r["bucket"])


def _block_bootstrap_ci(df: pd.DataFrame, seed: int = _BOOTSTRAP_SEED, resamples: int = _BOOTSTRAP_RESAMPLES) -> tuple[float | None, float | None]:
    """Block bootstrap by day: resample whole days with replacement,
    recompute pooled Spearman IC each time, report the 95% CI.

    Kept as an available method, but NOT the v1 default -- confirmed
    against real data (BINANCE/BTC_USDT, ~13M rows for a 15-day window)
    that 1,000 resamples at tick-level row counts takes on the order of
    tens of minutes, which contradicts the scanner's whole design goal of
    being cheap and fast per hypothesis. requirements.md already
    anticipated this exact tradeoff and names Newey-West/HAC
    (_newey_west_hac_ci below) as the fallback "if bootstrap cost becomes
    a bottleneck at scan volume" -- it has, so that's now the v1 default.
    """
    days = df["jst_date"].unique()
    if len(days) < 2:
        return None, None
    rng = np.random.RandomState(seed)
    by_day = {d: g["signal"].to_numpy() for d, g in df.groupby("jst_date")}
    by_day_target = {d: g["target_return"].to_numpy() for d, g in df.groupby("jst_date")}
    ics = []
    for _ in range(resamples):
        sampled_days = rng.choice(days, size=len(days), replace=True)
        signal = np.concatenate([by_day[d] for d in sampled_days])
        target = np.concatenate([by_day_target[d] for d in sampled_days])
        ic = _spearman_ic(signal, target)
        if ic is not None:
            ics.append(ic)
    if not ics:
        return None, None
    return float(np.percentile(ics, 2.5)), float(np.percentile(ics, 97.5))


def _newey_west_hac_ci(df: pd.DataFrame) -> tuple[float | None, float | None, int | None]:
    """Newey-West/HAC confidence interval for the pooled Spearman IC.

    Spearman IC is the Pearson correlation of (signal, target) ranks. We
    standardize the pooled ranks, take the elementwise product (its mean
    is exactly the IC), and compute a Newey-West long-run-variance
    estimate of that product series to get a HAC standard error of the
    mean -- which accounts for autocorrelation without needing to
    resample anything. Bandwidth uses the standard Newey-West (1994)
    automatic rule, clipped to a sane range for very large n.

    Returns (ci_low, ci_high, bandwidth_used), or (None, None, None) if
    there isn't enough data.
    """
    n = len(df)
    if n < 10:
        return None, None, None

    signal_rank = pd.Series(df["signal"]).rank().to_numpy()
    target_rank = pd.Series(df["target_return"]).rank().to_numpy()
    if np.std(signal_rank) == 0 or np.std(target_rank) == 0:
        return None, None, None

    z_signal = (signal_rank - signal_rank.mean()) / signal_rank.std()
    z_target = (target_rank - target_rank.mean()) / target_rank.std()
    product = z_signal * z_target
    ic = float(product.mean())

    bandwidth = int(np.clip(np.floor(4 * (n / 100) ** (2 / 9)), 1, 5000))
    product_centered = product - ic
    gamma_0 = float(np.dot(product_centered, product_centered) / n)
    long_run_var = gamma_0
    for lag in range(1, bandwidth + 1):
        weight = 1 - lag / (bandwidth + 1)  # Bartlett kernel
        gamma_lag = float(np.dot(product_centered[:-lag], product_centered[lag:]) / n)
        long_run_var += 2 * weight * gamma_lag

    se = np.sqrt(max(long_run_var, 0.0) / n)
    return ic - 1.96 * se, ic + 1.96 * se, bandwidth


def _next_scan_id(scan_reports_dir: Path, at: datetime) -> str:
    date_str = at.strftime("%Y%m%d")
    prefix = f"scan-{date_str}-"
    existing = [p.stem for p in scan_reports_dir.glob(f"{prefix}*.json")] if scan_reports_dir.exists() else []
    seqs = [int(s[len(prefix):]) for s in existing if s[len(prefix):].isdigit()]
    return f"{prefix}{(max(seqs) + 1) if seqs else 1:03d}"


def run_scan(
    spec: dict,
    start_date: str,
    end_date: str,
    data_root: Path = _DEFAULT_DATA_ROOT,
    scan_reports_dir: Path = _DEFAULT_SCAN_REPORTS_DIR,
) -> dict:
    h = spec["hypothesis"]
    sql_expr = h["feature"]["sql_expression"]

    safety_errors = validate_expression(sql_expr)
    if safety_errors:
        raise ScannerError(f"refusing to scan an unsafe expression: {safety_errors}")

    feature_source = h["feature"]["source_datasets"][0]
    target_source = h["target"]["source_dataset"]
    if feature_source["dataset"] != "OrderBook" or target_source["dataset"] != "OrderBook":
        raise ScannerError("v1 scanner only supports OrderBook as the feature/target source dataset")

    feature_glob = _ob_glob(data_root, feature_source["exchange"], feature_source["symbol"])
    target_glob = _ob_glob(data_root, target_source["exchange"], target_source["symbol"])
    feature_alias = feature_source.get("alias") or "f"

    primary_horizon = h["target"]["primary_horizon_seconds"]
    all_horizons = sorted({primary_horizon, *h["target"]["comparison_horizons_seconds"]})

    con = duckdb.connect()
    decay_by_horizon = []
    primary_df = None
    for horizon in all_horizons:
        df = _pull_joined_table(
            con, sql_expr, feature_alias, feature_glob, target_glob, h["target"]["type"],
            horizon, start_date, end_date, data_root,
        )
        ic = _spearman_ic(df["signal"].to_numpy(), df["target_return"].to_numpy())
        hr = _hit_rate(df["signal"].to_numpy(), df["target_return"].to_numpy())
        decay_by_horizon.append({"horizon_seconds": horizon, "ic_pooled": ic, "hit_rate": hr, "n": len(df)})
        if horizon == primary_horizon:
            primary_df = df

    assert primary_df is not None
    ic_by_day = {}
    for jst_date, day_df in primary_df.groupby("jst_date"):
        ic = _spearman_ic(day_df["signal"].to_numpy(), day_df["target_return"].to_numpy())
        if ic is not None:
            ic_by_day[jst_date] = ic

    raw_n, effective_n = _effective_sample_count(primary_df)
    ci_low, ci_high, hac_bandwidth = _newey_west_hac_ci(primary_df)

    market_context = {
        "avg_half_spread_bps": _measured_half_spread_bps(con, target_glob, start_date, end_date),
        "realized_volatility_bps": _realized_volatility_bps(primary_df),
        "lead_lag": _measured_lead_lag(con, feature_glob, target_glob, start_date, end_date),
    }

    created_at = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "id": _next_scan_id(scan_reports_dir, created_at),
        "hypothesis_id": h["id"],
        "scanner_version": "v1",
        "config_hash": "",
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_window": {
            "exchange": target_source["exchange"],
            "symbol": target_source["symbol"],
            "start_date": start_date,
            "end_date": end_date,
            "distinct_days": primary_df["jst_date"].nunique(),
        },
        "market_context": market_context,
        "metrics": {
            "primary_horizon_seconds": primary_horizon,
            "ic_method": "spearman",
            "ic_pooled": _spearman_ic(primary_df["signal"].to_numpy(), primary_df["target_return"].to_numpy()),
            "ic_by_day": ic_by_day,
            "hit_rate": _hit_rate(primary_df["signal"].to_numpy(), primary_df["target_return"].to_numpy()),
            "bucketed_returns": _bucketed_returns(primary_df),
            "decay_by_horizon": decay_by_horizon,
            "raw_sample_count": raw_n,
            "effective_sample_count": effective_n,
            "signal_autocorrelation_lag1": _lag1_autocorr(primary_df["signal"].to_numpy()),
            "significance_method": "newey_west_hac",
            "hac_bandwidth": hac_bandwidth,
            "bootstrap_resamples": None,
            "bootstrap_seed": None,
            "bootstrap_ci_low": ci_low,
            "bootstrap_ci_high": ci_high,
            "stability_by_regime": {},
        },
        "threshold_verdict": {
            "status": "not_evaluated",
            "failed_rules": [],
            "yellow_flags": [],
            "verdict_text": "threshold evaluation is task 8's scope, not yet implemented",
        },
    }
    return report


def write_report(report: dict, scan_reports_dir: Path = _DEFAULT_SCAN_REPORTS_DIR) -> Path:
    scan_reports_dir.mkdir(parents=True, exist_ok=True)
    path = scan_reports_dir / f"{report['id']}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the signal scanner against a hypothesis spec")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out-dir", default=str(_DEFAULT_SCAN_REPORTS_DIR))
    args = parser.parse_args()

    spec = yaml.safe_load(Path(args.spec).read_text())
    try:
        report = run_scan(spec, args.start, args.end, scan_reports_dir=Path(args.out_dir))
    except ScannerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    path = write_report(report, Path(args.out_dir))
    print(f"Wrote {path}")
    print(json.dumps(report["metrics"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
