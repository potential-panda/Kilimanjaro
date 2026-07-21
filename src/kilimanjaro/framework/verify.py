"""General parquet↔InfluxDB comparison engine.

Every verifier in this repo does the same three things: load a local series
(derived from the parquet store), load the matching series from the backtest's
InfluxDB, align them, and report how far apart they are. This module is that
shared core — pure and I/O-light so it is unit-testable, which the hand-rolled
verifiers never were.

- `compare_frames(local, remote, on, fields, tolerance)` aligns two frames
  (exact key-join, or greedy nearest-match within a tolerance) and returns
  per-field diff stats (avg / stddev / p50 / p90 / max / pct_exact) plus
  coverage (n_local / n_remote / n_matched / *_only) and the matched frame.
- `load_influx(...)` / `influx_range_jst(...)` build and run the standard
  pivoted flux query both backtest streams (BAR / ORDER / EVAL) use.

Callers (`dataset.range_sar_store.verify`, `framework.backtest_verify`) supply
the store loader, the field mapping, and the tolerance; verify-scan additionally
layers Sharpe/Calmar/Martin (see `framework.performance`) on the matched PnL.
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

_INFLUX_URL = "http://localhost:8086"
_INFLUX_TOKEN = "panda-auth-token"
_INFLUX_ORG = "panda-org"


# ── comparison engine ─────────────────────────────────────────────────────────
def _diff_stats(diff: np.ndarray, eps: float) -> dict:
    """Distributional summary of a local−remote diff series.

    `avg` is the signed mean (a bias detector); `p50`/`p90`/`max_abs` are on the
    magnitude |diff|; `pct_exact` is the share within `eps`.
    """
    a = np.asarray(diff, dtype=float)
    absd = np.abs(a)
    n = int(a.size)
    if n == 0:
        keys = ("avg", "mean_abs", "std", "p50", "p90", "max_abs", "pct_exact")
        return {"n": 0, **{k: float("nan") for k in keys}}
    return {
        "n": n,
        "avg": float(a.mean()),
        "mean_abs": float(absd.mean()),
        "std": float(a.std()),
        "p50": float(np.percentile(absd, 50)),
        "p90": float(np.percentile(absd, 90)),
        "max_abs": float(absd.max()),
        "pct_exact": float((absd <= eps).mean() * 100.0),
    }


def _greedy_match(
    local_ts: np.ndarray, remote_ts: np.ndarray, tolerance: float
) -> tuple[np.ndarray, np.ndarray]:
    """Two-pointer nearest-match within `tolerance` over two SORTED 1-D arrays.

    Returns (local_positions, remote_positions) of matched pairs — the same
    greedy rule the flip-scan verify has always used.
    """
    n_l, n_r = len(local_ts), len(remote_ts)
    l_pos: list[int] = []
    r_pos: list[int] = []
    i = j = 0
    while i < n_l and j < n_r:
        d = local_ts[i] - remote_ts[j]
        if abs(d) <= tolerance:
            l_pos.append(i)
            r_pos.append(j)
            i += 1
            j += 1
        elif d < 0:
            i += 1
        else:
            j += 1
    return np.asarray(l_pos, dtype=int), np.asarray(r_pos, dtype=int)


def compare_frames(
    local: pd.DataFrame,
    remote: pd.DataFrame,
    *,
    on: str,
    fields: list[tuple[str, str, str]],
    tolerance: float | None = None,
    round_ndigits: int | None = None,
    eps: float = 0.0,
) -> dict:
    """Align local (parquet) vs remote (influx) and compare fields.

    on            : key column present in both frames (a timestamp).
    fields        : list of (label, local_col, remote_col) to compare.
    tolerance     : None → exact inner-join on `on` (optionally rounded to
                    `round_ndigits`); a float → greedy nearest-match on `on`
                    within that tolerance (same units as `on`, e.g. seconds).
    eps           : |diff| ≤ eps counts as an exact match for pct_exact.

    Returns a dict:
        coverage   : n_local, n_remote, n_matched, n_local_only, n_remote_only
        fields     : {label: _diff_stats(...)}
        matched    : DataFrame with `on` + <label>_local / _remote / _diff cols
        local_only : rows of `local` with no match (DataFrame)
        remote_only: rows of `remote` with no match (DataFrame)
    """
    local = local.reset_index(drop=True)
    remote = remote.reset_index(drop=True)
    n_local, n_remote = len(local), len(remote)

    if tolerance is None:
        lk = local[on] if round_ndigits is None else local[on].round(round_ndigits)
        rk = remote[on] if round_ndigits is None else remote[on].round(round_ndigits)
        left = pd.DataFrame({"_key": lk.values, "_li": np.arange(n_local)})
        right = pd.DataFrame({"_key": rk.values, "_ri": np.arange(n_remote)})
        m = left.merge(right, on="_key")
        l_idx = m["_li"].to_numpy()
        r_idx = m["_ri"].to_numpy()
    else:
        lo = np.argsort(local[on].to_numpy(), kind="mergesort")
        ro = np.argsort(remote[on].to_numpy(), kind="mergesort")
        sl, sr = _greedy_match(
            local[on].to_numpy()[lo], remote[on].to_numpy()[ro], tolerance
        )
        l_idx = lo[sl]
        r_idx = ro[sr]

    l_matched = np.zeros(n_local, dtype=bool)
    r_matched = np.zeros(n_remote, dtype=bool)
    l_matched[l_idx] = True
    r_matched[r_idx] = True

    matched = pd.DataFrame({on: local[on].to_numpy()[l_idx]})
    field_stats: dict[str, dict] = {}
    for label, lc, rc in fields:
        lv = local[lc].to_numpy()[l_idx].astype(float)
        rv = remote[rc].to_numpy()[r_idx].astype(float)
        diff = lv - rv
        matched[f"{label}_local"] = lv
        matched[f"{label}_remote"] = rv
        matched[f"{label}_diff"] = diff
        field_stats[label] = _diff_stats(diff, eps)

    return {
        "coverage": {
            "n_local": n_local,
            "n_remote": n_remote,
            "n_matched": int(len(l_idx)),
            "n_local_only": int((~l_matched).sum()),
            "n_remote_only": int((~r_matched).sum()),
        },
        "fields": field_stats,
        "matched": matched,
        "local_only": local.iloc[np.where(~l_matched)[0]].reset_index(drop=True),
        "remote_only": remote.iloc[np.where(~r_matched)[0]].reset_index(drop=True),
    }


def render_compare(result: dict, *, title: str = "", value_fmt: str = ".6f") -> str:
    """Basic text report of a `compare_frames` result: coverage + per-field
    stats table. Callers append domain sections (gaps, ratios) as needed."""
    cov = result["coverage"]
    lines = []
    if title:
        lines.append(title)
    match_rate = (
        cov["n_matched"] / cov["n_local"] * 100 if cov["n_local"] else 0.0
    )
    lines.append(
        f"  coverage: local={cov['n_local']} remote={cov['n_remote']} "
        f"matched={cov['n_matched']} ({match_rate:.1f}%)  "
        f"local_only={cov['n_local_only']} remote_only={cov['n_remote_only']}"
    )
    hdr = (
        f"  {'field':<14} {'n':>7} {'avg':>12} {'std':>12} "
        f"{'p50':>12} {'p90':>12} {'max':>12} {'pct_exact':>10}"
    )
    lines.append(hdr)
    for label, s in result["fields"].items():
        lines.append(
            f"  {label:<14} {s['n']:>7} {s['avg']:>12{value_fmt}} "
            f"{s['std']:>12{value_fmt}} {s['p50']:>12{value_fmt}} "
            f"{s['p90']:>12{value_fmt}} {s['max_abs']:>12{value_fmt}} "
            f"{s['pct_exact']:>9.2f}%"
        )
    return "\n".join(lines)


# ── InfluxDB loading ──────────────────────────────────────────────────────────
def influx_client(timeout: int = 120_000):
    """Open an InfluxDBClient against the local backtest store."""
    from influxdb_client import InfluxDBClient

    return InfluxDBClient(
        url=_INFLUX_URL, token=_INFLUX_TOKEN, org=_INFLUX_ORG, timeout=timeout
    )


def influx_range_jst(start_date: str, end_date: str) -> tuple[str, str]:
    """(start, stop) UTC RFC3339 covering JST [start_date 00:00, end_date+1 00:00)."""
    jst = datetime.timedelta(hours=9)
    start = (datetime.datetime.strptime(start_date, "%Y-%m-%d") - jst).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    stop = (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        + datetime.timedelta(days=1)
        - jst
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, stop


def query_df(qapi, flux: str) -> pd.DataFrame:
    """Run a flux query and always return a single DataFrame (influx returns a
    list when the result spans multiple tables)."""
    raw = qapi.query_data_frame(flux)
    if isinstance(raw, list):
        return pd.concat(raw, ignore_index=True) if raw else pd.DataFrame()
    return raw


def build_flux(
    *,
    bucket: str,
    measurement: str,
    fields: list[str],
    start: str,
    stop: str,
    tags: dict[str, str] | None = None,
    pivot: bool = True,
    sort: bool = True,
) -> str:
    """The standard pivoted flux query every backtest stream uses:
    range → measurement → tag filters → field filter → pivot → sort."""
    lines = [
        f'from(bucket: "{bucket}")',
        f"  |> range(start: {start}, stop: {stop})",
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")',
    ]
    for k, v in (tags or {}).items():
        lines.append(f'  |> filter(fn: (r) => r["{k}"] == "{v}")')
    if fields:
        cond = " or ".join(f'r["_field"] == "{f}"' for f in fields)
        lines.append(f"  |> filter(fn: (r) => {cond})")
    if pivot:
        lines.append(
            '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")'
        )
    if sort:
        lines.append('  |> sort(columns: ["_time"])')
    return "\n".join(lines)


def load_influx(qapi, *, add_ts: bool = True, ts_col: str = "ts", **flux_kwargs) -> pd.DataFrame:
    """Run `build_flux(**flux_kwargs)` and return the DataFrame, adding a unix
    `ts_col` (seconds) derived from `_time` when present."""
    df = query_df(qapi, build_flux(**flux_kwargs))
    if add_ts and not df.empty and "_time" in df.columns:
        df[ts_col] = df["_time"].apply(lambda t: t.timestamp())
    return df
