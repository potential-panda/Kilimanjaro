"""LGBM meta-labeling on top of the linear composite (step 3+).

The linear composite (`driver/blend.composite_spec`) is the PRIMARY model: it
decides WHEN to fire and WHICH direction, by `|composite z| > threshold`. That
selector is just "rank by |z|, take the top." This module asks whether a
secondary LGBM model — trained to predict *whether a fired trade is profitable*
(López de Prado meta-labeling) — is a BETTER selector than the raw |z| threshold.

Why meta-labeling and not a primary LGBM (given the SNR write-up in blend.py):
- At IC~0.07 the target is ~99.5% noise; asking a flexible model to find
  direction from scratch overfits. Meta-labeling never picks direction — it only
  filters an already-trusted primary signal, a far lower-variance job.
- The primary threshold is deliberately LOOSE (default 1.5): high recall, many
  candidates incl. many losers; the meta model adds precision by dropping the
  losers. A tight primary (z>=3.5) would leave too few candidates to fit on.

Label: triple-barrier realized return, NET of the flat latency cost; `y = 1` if
net > 0. Features (point-in-time, causal, at the signal ts):
  - each blend factor's raw value, and the composite z (`strength`)
  - jst_hour, jst_weekday (let the model learn the session effect found by hand)

Honest evaluation (the whole point): compare two RANKINGS of the same holdout
candidate pool at matched trade counts —
  incumbent  : rank by |composite z|   (what z>=3.5 does)
  challenger : rank by LGBM P(profitable)
If the challenger's holdout net dominates at matched N, interaction alpha beyond
the linear blend exists. If not, |z| is already the best selector — a clean,
valuable negative result. The split is time-based; the triple-barrier decimation
(non-overlapping, time_cap-spaced) keeps trades independent, and train/holdout
are built on disjoint windows so no barrier straddles the boundary.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.root import data_root as _resolve_data_root
from kilimanjaro.driver.blend_corr import Factor
from kilimanjaro.framework.costs import ROUND_TRIP_COST_BPS
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn
from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

_DEFAULT_DATA_ROOT = None

# Trade-count budgets (as a fraction of the holdout candidate pool) at which the
# meta ranking is compared to the |z| ranking.
DEFAULT_FRACTIONS = (0.05, 0.10, 0.20, 0.30, 0.50, 1.0)


def factor_columns(factors: list[Factor]) -> dict[str, str]:
    """Stable feature-column name -> human factor name, one per factor."""
    return {f"fac_{i:02d}": f.name for i, f in enumerate(factors)}


def build_candidate_table(
    composite_expr: str,
    factors: list[Factor],
    markets: dict[str, dict],
    target: MarketConfig,
    primary_threshold: float,
    start: str,
    end: str,
    *,
    barrier_bps: float,
    time_cap: int,
    jst_hours: set[int] | None = None,
    data_root: Path | None = _DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Fire the composite (loose primary), label each trade by triple-barrier net
    return, attach point-in-time factor features. One row per non-overlapping
    candidate trade: [ts, direction, strength, return_bps, net_bps, label,
    jst_hour, jst_weekday, fac_00..fac_kk]. Every factor gets a column (NaN if it
    can't be built in the window) so train/holdout columns always align.
    """
    data_root = _resolve_data_root(data_root)
    sig = ExpressionSignal.create(
        markets=markets, expr=composite_expr, threshold=primary_threshold
    )
    frame = sig.load_data(start, end, data_root)
    fired = sig.generate(frame)
    if fired.empty:
        return pd.DataFrame()

    rc = TripleBarrierReturn(barrier_bps=barrier_bps, time_cap_seconds=time_cap)
    trades = rc.compute(fired, target, start, end, data_root)
    if trades.empty:
        return pd.DataFrame()

    if jst_hours is not None:
        trades = trades[trades["jst_hour"].isin(jst_hours)].reset_index(drop=True)
        if trades.empty:
            return pd.DataFrame()

    # Point-in-time factor values on the frame grid, aligned to each trade ts.
    frame_ts = frame["ts"].to_numpy(dtype=float)
    cols = factor_columns(factors)
    fdata: dict[str, np.ndarray] = {}
    for col, f in zip(cols, factors):
        try:
            fdata[col] = eval_expr(f.expr, frame).to_numpy(dtype=float)
        except Exception:  # noqa: BLE001 — factor absent in window -> NaN column
            fdata[col] = np.full(len(frame), np.nan)
    fdf = pd.DataFrame(fdata, index=frame_ts)
    feat = fdf.reindex(trades["ts"].to_numpy(dtype=float)).reset_index(drop=True)

    out = trades[
        ["ts", "direction", "strength", "return_bps", "jst_hour", "jst_weekday"]
    ].copy()
    for col in cols:
        out[col] = feat[col].to_numpy()
    out["net_bps"] = out["return_bps"] - ROUND_TRIP_COST_BPS
    out["label"] = (out["net_bps"] > 0).astype(int)
    return out.reset_index(drop=True)


