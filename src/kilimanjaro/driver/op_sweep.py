"""Stage 13 — op sweep: the wide operator x base-column catalog.

The hand-authored factor library applies almost no causal operators: 22 of its 38
formulas are `zscore(raw_column, W)` and `diff` appears nowhere, even though a probe
showed `ema(diff(wimb,1),2)` beats the production `zscore(wimb)` by ~20% IC. This
module closes that gap systematically: every base column x every operator x its
params, measured identically.

MEASUREMENT ONLY — no fitting, no selection. Stage 13 produces a catalog; choosing
from it and fitting weights is stage 14's job, which is where train/holdout belongs.
The one discipline kept here is the window: run the catalog on the TRAIN span so that
whatever a human picks by scanning this table has not already consumed the holdout.

WHY IT STREAMS. The naive shape (build all factors, then measure) is impossible:
8,850 factors x 31.5M rows x 8 bytes is ~2 TB. Instead the outer loop is over BASE
COLUMNS — load a narrow frame (ts, the base's inputs, the target's mid/bid/ask),
evaluate that base's ~50 variants ONE AT A TIME, emit their rows, drop the frame.
Peak memory is one frame plus one factor (~3 GB/worker for a year), so the 177 bases
parallelize across cores. Cost per cell is unchanged; only the peak is bounded.

Each row carries the measures at every horizon: corr (Pearson), ic (Spearman rank),
gross (mid-to-mid bps on fires), net (the same trade at real bid/ask, minus latency
cost) and se (standard error of net). `op` and `param` are separate columns so the
table can be grouped by operator family rather than parsed out of 8,850 strings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.driver.signal_corr import _executable_bps

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_WARMUP_DAYS = 2
_IC_SUBSAMPLE = 1_500_000


@dataclass(frozen=True)
class Variant:
    """One factor: a base column, an operator applied to it, and a zscore window."""
    base: str
    base_expr: str
    op: str
    param: object
    zwin: int
    inner: str          # the op applied to the base, before normalization

    @property
    def name(self) -> str:
        return f"{self.base}|{self.op}{'' if self.param is None else self.param}|z{self.zwin}"

    @property
    def expr(self) -> str:
        return f"zscore({self.inner}, {self.zwin})"


def build_variants(cfg: dict) -> list[Variant]:
    """Expand {bases} x {ops x params} x {zscore_windows} into concrete factors."""
    out: list[Variant] = []
    for base, base_expr in cfg["bases"].items():
        for op, spec in cfg["ops"].items():
            for param in spec["params"]:
                inner = spec["tmpl"].format(x=f"({base_expr})", p=param)
                for zwin in cfg["zscore_windows"]:
                    out.append(Variant(base, base_expr, op, param, zwin, inner))
    return out


def _measure_base(args) -> list[dict]:
    """Measure every variant of ONE base column. Runs in a worker process.

    Loads a frame containing only what this base needs plus the target's mid/bid/ask,
    evaluates variants one at a time, and returns plain dicts (picklable).
    """
    (base, variants, markets, market_a, market_b, start, end, horizons,
     fire_threshold, cost_bps, data_root, warmup_days) = args
    from datetime import datetime, timedelta

    from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

    load_start = (datetime.strptime(start, "%Y-%m-%d")
                  - timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    # One expression naming every column this base needs, plus the target legs.
    load_expr = f"({variants[0].base_expr}) + (b_mid) + (b_bid) + (b_ask)"
    try:
        frame = ExpressionSignal.create(
            markets=markets, expr=load_expr, threshold=0.0, min_age=None, max_age=None,
            invert=False, label="op_sweep").load_data(load_start, end, Path(data_root))
    except Exception as exc:  # a base whose source is missing on this pair — skip, don't crash
        return [{"market_a": market_a, "market_b": market_b, "base": base,
                 "error": f"{type(exc).__name__}: {exc}"}]

    ts = frame["ts"].to_numpy(dtype=float)
    sec = np.floor(ts).astype(np.int64)
    in_win = sec >= int(datetime.strptime(start, "%Y-%m-%d").timestamp())
    mid = np.asarray(eval_expr("b_mid", frame), dtype=float)
    bid = np.asarray(eval_expr("b_bid", frame), dtype=float)
    ask = np.asarray(eval_expr("b_ask", frame), dtype=float)
    n_rows = len(mid)
    fwd = {h: _shift_ratio(mid, h) for h in horizons}

    win_idx = np.flatnonzero(in_win)
    if len(win_idx) > _IC_SUBSAMPLE:
        win_idx = win_idx[:: max(1, len(win_idx) // _IC_SUBSAMPLE)]

    rows = []
    for var in variants:
        try:
            v_full = np.asarray(eval_expr(var.expr, frame), dtype=float)
        except Exception as exc:
            rows.append({"market_a": market_a, "market_b": market_b, "base": base,
                         "op": var.op, "param": var.param, "zwin": var.zwin,
                         "factor": var.name, "expr": var.expr,
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        v = v_full[win_idx]
        row = {"market_a": market_a, "market_b": market_b, "base": base,
               "base_expr": var.base_expr, "op": var.op, "param": var.param,
               "zwin": var.zwin, "factor": var.name, "fire_threshold": fire_threshold}
        n_min, peak_ic, peak_h, best_net, best_net_h = None, np.nan, None, -np.inf, None
        for h in horizons:
            r = fwd[h][win_idx]
            ok = np.isfinite(v) & np.isfinite(r)
            n = int(ok.sum())
            n_min = n if n_min is None else min(n_min, n)
            if n < 30 or np.nanstd(v[ok]) == 0 or np.nanstd(r[ok]) == 0:
                corr = ric = float("nan")
            else:
                a, b = pd.Series(v[ok]), pd.Series(r[ok])
                corr, ric = float(a.corr(b)), float(a.corr(b, method="spearman"))
            g, net, n_fire, se = _executable_bps(
                v, win_idx, h, n_rows, mid, bid, ask, fire_threshold, cost_bps)
            row[f"corr{h}"] = round(corr, 5)
            row[f"ic{h}"] = round(ric, 5)
            row[f"gross{h}"] = g
            row[f"net{h}"] = net
            row[f"se{h}"] = se
            row["n_fire"] = n_fire
            if ric == ric and (peak_ic != peak_ic or abs(ric) > abs(peak_ic)):
                peak_ic, peak_h = ric, h
            if net == net and net > best_net:
                best_net, best_net_h = net, h
        row["n"] = n_min or 0
        row["peak_ic"] = peak_ic
        row["peak_ic_h"] = peak_h
        row["best_net"] = best_net if np.isfinite(best_net) else float("nan")
        row["best_net_h"] = best_net_h
        row["expr"] = var.expr
        rows.append(row)
    return rows


def _shift_ratio(mid: np.ndarray, h: int) -> np.ndarray:
    out = np.full(mid.shape, np.nan)
    if h < len(mid):
        out[:-h] = mid[h:] / mid[:-h] - 1.0
    return out


def run_sweep(cfg: dict, start: str, end: str, horizons: list[int], *,
              fire_threshold: float = 2.0, cost_bps: float = 1.0,
              workers: int | None = None, data_root: Path = _DEFAULT_DATA_ROOT,
              warmup_days: int = _WARMUP_DAYS, progress=print) -> pd.DataFrame:
    """Run the full base x op grid for one market pair. Parallel over BASE columns."""
    import multiprocessing as mp

    a, b = (_mkt(cfg["market_a"]), _mkt(cfg["market_b"]))
    markets = {"a": a, "b": b}
    market_a = f"{a['exchange']}:{a['symbol']}"
    market_b = f"{b['exchange']}:{b['symbol']}"

    variants = build_variants(cfg)
    by_base: dict[str, list] = {}
    for v in variants:
        by_base.setdefault(v.base, []).append(v)
    workers = workers or min(16, os.cpu_count() or 4)
    progress(f"op-sweep {market_a} -> {market_b} | {len(by_base)} bases x "
             f"{len(variants)//max(len(by_base),1)} variants = {len(variants):,} factors "
             f"| {len(horizons)} horizons | {start}..{end} | {workers} workers")

    tasks = [(base, vs, markets, market_a, market_b, start, end, horizons,
              fire_threshold, cost_bps, str(data_root), warmup_days)
             for base, vs in by_base.items()]
    rows: list[dict] = []
    with mp.get_context("spawn").Pool(workers) as pool:
        for i, out in enumerate(pool.imap_unordered(_measure_base, tasks), 1):
            rows.extend(out)
            if i % 10 == 0 or i == len(tasks):
                progress(f"  {i}/{len(tasks)} bases  ({len(rows):,} rows)")
    df = pd.DataFrame(rows)
    if "peak_ic" in df.columns:
        df = df.reindex(df.peak_ic.abs().sort_values(ascending=False, na_position="last").index)
    return df.reset_index(drop=True)


def _mkt(s: str) -> dict:
    e, sym, size = s.split(":")
    return {"exchange": e, "symbol": sym, "execution_size": size}
