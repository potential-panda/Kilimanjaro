"""Linear IC-weighted blend — the parameter-free combination baseline (step 3).

Why this exists: a single formula rarely clears cost alone (per-signal IC is
tiny). Real edge comes from *combining* many weak, weakly-correlated signals —
IC_combined ~= IC * sqrt(N) for N independent signals. This module builds the
simplest honest combination so it can be a floor an LGBM blend must beat.

**Blend the continuous factor, not the thresholded fired events.** Each formula
is `eval_expr(expr, frame)` over the shared 1s feature frame — the raw predictor
value, *before* any threshold/age/invert. The threshold is a downstream trading
decision applied to the *blended* signal; blending on the continuous factor is
the standard multi-factor construction and keeps the magnitude information a
threshold would throw away. `invert` is irrelevant here — IC-weighting sets a
factor's sign automatically (a down-predictor gets a negative weight).

The blend is **IC-weighted**: on the TRAIN window, z-score each factor and set
its weight to its IC (corr with the forward return); then

    blended(t) = sum_i IC_i * z_i(t)

No fitted parameters — means, stds and ICs are computed on train, the blend is a
fixed weighted sum, so its holdout number is trustworthy. This is the diagonal
(Sigma = I) optimal combination; it ignores redundancy between factors, which is
the deliberate, overfit-proof baseline. The full `w = Sigma^-1 . IC` version is
a later step, only once there are few enough factors to estimate Sigma stably.

Predictive-power measurement (this is step 1, not step 2): the forward return is
plain mid-to-mid over the horizon, **no cost**, on a horizon-decimated
**non-overlapping** sample so IC is honest by construction. Whether the blended
signal is *tradeable* (bid/ask, gate, cost) is the separate step-2 question, run
on the blended series as one signal — not answered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.price_cache import load_target_prices
from kilimanjaro.framework import MarketConfig, split_windows
from kilimanjaro.return_calc.fixed_horizon import _nonoverlap_indices
from kilimanjaro.signal.expression import eval_expr, validate_expr

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


@dataclass(frozen=True)
class Factor:
    """One continuous predictor: a formula over a market set. `name` is for
    reporting only; identity for dedup is (markets, expr) — threshold, age and
    invert are trading params, not part of the factor."""

    name: str
    markets: tuple  # frozen ((alias, exchange, symbol, size), ...)
    expr: str

    @property
    def key(self) -> tuple:
        return (self.markets, self.expr)

    @property
    def markets_dict(self) -> dict[str, dict]:
        return {
            alias: {"exchange": ex, "symbol": sym, "execution_size": size}
            for alias, ex, sym, size in self.markets
        }

    @property
    def columns(self) -> list[str]:
        return validate_expr(self.expr)


def _freeze_markets(markets: dict[str, dict]) -> tuple:
    return tuple(
        (alias, m["exchange"], m["symbol"], m.get("execution_size"))
        for alias, m in sorted(markets.items())
    )


def factors_from_specs(specs: list[dict]) -> list[Factor]:
    """Collect the distinct continuous factors across specs. Every (label, expr)
    in every spec becomes one factor; duplicates (same markets + expr) collapse.
    invert is dropped on purpose — IC-weighting handles direction."""
    seen: set[tuple] = set()
    out: list[Factor] = []
    for spec in specs:
        markets = _freeze_markets(spec["markets"])
        hyp = spec.get("hypothesis_id", "?")
        for e in spec["expressions"]:
            f = Factor(
                name=f"{hyp}:{e.get('label') or e['expr']}",
                markets=markets,
                expr=e["expr"],
            )
            if f.key not in seen:
                seen.add(f.key)
                out.append(f)
    return out


def _forward_return_bps(
    ts: np.ndarray,
    target: MarketConfig,
    start: str,
    end: str,
    horizon: int,
    data_root: Path,
) -> np.ndarray:
    """Plain mid-to-mid forward return (bps) over `horizon` seconds at each ts.

    No cost, no bid/ask — this measures predictive power (step 1). Entry mid at
    the first tick at/after ts, exit mid at the first tick at/after ts+horizon.
    """
    mp = load_target_prices(
        target.exchange, target.symbol, target.execution_size, start, end, data_root
    )
    n = len(mp.ts)
    mid = mp.mid
    ein = mp.first_at_or_after(ts)
    eout = mp.first_at_or_after(ts + horizon)
    ok = (ein < n) & (eout < n)
    ei = np.clip(ein, 0, max(n - 1, 0))
    xi = np.clip(eout, 0, max(n - 1, 0))
    entry = np.where(ok, mid[ei], np.nan)
    exit_ = np.where(ok, mid[xi], np.nan)
    return (exit_ - entry) / entry * 10000.0


def build_panel(
    factors: list[Factor],
    target: MarketConfig,
    start: str,
    end: str,
    horizon: int,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Continuous-factor panel for one window: columns = one per factor (raw
    factor value on the 1s grid) + `fwd_ret` (mid-to-mid forward return), inner-
    joined on ts, NaN rows dropped, then decimated to a horizon-spaced
    non-overlapping subset so the rows are independent for IC.

    Returns a DataFrame [ts, <factor.name> ..., fwd_ret]; empty if no overlap.
    """
    from kilimanjaro.data.feature_frame import load_feature_frame

    series: dict[str, pd.Series] = {}
    missing: list[str] = []
    for f in factors:
        try:
            fr = load_feature_frame(f.markets_dict, f.columns, start, end, data_root=data_root)
        except Exception:  # noqa: BLE001
            # This factor can't be built in this window — no rows (BookChange is
            # absent in the holdout months) or a column its market lacks (some
            # venues have no BookState best_mid). Treat it as entirely silent: a
            # NaN column, imputed neutral downstream — never crash the whole blend
            # for one factor.
            missing.append(f.name)
            continue
        val = eval_expr(f.expr, fr).to_numpy(dtype=float)
        series[f.name] = pd.Series(val, index=fr["ts"].to_numpy(), name=f.name)

    if not series:
        return pd.DataFrame()

    # Outer-join on ts to the UNION grid: a factor with narrow coverage (e.g.
    # BookChange spans only weeks) must not shrink the panel to its own span — it
    # contributes 0 (neutral) where it is absent, not delete those timestamps for
    # every other factor. A factor NaN anywhere is imputed neutral downstream; only
    # rows where every factor is NaN are dropped. (fwd_ret is required, below.)
    panel = pd.concat(series.values(), axis=1, join="outer").sort_index()
    for name in missing:  # absent-in-window factors: all-neutral columns
        panel[name] = np.nan
    names = [f.name for f in factors]
    panel = panel.dropna(how="all", subset=names)
    if panel.empty:
        return panel.reset_index().rename(columns={"index": "ts"})

    ts = panel.index.to_numpy(dtype=float)
    panel = panel.reset_index(drop=True)
    panel.insert(0, "ts", ts)
    panel["fwd_ret"] = _forward_return_bps(
        ts, target, start, end, horizon, data_root
    )
    panel = panel.dropna(subset=["fwd_ret"]).reset_index(drop=True)
    if len(panel) > 1:  # honest inference: keep a non-overlapping subset
        keep = _nonoverlap_indices(panel["ts"].to_numpy(dtype=float), float(horizon))
        panel = panel.iloc[keep].reset_index(drop=True)
    return panel


