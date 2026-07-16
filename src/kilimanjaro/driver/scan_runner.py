"""Turn one scan-spec (a grid_scan_expr run config) into a completed scan.

This is the canonical home for spec -> combos -> run_grid_split -> output
folder. tools/grid_scan_expr.py is a thin CLI over the same functions; the
driver calls them directly.

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
    ScheduleFilter,
    default_split,
    run_grid_split,
    write_csv,
)
from kilimanjaro.framework.costs import estimate_round_trip_cost_bps
from kilimanjaro.return_calc.episode import EpisodeReturn
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn
from kilimanjaro.signal.expression import ExpressionSignal

CSV_FIELDS = [
    "expr", "threshold", "min_age", "max_age", "invert", "horizon", "max_hold_s",
    "train_n", "train_avg_gross", "train_avg_net", "train_hit_rate", "train_ic",
    "train_avg_hold_s", "holdout_avg_hold_s",
    "train_t_stat", "train_p_value_bootstrap", "train_total_net_bps", "train_rank_pct",
    "holdout_n", "holdout_avg_gross", "holdout_avg_net", "holdout_hit_rate", "holdout_ic",
    "holdout_t_stat", "holdout_p_value_bootstrap", "holdout_total_net_bps", "holdout_rank_pct",
    "train_pos_month_share", "holdout_pos_month_share", "train_plateau",
    "verdict", "stable", "mt_significant", "n_combos_tested",
    "train_status", "holdout_status",
]


def _return_calcs(spec: dict) -> list:
    kind = spec.get("return_calc", "fixed_horizon")
    if kind == "episode":
        return [EpisodeReturn(max_hold_seconds=h) for h in spec.get("max_holds", [None])]
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
                for (min_age, max_age) in age_windows:
                    sig = ExpressionSignal.create(
                        markets=markets, expr=e["expr"], threshold=threshold,
                        min_age=min_age, max_age=max_age, invert=invert,
                        label=e.get("label"),
                    )
                    for rc in return_calcs:
                        combos.append((sig, rc))
    return combos


def _round(r: dict) -> dict:
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()}


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
    """Run the whole grid for one spec, write the output folder, return
    (res, out_dir, scan_id). res is run_grid_split's dict.
    """
    combos = build_combos(spec)
    t = spec["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    schedule = ScheduleFilter(block_gmo_maintenance=(t["exchange"].upper() == "GMO"))
    split = split or default_split(start, end)

    # Gap 1: charge the real round-trip spread of the target market so the
    # mid-to-mid screen cannot pass sub-spread mirages. A spec may override
    # (e.g. costs_bps: 0 for a pure signal-existence study).
    costs_bps = spec.get("costs_bps")
    if costs_bps is None:
        costs_bps = estimate_round_trip_cost_bps(target, start, end)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{scan_name}_{start}_{end}_{ts}"
    out_dir = results_root / scan_id

    res = run_grid_split(
        combos, target, start, split, end,
        costs_bps=costs_bps,
        schedule=schedule, rank_metric=rank_metric,
        top_quantile=top_quantile, min_n=min_n, on_result=on_result,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "params.json").write_text(json.dumps({
        "scan_id": scan_id, "spec": spec, "start": start, "end": end,
        "windows": res["windows"], **res["params"],
    }, indent=2, default=str))

    rows = [_round(r) for r in res["rows"]]
    rows.sort(key=lambda r: -(r.get("holdout_rank_pct") or 0))
    stable = [r for r in rows if r["stable"]]
    write_csv(rows, out_dir / "output.csv", CSV_FIELDS)
    write_csv(stable, out_dir / "output_stable.csv", CSV_FIELDS)
    monthly = res["monthly_rows"]
    if monthly:
        write_csv(monthly, out_dir / "output_monthly.csv", list(monthly[0].keys()))

    return res, out_dir, scan_id
