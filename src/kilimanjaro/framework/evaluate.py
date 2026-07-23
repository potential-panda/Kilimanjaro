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

Cost policy (framework/costs.py): every combo's returns are bid/ask (spread
already paid) and net of the flat round-trip latency slippage
(ROUND_TRIP_COST_BPS, the default `costs_bps` here). We never estimate or add a
separate spread cost — that would double-count.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable

from kilimanjaro.framework.costs import ROUND_TRIP_COST_BPS
from kilimanjaro.framework.gate import Gate

# Path/drawdown metrics (performance.py) differ per window, so they must be
# split into train_/holdout_ like the trade stats — not treated as combo
# identity. Appended (deduped) so the source of truth stays performance.py.
from kilimanjaro.framework.performance import JPY_FIELDS as _JPY_FIELDS
from kilimanjaro.framework.performance import PATH_FIELDS as _PATH_FIELDS
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.framework.runner import run_grid
from kilimanjaro.framework.schedule import ScheduleFilter

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

# Stat fields that get train_/holdout_ prefixes in merged rows. Everything
# else in a row (signal/return-calc params, schedule, status) is combo
# identity, identical across windows.
_STAT_FIELDS = [
    "status",
    "n_trades",
    "n",
    "n_episodes",
    "n_eff",
    "avg_gross",
    "avg_net",
    "total_net_bps",
    "hit_rate",
    "hit_rate_net",
    "std_bps",
    "sharpe_per_trade",
    "t_stat",
    "t_stat_eff",
    "p_value_1sided",
    "p_value_bootstrap",
    "skewness",
    "ic",
    "avg_hold_s",
    "median_hold_s",
    "median_gross",
    "p25_bps",
    "p75_bps",
    "n_dd_blocked",
    "n_dd_triggers",
]

_STAT_FIELDS += [f for f in (*_PATH_FIELDS, *_JPY_FIELDS) if f not in _STAT_FIELDS]


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
    return (
        d_start + datetime.timedelta(days=max(1, round(days * train_fraction)))
    ).isoformat()


def _resolve_rank_metric(rows: list[dict], prefix: str, rank_metric: str) -> str:
    """The requested metric, or `t_stat` when nothing carries it.

    `t_stat_eff` needs an episode id, which only episode-aware signals emit —
    flip and OBI grids have none. Without a fallback those grids would rank
    nothing and silently report zero survivors, so fall back wholesale (never
    per row: ranking half a window by one metric and half by another is
    meaningless).
    """
    if rank_metric == "t_stat":
        return rank_metric
    key = f"{prefix}{rank_metric}"
    if any(isinstance(r.get(key), (int, float)) and r.get(key) == r.get(key) for r in rows):
        return rank_metric
    return "t_stat"


