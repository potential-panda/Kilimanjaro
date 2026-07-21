"""Scan core: combos -> run_grid_split -> output folder.

Canonical home for the run/write pipeline shared by every scan flavour.
`run_grid_scan` is the generic core; `run_spec` is the expression-spec flavour
on top of it. The flip and OBI flavours live in driver/flip_grid.py and
driver/obi_grid.py, and cli.py `grid {flip,expr,obi}` is the thin CLI over all
three. The Stage-A loop calls run_spec directly.

A scan-spec is the YAML consumed by the expression funnel:

    hypothesis_id: hyp-YYYYMMDD-NNN
    return_calc: fixed_horizon | episode      # default fixed_horizon
    markets: {a: {exchange, symbol, execution_size}, ...}
    target:  {exchange, symbol, execution_size}
    expressions: [{label, expr, invert: [..]}, ...]
    thresholds: [..]
    age_windows: [[min,max], ..]              # default [[null, null]]
    horizons: [..]                            # fixed_horizon
    max_holds: [..]                           # episode
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from kilimanjaro.framework import (
    MarketConfig,
    default_split,
    run_grid_split,
    write_csv,
)
from kilimanjaro.framework.costs import ROUND_TRIP_COST_BPS
from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
from kilimanjaro.return_calc.episode import EpisodeReturn
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn
from kilimanjaro.signal.expression import ExpressionSignal

CSV_FIELDS = [
    "expr",
    "threshold",
    "min_age",
    "max_age",
    "invert",
    "horizon",
    "max_hold_s",
    "train_n",
    "train_avg_gross",
    "train_avg_net",
    "train_hit_rate",
    "train_ic",
    "train_avg_hold_s",
    "holdout_avg_hold_s",
    "train_t_stat",
    "train_p_value_bootstrap",
    "train_total_net_bps",
    "train_rank_pct",
    "holdout_n",
    "holdout_avg_gross",
    "holdout_avg_net",
    "holdout_hit_rate",
    "holdout_ic",
    "holdout_t_stat",
    "holdout_p_value_bootstrap",
    "holdout_total_net_bps",
    "holdout_rank_pct",
    "train_pos_month_share",
    "holdout_pos_month_share",
    "train_plateau",
    "verdict",
    "stable",
    "mt_significant",
    "n_combos_tested",
    "train_status",
    "holdout_status",
]


def _return_calcs(spec: dict) -> list:
    kind = spec.get("return_calc", "fixed_horizon")
    if kind == "episode":
        return [
            EpisodeReturn(max_hold_seconds=h) for h in spec.get("max_holds", [None])
        ]
    if kind == "fixed_horizon":
        return [FixedHorizonReturn(horizon_seconds=h) for h in spec["horizons"]]
    raise ValueError(f"unknown return_calc {kind!r} (want fixed_horizon or episode)")


def build_combos(spec: dict) -> list[tuple]:
    """Expand a scan-spec into (SignalDef, ReturnCalc) combos for run_grid_split."""
    markets = spec["markets"]
    thresholds = spec["thresholds"]
    age_windows = spec.get("age_windows", [[None, None]])
    return_calcs = _return_calcs(spec)

    combos: list[tuple] = []
    for e in spec["expressions"]:
        for invert in e.get("invert", [False]):
            for threshold in thresholds:
                for min_age, max_age in age_windows:
                    sig = ExpressionSignal.create(
                        markets=markets,
                        expr=e["expr"],
                        threshold=threshold,
                        min_age=min_age,
                        max_age=max_age,
                        invert=invert,
                        label=e.get("label"),
                    )
                    for rc in return_calcs:
                        combos.append((sig, rc))
    return combos


def _round(r: dict) -> dict:
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()}


def run_grid_scan(
    combos: list[tuple],
    target: MarketConfig,
    start: str,
    end: str,
    *,
    results_root: Path,
    scan_name: str,
    csv_fields: list[str],
    split: str | None = None,
    param_meta: dict | None = None,
    schedule=None,
    gates: list | None = None,
    costs_bps: float | None = None,
    rank_metric: str = "t_stat",
    top_quantile: float = 0.10,
    min_n: int = 30,
    on_result=None,
    on_data_loaded=None,
    row_transform=None,
    scan_id: str | None = None,
    out_dir: Path | None = None,
) -> tuple[dict, Path, str]:
    """THE shared scan core: run a combo grid through the train/holdout split and
    write the standard output folder (params.json, output.csv, output_stable.csv,
    output_monthly.csv). Every scan flavour (flip, expression, OBI) calls this;
    they differ only in how `combos` / `csv_fields` are built.

    row_transform: optional per-row map applied before writing (default `_round`;
    the OBI flavour passes a labeler that recovers depth/halflife). on_data_loaded
    is forwarded to run_grid_split (used by OBI for age histograms) — pass an
    explicit out_dir so such hooks can write into it. Returns (res, out_dir,
    scan_id); does NOT touch the ledger (the caller owns that).
    """
    if costs_bps is None:  # cost policy: bid/ask includes spread; add flat slippage
        costs_bps = ROUND_TRIP_COST_BPS
    split = split or default_split(start, end)
    if scan_id is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        scan_id = f"{scan_name}_{start}_{end}_{ts}"
    if out_dir is None:
        out_dir = results_root / scan_id
    out_dir.mkdir(parents=True, exist_ok=True)  # early: age-hist hooks write here

    res = run_grid_split(
        combos,
        target,
        start,
        split,
        end,
        costs_bps=costs_bps,
        schedule=schedule,
        gates=gates,
        rank_metric=rank_metric,
        top_quantile=top_quantile,
        min_n=min_n,
        on_data_loaded=on_data_loaded,
        on_result=on_result,
    )

    (out_dir / "params.json").write_text(
        json.dumps(
            {
                "scan_id": scan_id,
                "start": start,
                "end": end,
                "split": split,
                "windows": res["windows"],
                **(param_meta or {}),
                **res["params"],
            },
            indent=2,
            default=str,
        )
    )

    tf = row_transform or _round
    rows = [tf(r) for r in res["rows"]]
    rows.sort(key=lambda r: -(r.get("holdout_rank_pct") or 0))
    stable = [r for r in rows if r.get("stable")]
    write_csv(rows, out_dir / "output.csv", csv_fields)
    write_csv(stable, out_dir / "output_stable.csv", csv_fields)
    monthly = [tf(m) for m in res["monthly_rows"]]
    if monthly:
        write_csv(monthly, out_dir / "output_monthly.csv", list(monthly[0].keys()))
    return res, out_dir, scan_id


def run_spec(
    spec: dict,
    start: str,
    end: str,
    results_root: Path,
    split: str | None = None,
    rank_metric: str = "t_stat",
    top_quantile: float = 0.10,
    min_n: int = 30,
    scan_name: str = "scan",
    on_result=None,
) -> tuple[dict, Path, str]:
    """Expression-scan flavour: build combos from a spec, run through the core."""
    t = spec["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    # Unified schedule: orion is the source of truth (framework/orion_schedule).
    schedule = (
        OrionScheduleFilter(spec.get("schedule_version", "nofilter"))
        if t["exchange"].upper() == "GMO"
        else None
    )
    return run_grid_scan(
        build_combos(spec),
        target,
        start,
        end,
        results_root=results_root,
        scan_name=scan_name,
        csv_fields=CSV_FIELDS,
        split=split,
        param_meta={"spec": spec},
        schedule=schedule,
        costs_bps=spec.get("costs_bps"),
        rank_metric=rank_metric,
        top_quantile=top_quantile,
        min_n=min_n,
        on_result=on_result,
    )