def composite_spec(
    specs: list[dict],
    factors: list[Factor],
    weights: dict[str, float],
    *,
    thresholds: list[float],
    horizon: int,
    min_abs_weight: float = 0.01,
    return_calc: str = "episode",
    barrier_bps: float = 5.0,
    time_cap: int | None = None,
) -> dict:
    """Turn a fitted blend into a single tradeable spec: one ExpressionSignal
    whose formula is the IC-weighted sum of the factor expressions, wrapped in a
    rolling zscore so a std-unit threshold trades it. This is the bridge from
    the step-1 blend (IC only) to the step-2 sim (bid/ask, gate, cost) — it runs
    through the exact same episode machinery every other spec does.

    Factors below `min_abs_weight` (the ~0-IC duds) are dropped from the sum.
    Markets are the union across the input specs (aliases must be consistent).
    """
    markets: dict[str, dict] = {}
    for s in specs:
        for alias, m in s["markets"].items():
            if alias in markets and markets[alias] != m:
                raise ValueError(f"alias {alias!r} maps to different markets across specs")
            markets[alias] = m

    terms = [
        (f, weights.get(f.name, 0.0))
        for f in factors
        if abs(weights.get(f.name, 0.0)) >= min_abs_weight
    ]
    if not terms:
        raise ValueError("no factor clears the weight floor")
    summed = " + ".join(f"{w:.6f}*({f.expr})" for f, w in terms)
    expr = f"zscore({summed}, 1800)"

    return {
        "hypothesis_id": "blend-composite",
        "family": "blend",
        "markets": markets,
        "target": dict(specs[0]["target"]),
        "return_calc": return_calc,
        "expressions": [{"label": "blend", "expr": expr, "invert": [False]}],
        "thresholds": list(thresholds),
        "age_windows": [[None, None]],
        "horizons": [horizon],
        "max_holds": [None],
        # triple_barrier params (ignored by other return_calcs)
        "barriers": [barrier_bps],
        "time_caps": [time_cap if time_cap is not None else horizon],
    }