def _rank_pcts(rows: list[dict], prefix: str, rank_metric: str, min_n: int) -> None:
    """Add {prefix}rank_pct in-place: percentile of rank_metric among ranked
    rows (1.0 = best). Rows that are not ok or have n < min_n get NaN."""
    rank_metric = _resolve_rank_metric(rows, prefix, rank_metric)

    def _metric(r: dict) -> float:
        v = r.get(f"{prefix}{rank_metric}")
        return v if isinstance(v, (int, float)) else float("nan")

    ranked = [
        r
        for r in rows
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
    costs_bps: float = ROUND_TRIP_COST_BPS,
    schedule: ScheduleFilter | None = None,
    gates: list[Gate] | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    rank_metric: str = "t_stat_eff",
    top_quantile: float = 0.10,
    min_n: int = 30,
    min_month_n: int = 10,
    mt_alpha: float = 0.10,
    on_data_loaded: Callable[[tuple, Any], None] | None = None,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """Run all combos on train and holdout windows; return merged, ranked,
    classified rows.

    Parameters beyond run_grid's:
    rank_metric : str
        Stat used for within-window ranking (on merged prefix-less name).
    top_quantile : float
        A combo is `stable` if its rank_pct >= 1 - top_quantile in BOTH
        windows and avg_net > 0 in both.
    min_n : int
        Rows with fewer valid trades are excluded from ranking (can never
        be stable) — kills the tiny-n significance mirage.
    min_month_n : int
        Months with fewer trades are ignored in the monthly consistency
        profile (pos_month_share).
    on_data_loaded : optional
        Forwarded to the TRAIN pass only (side outputs like age histograms
        should be computed once).

    Returns
    -------
    dict with:
        rows           merged rows, one per combo: params + train_* +
                       holdout_* + rank pcts + pos_month_shares +
                       train_plateau + stable + verdict
                       (stable/decayed/regime_dependent/noise/no_data)
        stable         the stable subset, best holdout rank first
        monthly_rows   long-format per-combo per-month profile
                       (identity params + window + month + n + avg_net)
        verdict_counts verdict → count
        windows        {"train": [start, end], "holdout": [start, end]}
        params         reproducibility block
    """
    train_start, train_end, holdout_start, holdout_end = split_windows(
        start_date, split_date, end_date
    )

    def _pass(label: str, w_start: str, w_end: str, data_hook) -> list[dict]:
        def _progress(row: dict) -> None:
            if on_result:
                on_result({**row, "window": label})

        return run_grid(
            combos,
            target,
            w_start,
            w_end,
            costs_bps=costs_bps,
            schedule=schedule,
            gates=gates,
            data_root=data_root,
            on_data_loaded=data_hook,
            on_result=_progress,
        )

    train_rows = _pass("train", train_start, train_end, on_data_loaded)
    holdout_rows = _pass("holdout", holdout_start, holdout_end, None)

    merged: list[dict] = []
    monthly_rows: list[dict] = []
    for tr, ho in zip(train_rows, holdout_rows):
        assert tr["_combo_id"] == ho["_combo_id"]
        row = {
            k: v
            for k, v in tr.items()
            if k not in _STAT_FIELDS and not k.startswith("_")
        }
        for f in _STAT_FIELDS:
            if f in tr:
                row[f"train_{f}"] = tr[f]
            if f in ho:
                row[f"holdout_{f}"] = ho[f]

        identity = {
            k: v
            for k, v in row.items()
            if not k.startswith(("train_", "holdout_")) and k != "n_combos_tested"
        }
        for window, src in (("train", tr), ("holdout", ho)):
            for month, (n, avg_net) in sorted(src.get("_monthly", {}).items()):
                monthly_rows.append(
                    {
                        **identity,
                        "window": window,
                        "month": month,
                        "n": n,
                        "avg_net": round(avg_net, 4),
                    }
                )
        row["train_pos_month_share"] = _pos_month_share(tr.get("_monthly"), min_month_n)
        row["holdout_pos_month_share"] = _pos_month_share(
            ho.get("_monthly"), min_month_n
        )
        merged.append(row)

    _rank_pcts(merged, "train_", rank_metric, min_n)
    _rank_pcts(merged, "holdout_", rank_metric, min_n)
    _add_plateau(merged)

    cutoff = 1.0 - top_quantile
    for r in merged:
        r["stable"] = bool(
            r["train_rank_pct"] > cutoff
            and r["holdout_rank_pct"] > cutoff
            and (r.get("train_avg_net") or 0) > 0
            and (r.get("holdout_avg_net") or 0) > 0
        )
        r["verdict"] = _classify(r, cutoff)

    # Multiple-testing control (Gap 2): with many combos, some clear rank
    # stability by luck. Benjamini-Hochberg on the holdout bootstrap p-values
    # controls the false-discovery rate across all combos tested, so the bar
    # tightens automatically as the grid grows. `stable` stays the rank-based
    # consistency flag; `mt_significant` is the FDR-survivor flag; a trustworthy
    # survivor is both (the driver's policy requires both).
    mt_flags = _bh_significant(
        [r.get("holdout_p_value_bootstrap") for r in merged], mt_alpha
    )
    for r, sig in zip(merged, mt_flags):
        r["mt_significant"] = bool(sig)
    n_mt_significant = sum(mt_flags)

    stable = sorted(
        (r for r in merged if r["stable"]),
        key=lambda r: -r["holdout_rank_pct"],
    )
    verdict_counts: dict[str, int] = {}
    for r in merged:
        verdict_counts[r["verdict"]] = verdict_counts.get(r["verdict"], 0) + 1

    return {
        "rows": merged,
        "stable": stable,
        "monthly_rows": monthly_rows,
        "verdict_counts": verdict_counts,
        "windows": {
            "train": [train_start, train_end],
            "holdout": [holdout_start, holdout_end],
        },
        "params": {
            "rank_metric": rank_metric,
            "top_quantile": top_quantile,
            "min_n": min_n,
            "min_month_n": min_month_n,
            "mt_alpha": mt_alpha,
            "n_mt_significant": n_mt_significant,
            "n_combos": len(combos),
            "costs_bps": costs_bps,
            "gates": [g.params for g in gates or []],
        },
    }


def _bh_significant(pvals: list[float | None], alpha: float) -> list[bool]:
    """Benjamini-Hochberg FDR: which one-sided p-values are discoveries at `alpha`.

    Controls the expected false-discovery rate across the whole family of combos.
    NaN/None p-values (too few trades to test) are excluded from the family and
    never flagged. Returns a boolean per input position.
    """
    import math

    valid = [
        i
        for i, p in enumerate(pvals)
        if p is not None and not (isinstance(p, float) and math.isnan(p))
    ]
    flags = [False] * len(pvals)
    m = len(valid)
    if m == 0:
        return flags
    order = sorted(valid, key=lambda i: pvals[i])
    thresh = None
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= (rank / m) * alpha:
            thresh = pvals[i]  # largest p that still passes the BH line
    if thresh is not None:
        for i in valid:
            if pvals[i] <= thresh:
                flags[i] = True
    return flags


def _pos_month_share(monthly: dict | None, min_month_n: int) -> float:
    """Fraction of months (with at least min_month_n trades) that are net
    positive. NaN when no month qualifies."""
    if not monthly:
        return float("nan")
    qualified = [(n, avg) for n, avg in monthly.values() if n >= min_month_n]
    if not qualified:
        return float("nan")
    return sum(1 for _, avg in qualified if avg > 0) / len(qualified)


def _add_plateau(rows: list[dict]) -> None:
    """Add train_plateau in-place: the share of a combo's grid NEIGHBORS
    (rows identical in every identity param except one numeric one, adjacent
    in that param's sorted unique values) with train_avg_net > 0.

    Real edges live on parameter plateaus; an isolated winner whose
    neighbors disagree is selection noise. NaN when a combo has no
    neighbors (single-valued grid).
    """
    id_keys = [
        k
        for k in rows[0]
        if not k.startswith(("train_", "holdout_"))
        and k not in ("n_combos_tested", "stable", "verdict")
    ]
    numeric_keys = [
        k
        for k in id_keys
        if len(
            {
                r[k]
                for r in rows
                if isinstance(r[k], (int, float)) and not isinstance(r[k], bool)
            }
        )
        > 1
    ]

    neighbors: dict[int, list[int]] = {i: [] for i in range(len(rows))}
    for key in numeric_keys:
        groups: dict[tuple, list[int]] = {}
        for i, r in enumerate(rows):
            if not isinstance(r[key], (int, float)) or isinstance(r[key], bool):
                continue
            others = tuple((k, repr(r[k])) for k in id_keys if k != key)
            groups.setdefault(others, []).append(i)
        for idxs in groups.values():
            idxs.sort(key=lambda i: rows[i][key])
            for a, b in zip(idxs, idxs[1:]):
                if rows[a][key] != rows[b][key]:
                    neighbors[a].append(b)
                    neighbors[b].append(a)

    for i, r in enumerate(rows):
        nb = [
            rows[j]
            for j in neighbors[i]
            if isinstance(rows[j].get("train_avg_net"), (int, float))
        ]
        if not nb:
            r["train_plateau"] = float("nan")
        else:
            r["train_plateau"] = sum(1 for x in nb if x["train_avg_net"] > 0) / len(nb)


def _classify(r: dict, cutoff: float) -> str:
    """Verdict taxonomy — a rejection is not one thing:

    stable            top of both windows (the promotion candidates)
    decayed           genuinely worked in train (top rank + consistent
                      months + parameter plateau) then uniformly stopped
                      in the holdout — regime change, worth monitoring
                      for recovery, and "what turned it off" is itself a
                      hypothesis
    regime_dependent  strong overall but the monthly profile alternates —
                      look for the conditioning variable
    noise             everything else, including isolated winners whose
                      parameter neighbors disagree (selection luck)
    """
    if r.get("train_status") != "ok" or r.get("holdout_status") != "ok":
        return "no_data"
    if r["stable"]:
        return "stable"

    train_top = r["train_rank_pct"] > cutoff
    pos_train = r["train_pos_month_share"]
    pos_holdout = r["holdout_pos_month_share"]
    plateau = r["train_plateau"]
    on_plateau = not (plateau == plateau) or plateau >= 0.5  # NaN → benefit of doubt

    if train_top and pos_train == pos_train and pos_train >= 0.7 and on_plateau:
        if pos_holdout == pos_holdout and pos_holdout <= 1 / 3:
            return "decayed"
        return "regime_dependent"
    if train_top and pos_train == pos_train and 0.4 <= pos_train < 0.7:
        return "regime_dependent"
    return "noise"
