"""Train/holdout grid evaluation with rank-stability selection.

This is the framework's anti-overfitting layer, and the default way to run a
grid. Lesson learned the hard way (2026-07-13 flip grid): selecting on the
same window you report on manufactured 110 ETH "winners" of which 0 survived
a held-out four months. With hundreds of correlated combos, a p<=0.05 filter
alone passes ~5% by luck every time.

run_grid_split() therefore:
  - runs ALL combos on the train window and again on the holdout window;
  - merges the two result sets per combo into one row with train_*/holdout_*
    columns;
  - ranks combos within each window by `rank_metric` (among ok rows with
    n >= min_n) and computes each combo's percentile rank;
  - marks a combo `stable` only if it is in the top `top_quantile` of BOTH
    windows and has positive avg_net in both.

Rank stability replaces p-value filtering as the selection criterion: a real
edge should be near the top on data it has never seen, not merely significant
on the data that selected it. p-values (including the block-bootstrap one)
remain in the output as evidence, not as the gate.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable

from kilimanjaro.framework.gate import Gate
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.framework.runner import run_grid
from kilimanjaro.framework.schedule import ScheduleFilter

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

# Stat fields that get train_/holdout_ prefixes in merged rows. Everything
# else in a row (signal/return-calc params, schedule, status) is combo
# identity, identical across windows.
_STAT_FIELDS = [
    "status", "n_trades", "n",
    "avg_gross", "avg_net", "total_net_bps",
    "hit_rate", "hit_rate_net", "std_bps", "sharpe_per_trade",
    "t_stat", "p_value_1sided", "p_value_bootstrap",
    "skewness", "ic",
    "avg_hold_s", "median_hold_s", "median_gross", "p25_bps", "p75_bps",
    "n_dd_blocked", "n_dd_triggers",
]


def split_windows(start: str, split: str, end: str) -> tuple[str, str, str, str]:
    """Return (train_start, train_end, holdout_start, holdout_end).

    The split date belongs to the holdout window; the train window ends the
    day before, so the two windows never overlap (scan dates are inclusive).
    """
    d_start = datetime.date.fromisoformat(start)
    d_split = datetime.date.fromisoformat(split)
    d_end = datetime.date.fromisoformat(end)
    if not d_start < d_split <= d_end:
        raise ValueError(
            f"split must satisfy start < split <= end, got {start} < {split} <= {end}"
        )
    train_end = (d_split - datetime.timedelta(days=1)).isoformat()
    return start, train_end, split, end


def default_split(start: str, end: str, train_fraction: float = 2 / 3) -> str:
    """Split date at `train_fraction` of the range (default: last third held out)."""
    d_start = datetime.date.fromisoformat(start)
    d_end = datetime.date.fromisoformat(end)
    days = (d_end - d_start).days
    if days < 3:
        raise ValueError(f"range too short to split: {start}..{end}")
    return (d_start + datetime.timedelta(days=max(1, round(days * train_fraction)))).isoformat()


def _rank_pcts(rows: list[dict], prefix: str, rank_metric: str, min_n: int) -> None:
    """Add {prefix}rank_pct in-place: percentile of rank_metric among ranked
    rows (1.0 = best). Rows that are not ok or have n < min_n get NaN."""
    def _metric(r: dict) -> float:
        v = r.get(f"{prefix}{rank_metric}")
        return v if isinstance(v, (int, float)) else float("nan")

    ranked = [
        r for r in rows
        if r.get(f"{prefix}status") == "ok"
        and (r.get(f"{prefix}n") or 0) >= min_n
        and _metric(r) == _metric(r)  # not NaN
    ]
    m = len(ranked)
    ranked.sort(key=_metric)
    for i, r in enumerate(ranked):
        r[f"{prefix}rank_pct"] = (i + 1) / m
    for r in rows:
        r.setdefault(f"{prefix}rank_pct", float("nan"))


def run_grid_split(
    combos: list[tuple],
    target: MarketConfig,
    start_date: str,
    split_date: str,
    end_date: str,
    costs_bps: float = 1.0,
    schedule: ScheduleFilter | None = None,
    gates: list[Gate] | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    rank_metric: str = "t_stat",
    top_quantile: float = 0.10,
    min_n: int = 30,
    on_data_loaded: Callable[[tuple, Any], None] | None = None,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """Run all combos on train and holdout windows; return merged, ranked rows.

    Parameters beyond run_grid's:
    rank_metric : str
        Stat used for within-window ranking (on merged prefix-less name).
    top_quantile : float
        A combo is `stable` if its rank_pct >= 1 - top_quantile in BOTH
        windows and avg_net > 0 in both.
    min_n : int
        Rows with fewer valid trades are excluded from ranking (can never
        be stable) — kills the tiny-n significance mirage.
    on_data_loaded : optional
        Forwarded to the TRAIN pass only (side outputs like age histograms
        should be computed once).

    Returns
    -------
    dict with:
        rows       merged rows, one per combo: params + train_* + holdout_*
                   + train_rank_pct + holdout_rank_pct + stable
        stable     the stable subset, best holdout rank first
        windows    {"train": [start, end], "holdout": [start, end]}
        params     reproducibility block (rank_metric, top_quantile, min_n,
                   n_combos, costs_bps, gate params)
    """
    train_start, train_end, holdout_start, holdout_end = split_windows(
        start_date, split_date, end_date
    )

    def _pass(label: str, w_start: str, w_end: str, data_hook) -> list[dict]:
        def _progress(row: dict) -> None:
            if on_result:
                on_result({**row, "window": label})
        return run_grid(
            combos, target, w_start, w_end,
            costs_bps=costs_bps, schedule=schedule, gates=gates,
            data_root=data_root,
            on_data_loaded=data_hook, on_result=_progress,
        )

    train_rows = _pass("train", train_start, train_end, on_data_loaded)
    holdout_rows = _pass("holdout", holdout_start, holdout_end, None)

    merged: list[dict] = []
    for tr, ho in zip(train_rows, holdout_rows):
        assert tr["_combo_id"] == ho["_combo_id"]
        row = {
            k: v for k, v in tr.items()
            if k not in _STAT_FIELDS and k not in ("_combo_id",)
        }
        for f in _STAT_FIELDS:
            if f in tr:
                row[f"train_{f}"] = tr[f]
            if f in ho:
                row[f"holdout_{f}"] = ho[f]
        merged.append(row)

    _rank_pcts(merged, "train_", rank_metric, min_n)
    _rank_pcts(merged, "holdout_", rank_metric, min_n)

    cutoff = 1.0 - top_quantile
    for r in merged:
        r["stable"] = bool(
            r["train_rank_pct"] > cutoff
            and r["holdout_rank_pct"] > cutoff
            and (r.get("train_avg_net") or 0) > 0
            and (r.get("holdout_avg_net") or 0) > 0
        )

    stable = sorted(
        (r for r in merged if r["stable"]),
        key=lambda r: -r["holdout_rank_pct"],
    )

    return {
        "rows": merged,
        "stable": stable,
        "windows": {
            "train": [train_start, train_end],
            "holdout": [holdout_start, holdout_end],
        },
        "params": {
            "rank_metric": rank_metric,
            "top_quantile": top_quantile,
            "min_n": min_n,
            "n_combos": len(combos),
            "costs_bps": costs_bps,
            "gates": [g.params for g in gates or []],
        },
    }
