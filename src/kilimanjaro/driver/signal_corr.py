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
from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_WARMUP_DAYS = 2  # converge the factors' rolling windows before the compared window


@dataclass(frozen=True)
class Factor:
    name: str
    expr: str


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
            fexpr = t[star + 2:-1].strip()
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
    ic: pd.DataFrame          # [factor, expr, horizon, n, ic, rank_ic]
    corr: pd.DataFrame        # factor × factor value-correlation matrix
    horizons: list[int]

    def ranked(self, horizon: int | None = None) -> pd.DataFrame:
        """IC table at one horizon (default: the largest), sorted by |rank_ic|."""
        h = horizon if horizon is not None else max(self.horizons)
        d = self.ic[self.ic.horizon == h].copy()
        d["abs_rank_ic"] = d.rank_ic.abs()
        return d.sort_values("abs_rank_ic", ascending=False).drop(columns="abs_rank_ic")


def compute(
    markets: dict,
    factors: list[Factor],
    target_mid_expr: str,
    start: str,
    end: str,
    horizons: list[int],
    data_root: Path = _DEFAULT_DATA_ROOT,
    warmup_days: int = _WARMUP_DAYS,
) -> SignalCorrResult:
    """Per-factor IC (vs `target_mid_expr` forward return) + factor-value corr matrix.

    `target_mid_expr` is the execution venue's mid on the feature frame (e.g. "b_mid").
    """
    from datetime import datetime, timedelta

    from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

    load_start = (datetime.strptime(start, "%Y-%m-%d")
                  - timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    # Load the frame once with an expr referencing every column the factors need.
    load_expr = " + ".join(f"({f.expr})" for f in factors) + f" + ({target_mid_expr})"
    frame = ExpressionSignal.create(
        markets=markets, expr=load_expr, threshold=0.0, min_age=None, max_age=None,
        invert=False, label="signal_corr").load_data(load_start, end, data_root)

    ts = frame["ts"].to_numpy(dtype=float)
    sec = np.floor(ts).astype(np.int64)
    in_win = sec >= int(datetime.strptime(start, "%Y-%m-%d").timestamp())

    mid = np.asarray(eval_expr(target_mid_expr, frame), dtype=float)
    # Forward return over h seconds on the ~1s grid (shift by h rows). Gross mid-to-mid.
    fwd = {h: _shift_ratio(mid, h) - 1.0 for h in horizons}

    values = {f.name: np.asarray(eval_expr(f.expr, frame), dtype=float) for f in factors}

    rows = []
    for f in factors:
        v = values[f.name]
        for h in horizons:
            r = fwd[h]
            ok = in_win & np.isfinite(v) & np.isfinite(r)
            n = int(ok.sum())
            if n < 30 or np.nanstd(v[ok]) == 0 or np.nanstd(r[ok]) == 0:
                ic = rank_ic = float("nan")
            else:
                a, b = pd.Series(v[ok]), pd.Series(r[ok])
                ic = float(a.corr(b))
                rank_ic = float(a.corr(b, method="spearman"))
            rows.append(dict(factor=f.name, expr=f.expr, horizon=h, n=n,
                             ic=round(ic, 5), rank_ic=round(rank_ic, 5)))
    ic_df = pd.DataFrame(rows)

    # Cross-factor value-correlation matrix (in-window, pairwise-complete).
    vdf = pd.DataFrame({f.name: values[f.name] for f in factors})[in_win]
    corr_df = vdf.corr().round(4)
    return SignalCorrResult(ic=ic_df, corr=corr_df, horizons=list(horizons))


def _shift_ratio(mid: np.ndarray, h: int) -> np.ndarray:
    """mid[t+h] / mid[t], nan where t+h is out of range."""
    out = np.full(mid.shape, np.nan)
    if h < len(mid):
        out[:-h] = mid[h:] / mid[:-h]
    return out
