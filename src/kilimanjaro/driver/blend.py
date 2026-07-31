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
    series: dict[str, pd.Series] = {}
    ts_ref: pd.Series | None = None
    for f in factors:
        frame = f.markets_dict  # load per factor (frame cache shares loads)
        from kilimanjaro.data.feature_frame import load_feature_frame

        fr = load_feature_frame(frame, f.columns, start, end, data_root=data_root)
        val = eval_expr(f.expr, fr).to_numpy(dtype=float)
        s = pd.Series(val, index=fr["ts"].to_numpy(), name=f.name)
        series[f.name] = s
        ts_ref = s.index if ts_ref is None else ts_ref

    if not series:
        return pd.DataFrame()

    panel = pd.concat(series.values(), axis=1, join="inner")
    panel = panel.dropna(how="any")
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


def _ic(x: np.ndarray, r: np.ndarray) -> float:
    """Pearson corr, NaN-safe; 0.0 when a series is constant or too short."""
    if len(x) < 3:
        return 0.0
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
        mu, sd = float(np.mean(x_tr)), float(np.std(x_tr))
        sd = sd if sd > 0 else 1.0
        zt = (x_tr - mu) / sd
        zh = (x_ho - mu) / sd  # train stats on holdout: no peeking
        ic_tr = _ic(zt, r_tr)
        ic_ho = _ic(zh, r_ho)
        z_tr[name], z_ho[name] = zt, zh
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