def _ic(x: np.ndarray, r: np.ndarray) -> float:
    """Pearson corr over the rows where both are present; 0.0 when too few
    finite pairs or a series is constant. A factor with partial coverage is
    scored on its own present rows — not penalised for the seconds it is silent."""
    m = np.isfinite(x) & np.isfinite(r)
    if m.sum() < 3:
        return 0.0
    x, r = x[m], r[m]
    sx, sr = np.std(x), np.std(r)
    if sx == 0 or sr == 0 or not np.isfinite(sx) or not np.isfinite(sr):
        return 0.0
    c = np.corrcoef(x, r)[0, 1]
    return float(c) if np.isfinite(c) else 0.0


def blend(
    factors: list[Factor],
    target: MarketConfig,
    start: str,
    split: str,
    end: str,
    horizon: int,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict:
    """Fit the IC-weighted blend on train, evaluate on holdout.

    Weights are TRAIN ICs; z-score stats (mean/std) are TRAIN — so the holdout
    number has nothing fitted to it. Returns a dict with per-factor ICs, the
    blended IC on both windows, and the best single-factor holdout |IC| (the
    thing the blend must beat to have bought any diversification).
    """
    tr_start, tr_end, ho_start, ho_end = split_windows(start, split, end)
    train = build_panel(factors, target, tr_start, tr_end, horizon, data_root)
    holdout = build_panel(factors, target, ho_start, ho_end, horizon, data_root)

    names = [f.name for f in factors]
    r_tr = train["fwd_ret"].to_numpy(dtype=float)
    r_ho = holdout["fwd_ret"].to_numpy(dtype=float)

    per_factor: list[dict] = []
    z_tr: dict[str, np.ndarray] = {}
    z_ho: dict[str, np.ndarray] = {}
    weights: dict[str, float] = {}
    for name in names:
        x_tr = train[name].to_numpy(dtype=float)
        x_ho = holdout[name].to_numpy(dtype=float)
        # Train stats over present rows only; a mostly-silent factor is still
        # standardised by the scale of the readings it does have.
        mu, sd = float(np.nanmean(x_tr)), float(np.nanstd(x_tr))
        if not np.isfinite(mu):  # column entirely NaN on train
            mu = 0.0
        sd = sd if (sd > 0 and np.isfinite(sd)) else 1.0
        zt = (x_tr - mu) / sd  # NaN preserved -> scored on present rows by _ic
        zh = (x_ho - mu) / sd  # train stats on holdout: no peeking
        ic_tr = _ic(zt, r_tr)
        ic_ho = _ic(zh, r_ho)
        # In the blend sum a silent second contributes 0 (neutral), never drops
        # the row — that is the whole point of the coverage fix.
        z_tr[name] = np.nan_to_num(zt, nan=0.0)
        z_ho[name] = np.nan_to_num(zh, nan=0.0)
        weights[name] = ic_tr if np.isfinite(ic_tr) else 0.0
        per_factor.append(
            {"factor": name, "weight": ic_tr, "train_ic": ic_tr, "holdout_ic": ic_ho}
        )

    blend_tr = np.zeros(len(train)) if len(train) else np.zeros(0)
    blend_ho = np.zeros(len(holdout)) if len(holdout) else np.zeros(0)
    for name in names:
        blend_tr = blend_tr + weights[name] * z_tr[name]
        blend_ho = blend_ho + weights[name] * z_ho[name]

    blend_ic_tr = _ic(blend_tr, r_tr)
    blend_ic_ho = _ic(blend_ho, r_ho)
    best_single_ho = max((abs(p["holdout_ic"]) for p in per_factor), default=0.0)

    per_factor.sort(key=lambda p: -abs(p["holdout_ic"]))
    return {
        "n_factors": len(names),
        "horizon": horizon,
        "train_n": int(len(train)),
        "holdout_n": int(len(holdout)),
        "blend_train_ic": blend_ic_tr,
        "blend_holdout_ic": blend_ic_ho,
        "best_single_holdout_ic": best_single_ho,
        "diversification_ratio": (
            abs(blend_ic_ho) / best_single_ho if best_single_ho else float("nan")
        ),
        "per_factor": per_factor,
    }


# ── stage 14: multi-method blend construction + ranking ───────────────────────
# The baseline `blend()` above is one method (IC-weighted, Σ=I). Stage 14 builds
# SEVERAL candidate blends from the same factor set and ranks them by honest
# HOLDOUT IC — every weight is fit on train, so the holdout number has nothing
# fitted to it. A strong single factor competes as `single_best` (a blend of one).

def _standardize(train: pd.DataFrame, holdout: pd.DataFrame, names: list[str]):
    """z-standardize each factor on TRAIN stats → (Z_tr, Z_ho matrices, ic_tr, ic_ho,
    r_tr, r_ho). NaN (silent second) → 0 (neutral) so it never drops a row."""
    r_tr = train["fwd_ret"].to_numpy(dtype=float)
    r_ho = holdout["fwd_ret"].to_numpy(dtype=float)
    Ztr, Zho, ic_tr, ic_ho = [], [], [], []
    for name in names:
        xt, xh = train[name].to_numpy(dtype=float), holdout[name].to_numpy(dtype=float)
        mu = float(np.nanmean(xt)); mu = 0.0 if not np.isfinite(mu) else mu
        sd = float(np.nanstd(xt)); sd = sd if (sd > 0 and np.isfinite(sd)) else 1.0
        zt = np.nan_to_num((xt - mu) / sd, nan=0.0)
        zh = np.nan_to_num((xh - mu) / sd, nan=0.0)
        Ztr.append(zt); Zho.append(zh)
        ic_tr.append(_ic(zt, r_tr)); ic_ho.append(_ic(zh, r_ho))
    return (np.array(Ztr).T, np.array(Zho).T,
            np.array(ic_tr), np.array(ic_ho), r_tr, r_ho)


def _greedy_weights(Ztr: np.ndarray, ic_tr: np.ndarray, r_tr: np.ndarray,
                    min_gain: float = 1e-4) -> np.ndarray:
    """Forward-select factors (each added at its IC weight) while the TRAIN blend IC
    keeps improving — drops redundant/noise factors that a full IC-weighted sum keeps."""
    k = Ztr.shape[1]
    w = np.zeros(k)
    remaining = set(range(k))
    best = 0.0
    while remaining:
        gains = {}
        for j in remaining:
            w2 = w.copy(); w2[j] = ic_tr[j]
            gains[j] = abs(_ic(Ztr @ w2, r_tr))
        j = max(gains, key=gains.get)
        if gains[j] <= best + min_gain:
            break
        w[j] = ic_tr[j]; remaining.discard(j); best = gains[j]
    return w


def scan_blends(
    factors: list[Factor],
    target: MarketConfig,
    start: str,
    split: str,
    end: str,
    horizon: int,
    ridge: float = 0.2,
    use_lgbm: bool = False,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict:
    """Build + rank candidate blends of `factors`. Methods (all fit on TRAIN):
      single_best  — the top-|IC| single factor (the blend-of-one to beat)
      ic_weighted  — w = IC          (Σ=I diagonal baseline)
      ic_decorr    — w = (Σ+ridge·I)⁻¹·IC   (max-IC portfolio; down-weights redundancy)
      greedy       — forward-select factors that improve the train blend IC
      lgbm         — (use_lgbm) gradient-boosted nonlinear capacity ceiling; not a formula
    Each is evaluated on the untouched HOLDOUT. Returns ranked results + per-factor IC.
    """
    tr_s, tr_e, ho_s, ho_e = split_windows(start, split, end)
    train = build_panel(factors, target, tr_s, tr_e, horizon, data_root)
    holdout = build_panel(factors, target, ho_s, ho_e, horizon, data_root)
    names = [f.name for f in factors]
    if train.empty or holdout.empty:
        return {"results": [], "names": names, "train_n": len(train), "holdout_n": len(holdout)}

    Ztr, Zho, ic_tr, ic_ho, r_tr, r_ho = _standardize(train, holdout, names)
    k = len(names)
    Sigma = np.corrcoef(Ztr.T) if k > 1 else np.array([[1.0]])
    Sigma = np.nan_to_num(Sigma, nan=0.0)
    np.fill_diagonal(Sigma, 1.0)

    cand: dict[str, np.ndarray] = {}
    jb = int(np.argmax(np.abs(ic_tr)))
    cand["single_best"] = np.eye(k)[jb] * ic_tr[jb]
    cand["ic_weighted"] = ic_tr.copy()
    cand["ic_decorr"] = np.linalg.solve(Sigma + ridge * np.eye(k), ic_tr)
    cand["greedy"] = _greedy_weights(Ztr, ic_tr, r_tr)

    best_single_ho = float(np.max(np.abs(ic_ho))) if k else 0.0
    results = []
    for method, w in cand.items():
        used = {names[i]: float(w[i]) for i in range(k) if abs(w[i]) >= 1e-9}
        ho_ic = _ic(Zho @ w, r_ho)
        results.append({
            "method": method,
            "n_factors": len(used),
            "train_ic": round(_ic(Ztr @ w, r_tr), 5),
            "holdout_ic": round(ho_ic, 5),
            "div_ratio": round(abs(ho_ic) / best_single_ho, 3) if best_single_ho else float("nan"),
            "weights": used,
        })
    lgbm_importances = None
    if use_lgbm:
        lg = _lgbm_blend(train, holdout, names)  # nonlinear capacity, no linear weights
        lgbm_importances = lg["importances"]
        results.append({
            "method": "lgbm", "n_factors": len(names),
            "train_ic": round(lg["train_ic"], 5), "holdout_ic": round(lg["holdout_ic"], 5),
            "div_ratio": round(abs(lg["holdout_ic"]) / best_single_ho, 3) if best_single_ho else float("nan"),
            "weights": {},  # a tree model, not a formula — importances stored separately
        })
    results.sort(key=lambda r: -abs(r["holdout_ic"]))
    per_factor = sorted(
        [{"factor": names[i], "train_ic": round(float(ic_tr[i]), 5),
          "holdout_ic": round(float(ic_ho[i]), 5),
          "stable": bool(np.sign(ic_tr[i]) == np.sign(ic_ho[i]) and abs(ic_ho[i]) > 0.005)}
         for i in range(k)],
        key=lambda p: -abs(p["holdout_ic"]))
    return {
        "results": results, "per_factor": per_factor, "names": names,
        "horizon": horizon, "train_n": int(len(train)), "holdout_n": int(len(holdout)),
        "best_single_holdout_ic": best_single_ho, "lgbm_importances": lgbm_importances,
    }


def _lgbm_blend(train: pd.DataFrame, holdout: pd.DataFrame, names: list[str],
                seed: int = 0) -> dict:
    """LGBM capacity layer: a heavily-regularized gradient-boosted regression of the
    factors onto the forward return, fit on TRAIN, scored by holdout IC. This is NOT a
    tradeable formula — it measures the *nonlinear* predictive ceiling, so we can see how
    much (if any) edge sits beyond the linear decorrelated blend. LGBM handles NaN natively,
    so factors are fed raw (no neutral imputation). Returns train/holdout IC + importances."""
    import lightgbm as lgb

    Xtr, ytr = train[names].to_numpy(dtype=float), train["fwd_ret"].to_numpy(dtype=float)
    Xho, yho = holdout[names].to_numpy(dtype=float), holdout["fwd_ret"].to_numpy(dtype=float)
    # Strong regularization + big leaves so it can't memorize the ~0.01-R² return noise:
    # small trees, tiny learning rate, huge min_child_samples, row/col subsampling.
    m = lgb.LGBMRegressor(
        objective="regression", n_estimators=200, learning_rate=0.02,
        num_leaves=15, min_child_samples=5000, subsample=0.7, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=1.0, max_depth=4,
        random_state=seed, n_jobs=-1, verbosity=-1,
    )
    m.fit(Xtr, ytr)
    imp = m.feature_importances_.astype(float)
    imp = imp / imp.sum() if imp.sum() else imp
    return {
        "train_ic": _ic(m.predict(Xtr), ytr),
        "holdout_ic": _ic(m.predict(Xho), yho),
        "importances": {names[i]: round(float(imp[i]), 4) for i in range(len(names))},
    }
