"""Exact-match comparison of one factor's per-second series: Kilimanjaro (offline,
Makalu parquet) vs Himalaya (online, InfluxDB). The calibration goal is pct_match ≈ 100%.

A "match" is agreement within `tol` on the overlapping seconds. tol is a small absolute
value on the factor (default 1e-4) — the two engines compute the same formula but in
different arithmetic (numpy vs Python floats), so bit-exact equality never holds; ~1e-4
agreement means the computations are truly identical. Set tol=0 for strict equality.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class CalibResult:
    name: str
    n_points: int  # overlapping seconds compared
    n_match: int  # seconds within tol
    pct_match: float  # n_match / n_points * 100
    corr: float  # Pearson corr over the overlap
    max_abs_diff: float
    tol: float
    n_offline_only: int  # seconds present offline but not online (and vice versa)
    n_online_only: int
    mismatches: list = field(
        default_factory=list
    )  # [(sec, offline, online, diff)] worst first

    def ok(self, target: float = 99.9) -> bool:
        return self.pct_match >= target

    def summary(self) -> str:
        s = (
            f"{self.name:<18} pct_match {self.pct_match:6.2f}%  "
            f"({self.n_match}/{self.n_points})  corr {self.corr:.5f}  "
            f"maxdiff {self.max_abs_diff:.3g}  tol {self.tol:g}"
        )
        if self.n_offline_only or self.n_online_only:
            s += f"  [coverage: offline_only {self.n_offline_only}, online_only {self.n_online_only}]"
        return s


def compare(
    name: str,
    offline: pd.Series,
    online: pd.Series,
    *,
    tol: float = 1e-4,
    top_mismatches: int = 10,
) -> CalibResult:
    """Align two per-second series (index = integer second) and report the match rate."""
    offline = offline[~offline.index.duplicated(keep="last")]
    online = online[~online.index.duplicated(keep="last")]
    off_idx, on_idx = set(offline.index), set(online.index)
    common = sorted(off_idx & on_idx)
    n_off_only, n_on_only = len(off_idx - on_idx), len(on_idx - off_idx)
    if not common:
        return CalibResult(
            name, 0, 0, 0.0, float("nan"), float("nan"), tol, n_off_only, n_on_only, []
        )
    o = offline.loc[common].to_numpy(dtype=float)
    h = online.loc[common].to_numpy(dtype=float)
    valid = np.isfinite(o) & np.isfinite(h)
    o, h, secs = o[valid], h[valid], np.array(common)[valid]
    n = len(o)
    diff = np.abs(o - h)
    match = diff <= tol
    corr = (
        float(np.corrcoef(o, h)[0, 1])
        if n > 1 and o.std() > 0 and h.std() > 0
        else float("nan")
    )
    order = np.argsort(-diff)[:top_mismatches]
    mism = [
        (int(secs[i]), float(o[i]), float(h[i]), float(diff[i]))
        for i in order
        if diff[i] > tol
    ]
    return CalibResult(
        name=name,
        n_points=n,
        n_match=int(match.sum()),
        pct_match=float(match.mean() * 100) if n else 0.0,
        corr=corr,
        max_abs_diff=float(diff.max()) if n else float("nan"),
        tol=tol,
        n_offline_only=n_off_only,
        n_online_only=n_on_only,
        mismatches=mism,
    )