def feature_cols_for(factors: list[Factor]) -> list[str]:
    """The LGBM feature set: every factor + the composite z + time-of-day."""
    return list(factor_columns(factors)) + ["strength", "jst_hour", "jst_weekday"]


def _rank_ic(pred: np.ndarray, actual: np.ndarray) -> float:
    """Spearman rank correlation, computed as Pearson over ranks (no scipy)."""
    if len(pred) < 3:
        return float("nan")
    rp = pd.Series(pred).rank().to_numpy()
    ra = pd.Series(actual).rank().to_numpy()
    if np.std(rp) == 0 or np.std(ra) == 0:
        return float("nan")
    return float(np.corrcoef(rp, ra)[0, 1])


def fit_meta(
    train: pd.DataFrame,
    holdout: pd.DataFrame,
    feature_cols: list[str],
    *,
    objective: str = "classify",
    seed: int = 0,
) -> dict:
    """Fit a heavily-regularized LGBM meta-model on train, early-stopping on a
    time-separated tail of train, and score holdout.

    objective:
      "classify" (v1) — LGBMClassifier on the binary net>0 label; ranks trades by
        P(profitable). Optimizes hit COUNT, not PnL — a big winner counts the same
        as a tiny one.
      "regress" (v2) — LGBMRegressor on net_bps with a robust L1 objective; ranks
        trades by EXPECTED net return. Magnitude-aware: it is rewarded for putting
        the big winners on top, which is what actually drives PnL. L1 (not L2) so
        the fat-tailed net distribution doesn't let a few outliers dominate the fit.

    Either way `preds` is a score to rank by (higher = take), so compare_rankings
    treats both identically. Returns preds, importances, and mode-specific holdout
    skill: AUC (classify) or rank_ic/ic (regress)."""
    import lightgbm as lgb

    tr = train.sort_values("ts").reset_index(drop=True)
    n_val = max(1, int(len(tr) * 0.15))
    fit_part, val_part = tr.iloc[:-n_val], tr.iloc[-n_val:]

    params = {
        "learning_rate": 0.02,
        "num_leaves": 15,
        "min_child_samples": 200,
        "subsample": 0.7,
        "subsample_freq": 1,
        "colsample_bytree": 0.7,
        "reg_lambda": 5.0,
        "reg_alpha": 1.0,
        "random_state": seed,
        "n_jobs": -1,
        "verbose": -1,
    }

    if objective == "classify":
        y_col, eval_metric = "label", "auc"
        make = lambda n: lgb.LGBMClassifier(
            n_estimators=n, objective="binary", **params
        )
        predict = lambda m, X: m.predict_proba(X)[:, 1]
        can_early = fit_part[y_col].nunique() > 1 and val_part[y_col].nunique() > 1
    elif objective == "regress":
        y_col, eval_metric = "net_bps", "l1"
        make = lambda n: lgb.LGBMRegressor(
            n_estimators=n, objective="regression_l1", **params
        )
        predict = lambda m, X: m.predict(X)
        can_early = True
    else:
        raise ValueError(
            f"objective must be 'classify' or 'regress', got {objective!r}"
        )

    Xf, yf = fit_part[feature_cols], fit_part[y_col]
    Xv, yv = val_part[feature_cols], val_part[y_col]
    if can_early:
        model = make(3000)
        model.fit(
            Xf,
            yf,
            eval_set=[(Xv, yv)],
            eval_metric=eval_metric,
            callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
        )
        best_iter = model.best_iteration_
    else:  # single-class fold: can't early-stop, keep it small
        model = make(200)
        model.fit(tr[feature_cols], tr[y_col])
        best_iter = 200

    preds = predict(model, holdout[feature_cols])
    importances = sorted(
        zip(feature_cols, model.feature_importances_), key=lambda kv: -kv[1]
    )
    out = {
        "model": model,
        "preds": preds,
        "objective": objective,
        "best_iteration": int(best_iter),
        "importances": importances,
    }
    if objective == "classify":
        from sklearn.metrics import roc_auc_score

        out["holdout_auc"] = (
            float(roc_auc_score(holdout["label"], preds))
            if holdout["label"].nunique() > 1
            else float("nan")
        )
        out["val_auc"] = (
            float(roc_auc_score(yv, predict(model, Xv)))
            if yv.nunique() > 1
            else float("nan")
        )
    else:
        net = holdout["net_bps"].to_numpy(dtype=float)
        out["holdout_rank_ic"] = _rank_ic(preds, net)
        out["holdout_ic"] = (
            float(np.corrcoef(preds, net)[0, 1]) if len(net) > 2 else float("nan")
        )
        out["val_rank_ic"] = _rank_ic(predict(model, Xv), yv.to_numpy(dtype=float))
    return out


