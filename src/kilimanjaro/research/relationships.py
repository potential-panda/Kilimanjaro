"""Bounded descriptive relationships; every fitted choice uses training only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .stages import clean, label_panel


def correlation(x, y, *, rank=False):
    pair = pd.DataFrame({"x": x, "y": y}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 20 or pair.x.nunique() < 2 or pair.y.nunique() < 2:
        return None
    return float(pair.x.corr(pair.y, method="spearman" if rank else "pearson"))


def panel_relationships(train, validation, names, horizon):
    """Return observable effects, redundancy and train-fitted residual checks."""
    names = names[:32]
    pairwise = []
    clusters = []
    for name in names:
        group = next(
            (
                g
                for g in clusters
                if abs(correlation(train[name], train[g[0]]) or 0) >= 0.9
            ),
            None,
        )
        if group is None:
            clusters.append([name])
        else:
            group.append(name)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            pairwise.append(
                {
                    "kind": "feature_feature",
                    "left": left,
                    "right": right,
                    "train": correlation(train[left], train[right]),
                    "validation": correlation(validation[left], validation[right]),
                }
            )
    baseline = max(names, key=lambda n: abs(correlation(train[n], train.fwd_ret) or 0))
    rows = []
    for name in names:
        quantiles = train[name].dropna().quantile([0.2, 0.8]).to_numpy()
        fit = train[[name, baseline]].dropna() if name != baseline else None
        beta = None
        if fit is not None and len(fit) >= 20:
            beta = np.linalg.lstsq(
                np.column_stack([np.ones(len(fit)), fit[baseline]]),
                fit[name],
                rcond=None,
            )[0]
        for role, panel in (("train", train), ("validation", validation)):
            row = {
                "kind": "feature_return",
                "feature": name,
                "horizon": horizon,
                "role": role,
                "pearson": correlation(panel[name], panel.fwd_ret),
                "rank_ic": correlation(panel[name], panel.fwd_ret, rank=True),
                "n": int(panel[[name, "fwd_ret"]].dropna().shape[0]),
                "coverage": float(panel[name].notna().mean()) if len(panel) else 0,
                "baseline": baseline,
                "incremental_ic": None,
                "uncertainty": None,
                "inference": "descriptive; selected development data",
            }
            if len(quantiles) == 2:
                low = panel.loc[panel[name] <= quantiles[0], "fwd_ret"]
                high = panel.loc[panel[name] >= quantiles[1], "fwd_ret"]
                row["quantile_effect_bps"] = float(high.mean() - low.mean())
                row["train_quantile_cutoffs"] = quantiles.tolist()
            if beta is not None:
                row["incremental_ic"] = correlation(
                    panel[name] - beta[0] - beta[1] * panel[baseline], panel.fwd_ret
                )
            if len(panel) >= 40:
                row["shifted_label_ic"] = correlation(
                    panel[name].reset_index(drop=True),
                    pd.Series(np.roll(panel.fwd_ret.to_numpy(), len(panel) // 2)),
                )
            row["time_blocks"] = [
                correlation(panel.iloc[index][name], panel.iloc[index].fwd_ret)
                for index in np.array_split(np.arange(len(panel)), 4)
            ]
            rows.append(row)
            # The state is available at the decision; its cutoff was fit on train.
            cutoff = float(train.state.quantile(0.5)) if "state" in train else None
            if cutoff is not None:
                for regime, mask in (
                    ("low", panel.state <= cutoff),
                    ("high", panel.state > cutoff),
                ):
                    rows.append(
                        {
                            "kind": "conditional_return",
                            "feature": name,
                            "horizon": horizon,
                            "role": role,
                            "state": "past_60s_absolute_return",
                            "cutoff": cutoff,
                            "regime": regime,
                            "n": int(mask.sum()),
                            "pearson": correlation(
                                panel.loc[mask, name], panel.loc[mask, "fwd_ret"]
                            ),
                            "inference": "descriptive",
                        }
                    )
    return clean(
        {"relationships": rows + pairwise, "clusters": clusters, "baseline": baseline}
    )


def analyze(spec, frame, prices):
    from kilimanjaro.return_calc.fixed_horizon import _nonoverlap_indices

    frame = frame.copy()
    mid = frame[f"{spec['target']}_mid"]
    frame["state"] = (
        mid.pct_change(fill_method=None).abs().rolling(60, min_periods=60).mean()
    )
    output = []
    for horizon in spec["horizons"]:
        panels = []
        for role in ("train", "validation"):
            panel = label_panel(
                frame,
                prices,
                horizon,
                spec["windows"][role],
                spec["execution"]["max_quote_delay_seconds"],
            )
            panels.append(panel.iloc[_nonoverlap_indices(panel.ts.to_numpy(), horizon)])
        output.append(
            {
                "horizon": horizon,
                **panel_relationships(
                    *panels, [f"F{i}" for i in range(len(spec["features"]))], horizon
                ),
            }
        )
    return {"schema_version": 1, "data_role": "development", "horizons": output}
