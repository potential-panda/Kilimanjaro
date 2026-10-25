"""Stage 13 — signal_corr: per-factor correlation screen.

The cheap, wide top of the funnel (doc/research-pipeline.md). For each single
factor/formula it measures **correlation**, producing two artifacts:

- **IC** — the factor's information coefficient: correlation of the (causal) signal
  value at t with the target's **forward return** over a horizon h. Both Pearson
  (`ic`) and Spearman/rank (`rank_ic`, robust to outliers) are reported, across a
  grid of horizons. Sign of IC = the factor's directional polarity.
- **correlation matrix** — pairwise correlation of the factor *values*, so stage 14
  (blend_corr) can build *decorrelated* blends.

This stage RANKS; it does not hard-filter — a mediocre-IC factor may still earn a
place in a blend (14) or as a blend-of-one. Forward returns are mid-to-mid (gross,
no cost): IC measures prediction, not tradability — that is stage 15's job.

`factors_from_blend_expr` splits a composite `zscore(Σ wᵢ·(factorᵢ), W)` into its
factor terms, so screening the components of any blend spec is one call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_WARMUP_DAYS = 2  # converge the factors' rolling windows before the compared window


@dataclass(frozen=True)
class Factor:
    name: str
    expr: str
    family: str = ""


# ── blend-term extraction ─────────────────────────────────────────────────────


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split `s` on `sep` only at paren-depth 0."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(s[start:i])
            start = i + 1
    out.append(s[start:])
    return out


def _slug(expr: str) -> str:
    """Compact, filesystem-safe label derived from a factor expression."""
    e = expr.strip()
    m = re.match(r"zscore\((.+),\s*(\d+)\)$", e)
    if m:  # zscore(INPUT, W) -> z_<input>_<W>
        inp = re.sub(r"[^0-9a-zA-Z]+", "_", m.group(1)).strip("_")
        return f"z_{inp[:28]}_{m.group(2)}"
    s = re.sub(r"[^0-9a-zA-Z]+", "_", e).strip("_")
    return s[:32]


def factors_from_blend_expr(expr: str) -> list[Factor]:
    """Extract [Factor(name, term_expr)] from a composite `zscore(Σ wᵢ·(termᵢ), W)`.

    Each summand is `weight*(factor_expr)`; the wrapping weight and parens are
    stripped so each returned expr is the standalone factor the blend combines."""
    e = expr.strip()
    m = re.match(r"^zscore\((.*),\s*\d+\)$", e, re.DOTALL)
    inner = m.group(1) if m else e  # the Σ (or the raw expr if not a blend)
    factors: list[Factor] = []
    for term in _split_top_level(inner, "+"):
        t = term.strip()
        if not t:
            continue
        # weight*(factor): drop the leading `<weight>*(` and the matching trailing `)`
        star = t.find("*(")
        if star != -1 and t.endswith(")"):
            fexpr = t[star + 2 : -1].strip()
        else:
            fexpr = t  # bare term (no weight wrapper)
        factors.append(Factor(_slug(fexpr), fexpr))
    # de-dup identical exprs, keep order
    seen, uniq = set(), []
    for f in factors:
        if f.expr not in seen:
            seen.add(f.expr)
            uniq.append(f)
    return uniq


# ── IC + correlation matrix ───────────────────────────────────────────────────


@dataclass
class SignalCorrResult:
    ic: pd.DataFrame  # [family, factor, expr, horizon, n, ic, rank_ic]
    dist: pd.DataFrame  # one row/factor: best horizon, value distribution, IC stability
    corr: pd.DataFrame  # factor × factor value-correlation matrix
    horizons: list[int]

    def ranked(self, horizon: int | None = None) -> pd.DataFrame:
        """IC table at one horizon (default: the largest), sorted by |rank_ic|."""
        h = horizon if horizon is not None else max(self.horizons)
        d = self.ic[self.ic.horizon == h].copy()
        d["abs_rank_ic"] = d.rank_ic.abs()
        return d.sort_values("abs_rank_ic", ascending=False).drop(columns="abs_rank_ic")

    def summary(self) -> pd.DataFrame:
        """dist rows sorted by |best_rank_ic| — the standardized per-factor scorecard."""
        d = self.dist.copy()
        d["abs"] = d.best_rank_ic.abs()
        return d.sort_values("abs", ascending=False).drop(columns="abs")


def compute(
    markets: dict,
    factors: list[Factor],
    target_mid_expr: str,
    start: str,
    end: str,
    horizons: list[int],
    data_root: Path = _DEFAULT_DATA_ROOT,
    warmup_days: int = _WARMUP_DAYS,
    fire_threshold: float = 2.0,
    target_alias: str = "b",
) -> SignalCorrResult:
    """Per-factor IC (vs `target_mid_expr` forward return) + factor-value corr matrix,
    plus per-horizon EXECUTABLE economics at bid/ask.

    `target_mid_expr` is the execution venue's mid on the feature frame (e.g. "b_mid").

    IC answers "does it predict?"; it is scale- and shift-invariant, so executing at
    bid/ask instead of mid would barely move it (the spread is very nearly a constant
    offset). The question "can it CROSS the spread?" is therefore a MAGNITUDE question,
    answered by `gross<h>`/`net<h>`: the mean signed move in bps on the factor's fires
    (|value| >= `fire_threshold`), measured mid-to-mid (gross) and at the real
    executable prices — enter aggressive (ask to buy / bid to sell), exit passive —
    minus the flat latency cost (framework.costs). net > 0 is the step-1 gate that IC
    alone cannot express.
    """
    from datetime import datetime, timedelta

    from kilimanjaro.framework.costs import ROUND_TRIP_COST_BPS
    from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

    load_start = (
        datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
        - timedelta(days=warmup_days)
    ).strftime("%Y-%m-%d")
    # Load the frame once with an expr referencing every column the factors need —
    # including the target's bid/ask, so executable fills come from real quotes.
    bid_expr, ask_expr = f"{target_alias}_bid", f"{target_alias}_ask"
    load_expr = (
        " + ".join(f"({f.expr})" for f in factors)
        + f" + ({target_mid_expr}) + ({bid_expr}) + ({ask_expr})"
    )
    frame = ExpressionSignal.create(
        markets=markets,
        expr=load_expr,
        threshold=0.0,
        min_age=None,
        max_age=None,
        invert=False,
        label="signal_corr",
    ).load_data(load_start, end, data_root)

    ts = frame["ts"].to_numpy(dtype=float)
    sec = np.floor(ts).astype(np.int64)
    in_win = sec >= int(
        datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    )
    month = pd.PeriodIndex(pd.to_datetime(sec, unit="s"), freq="M")  # for IC stability

    mid = np.asarray(eval_expr(target_mid_expr, frame), dtype=float)
    bid = np.asarray(eval_expr(bid_expr, frame), dtype=float)
    ask = np.asarray(eval_expr(ask_expr, frame), dtype=float)
    n_rows = len(mid)
    # Forward return over h seconds on the ~1s grid (shift by h rows). Gross mid-to-mid.
    fwd = {h: _shift_ratio(mid, h) - 1.0 for h in horizons}

    values = {
        f.name: np.asarray(eval_expr(f.expr, frame), dtype=float) for f in factors
    }

    # Subsample the in-window rows for the correlation estimates. Adjacent-second forward
    # returns overlap heavily (autocorrelated), so a strided subsample to ~1.5M points is
    # a statistically equivalent (less overcounted) IC estimate that keeps a full-year scan
    # tractable. The value distribution is measured on the same subsample.
    win_idx = np.flatnonzero(in_win)
    cap = 1_500_000
    if len(win_idx) > cap:
        win_idx = win_idx[:: max(1, len(win_idx) // cap)]

    ic_rows, dist_rows = [], []
    for f in factors:
        v = values[f.name][win_idx]
        per_h = {}
        for h in horizons:
            r = fwd[h][win_idx]
            ok = np.isfinite(v) & np.isfinite(r)
            n = int(ok.sum())
            if n < 30 or np.nanstd(v[ok]) == 0 or np.nanstd(r[ok]) == 0:
                ic = rank_ic = float("nan")
            else:
                a, b = pd.Series(v[ok]), pd.Series(r[ok])
                ic = float(a.corr(b))
                rank_ic = float(a.corr(b, method="spearman"))
            per_h[h] = rank_ic
            gross_bps, net_bps, n_fire, se_bps = _executable_bps(
                v,
                win_idx,
                h,
                n_rows,
                mid,
                bid,
                ask,
                fire_threshold,
                ROUND_TRIP_COST_BPS,
            )
            ic_rows.append(
                {
                    "family": f.family,
                    "factor": f.name,
                    "expr": f.expr,
                    "horizon": h,
                    "n": n,
                    "ic": round(ic, 5),
                    "rank_ic": round(rank_ic, 5),
                    "n_fire": n_fire,
                    "gross_bps": gross_bps,
                    "net_bps": net_bps,
                    "se_bps": se_bps,
                }
            )
        dist_rows.append(_factor_dist(f, values[f.name], win_idx, per_h, fwd, month))
    ic_df = pd.DataFrame(ic_rows)
    dist_df = pd.DataFrame(dist_rows)

    # Cross-factor value-correlation matrix (subsampled in-window, pairwise-complete).
    vdf = pd.DataFrame({f.name: values[f.name][win_idx] for f in factors})
    corr_df = vdf.corr().round(4)
    return SignalCorrResult(
        ic=ic_df, dist=dist_df, corr=corr_df, horizons=list(horizons)
    )


def _executable_bps(v, win_idx, h, n_rows, mid, bid, ask, fire_threshold, cost_bps):
    """Mean signed move in bps on this factor's fires, gross (mid) and net (bid/ask).

    Fire = |factor| >= fire_threshold, direction = its sign. Execution follows the base
    cost policy: enter AGGRESSIVE (buy the ask / sell the bid) at t, exit PASSIVE at t+h,
    so the spread is paid inside the price — then subtract the flat latency cost. No
    separate spread estimate is added (that would double-count it).

    Returns (gross_bps, net_bps, n_fire, se_bps). `se_bps` is the standard error of the
    net mean (sd/sqrt(n)) — without it a cell built on 28 fires reads the same as one built
    on 28,000, which is how a wide sweep manufactures winners. The gap between gross and net
    IS the spread: what a correlation cannot show you, because corr is invariant to it.
    """
    fire = np.isfinite(v) & (np.abs(v) >= fire_threshold)
    if not fire.any():
        return float("nan"), float("nan"), 0, float("nan")
    src = win_idx[fire]  # positions at t
    dst = src + h  # positions at t+h
    keep = dst < n_rows
    src, dst = src[keep], dst[keep]
    if len(src) == 0:
        return float("nan"), float("nan"), 0, float("nan")
    d = np.sign(v[fire][keep])
    entry = np.where(d > 0, ask[src], bid[src])  # aggressive side at entry
    exit_ = np.where(d > 0, bid[dst], ask[dst])  # passive side at exit
    gross_mid = d * (mid[dst] / mid[src] - 1.0) * 1e4
    exec_bps = d * (exit_ - entry) / entry * 1e4
    ok = np.isfinite(gross_mid) & np.isfinite(exec_bps)
    if not ok.any():
        return float("nan"), float("nan"), 0, float("nan")
    n = int(ok.sum())
    return (
        round(float(gross_mid[ok].mean()), 3),
        round(float(exec_bps[ok].mean() - cost_bps), 3),
        n,
        round(float(exec_bps[ok].std(ddof=1) / np.sqrt(n)), 3)
        if n > 1
        else float("nan"),
    )


def decay_table(
    ic: pd.DataFrame, market_a: str, market_b: str, fire_threshold: float | None = None
) -> pd.DataFrame:
    """Reshape the long IC table into one row per factor, two columns per horizon.

    `compute()` emits [factor, horizon, ic, rank_ic] long — good for machines, poor for
    reading a DECAY PROFILE, which is a statement about one factor ACROSS horizons. This
    pivots so a row reads left-to-right as the decay curve:

        market_a, market_b, family, factor, n, peak_h, peak_ic,
        corr5, ic5, corr10, ic10, ... , expr

    `corr<h>` = Pearson (linear co-movement), `ic<h>` = Spearman rank (the robust IC the
    rest of the funnel ranks on). Both are kept because they disagree in an informative
    way: Pearson >> Spearman means the correlation is carried by a few extreme values.

    market_a/market_b are carried as COLUMNS, not encoded in the output path, so decay
    tables from different runs (other leaders, other venues, other symbols) concatenate
    into one survey.
    """
    horizons = sorted(ic.horizon.unique())
    rows = []
    for (fam, factor, expr), g in ic.groupby(["family", "factor", "expr"], sort=False):
        g = g.set_index("horizon")
        row = {
            "market_a": market_a,
            "market_b": market_b,
            "family": fam,
            "factor": factor,
            "n": int(g.n.min()),
        }
        if (
            fire_threshold is not None
        ):  # gross/net depend on it — keep the file self-describing
            row["fire_threshold"] = fire_threshold
        # peak of the decay curve: where |rank IC| is largest (a factor that peaks at the
        # shortest horizon and decays monotonically is the classic lead-lag shape).
        valid = {h: g.rank_ic.get(h) for h in horizons if pd.notna(g.rank_ic.get(h))}
        row["peak_h"] = max(valid, key=lambda h: abs(valid[h])) if valid else None
        row["peak_ic"] = valid.get(row["peak_h"]) if valid else float("nan")
        row["n_fire"] = int(g.n_fire.max()) if "n_fire" in g else 0
        for h in horizons:
            row[f"corr{h}"] = g.ic.get(h, float("nan"))
            row[f"ic{h}"] = g.rank_ic.get(h, float("nan"))
            if "gross_bps" in g:
                row[f"gross{h}"] = g.gross_bps.get(h, float("nan"))
                row[f"net{h}"] = g.net_bps.get(h, float("nan"))
                row[f"se{h}"] = (
                    g.se_bps.get(h, float("nan")) if "se_bps" in g else float("nan")
                )
        # best NET horizon — the tradability peak, which need not be the IC peak (IC
        # peaks short, where moves are smallest relative to the spread).
        nets = {h: row.get(f"net{h}") for h in horizons if pd.notna(row.get(f"net{h}"))}
        row["best_net_h"] = max(nets, key=lambda h: nets[h]) if nets else None
        row["best_net"] = nets.get(row["best_net_h"]) if nets else float("nan")
        row["expr"] = expr
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.reindex(
        out.peak_ic.abs().sort_values(ascending=False).index
    ).reset_index(drop=True)


def _factor_dist(
    f: Factor, v_full: np.ndarray, win_idx, per_h: dict, fwd: dict, month
) -> dict:
    """Per-factor scorecard: value distribution + best-horizon IC + monthly IC stability.
    Operates on the subsampled in-window rows (`win_idx` = integer positions)."""
    v = v_full[win_idx]
    s = pd.Series(v[np.isfinite(v)])
    # best horizon = max |rank_ic|
    valid = {h: r for h, r in per_h.items() if np.isfinite(r)}
    best_h = max(valid, key=lambda h: abs(valid[h])) if valid else max(per_h)
    best_ic = per_h.get(best_h, float("nan"))
    # monthly IC at best horizon → fraction of months matching the overall sign.
    # Vectorized Pearson per month (fast: grouped means of v, r, v·r, v², r²).
    r = fwd[best_h][win_idx]
    mo = month[win_idx]
    ok = np.isfinite(v) & np.isfinite(r)
    g = pd.DataFrame({"m": mo[ok].astype(str), "v": v[ok], "r": r[ok]})
    g["vr"] = g.v * g.r
    g["v2"] = g.v**2
    g["r2"] = g.r**2
    gm = g.groupby("m").agg(["mean", "size"])
    n_m = gm[("v", "size")]
    mv, mr, mvr = gm[("v", "mean")], gm[("r", "mean")], gm[("vr", "mean")]
    denom = np.sqrt((gm[("v2", "mean")] - mv**2) * (gm[("r2", "mean")] - mr**2))
    mic = ((mvr - mv * mr) / denom).where(n_m > 30).dropna()
    sign = np.sign(best_ic) if np.isfinite(best_ic) else 0
    pos_frac = (
        float((np.sign(mic) == sign).mean()) if len(mic) and sign else float("nan")
    )
    return {
        "family": f.family,
        "factor": f.name,
        "expr": f.expr,
        "best_h": best_h,
        "best_rank_ic": round(best_ic, 5),
        "val_mean": round(float(s.mean()), 4),
        "val_std": round(float(s.std()), 4),
        "val_skew": round(float(s.skew()), 3),
        "p01": round(float(s.quantile(0.01)), 3),
        "p50": round(float(s.quantile(0.50)), 3),
        "p99": round(float(s.quantile(0.99)), 3),
        "active_frac": round(float((s.abs() >= 2.0).mean()), 4),  # |z|>=2 share
        "n_months": len(mic),
        "ic_month_pos": round(pos_frac, 3),
        "ic_month_mean": round(float(mic.mean()), 5) if len(mic) else float("nan"),
    }


def _shift_ratio(mid: np.ndarray, h: int) -> np.ndarray:
    """mid[t+h] / mid[t], nan where t+h is out of range."""
    out = np.full(mid.shape, np.nan)
    if h < len(mid):
        out[:-h] = mid[h:] / mid[:-h]
    return out