def compare_rankings(
    holdout: pd.DataFrame,
    preds: np.ndarray,
    fractions: tuple[float, ...] = DEFAULT_FRACTIONS,
) -> list[dict]:
    """At each trade-count budget, compare the incumbent |composite z| ranking to
    the meta P(profitable) ranking on the SAME holdout pool. Higher meta_total_net
    than z_total_net at matched n == the meta model is the better selector.
    """
    net = holdout["net_bps"].to_numpy(dtype=float)
    absz = holdout["strength"].to_numpy(dtype=float)
    absz = np.abs(absz)
    n = len(holdout)
    z_order = np.argsort(-absz)
    p_order = np.argsort(-preds)

    rows: list[dict] = []
    for fr in fractions:
        k = max(1, round(n * fr))
        zi, pi = z_order[:k], p_order[:k]
        rows.append(
            {
                "frac": fr,
                "n": k,
                "z_avg_net": float(net[zi].mean()),
                "z_total_net": float(net[zi].sum()),
                "z_hit": float((net[zi] > 0).mean()),
                "meta_avg_net": float(net[pi].mean()),
                "meta_total_net": float(net[pi].sum()),
                "meta_hit": float((net[pi] > 0).mean()),
            }
        )
    return rows


def natural_operating_point(
    holdout: pd.DataFrame, preds: np.ndarray, cut: float = 0.5
) -> dict:
    """The meta model's own cut: take trades with pred >= cut (0.5 for classify =
    P(profit); 0.0 for regress = predicted net > 0), vs taking the whole pool
    (primary-only). No |z| tuning involved."""
    net = holdout["net_bps"].to_numpy(dtype=float)
    take = preds >= cut
    k = int(take.sum())
    return {
        "pool_n": len(holdout),
        "pool_avg_net": float(net.mean()),
        "pool_total_net": float(net.sum()),
        "meta_n": k,
        "meta_avg_net": float(net[take].mean()) if k else float("nan"),
        "meta_total_net": float(net[take].sum()) if k else 0.0,
        "meta_hit": float((net[take] > 0).mean()) if k else float("nan"),
    }
