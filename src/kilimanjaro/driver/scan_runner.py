"""Scan core: combos -> run_grid_split -> output folder.

Canonical home for the run/write pipeline shared by every scan flavour.
`run_grid_scan` is the generic core; `run_spec` is the expression-spec flavour
on top of it. The flip flavour lives in driver/flip_grid.py, and cli.py
`grid {flip,horizon}` is the thin CLI over both. The scanner loop calls run_spec
directly.

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
from kilimanjaro.framework.gate import (
    DEFAULT_DD_COOLDOWN_SECONDS,
    DEFAULT_DD_THRESHOLD_BPS,
    DEFAULT_DD_WINDOW_SECONDS,
    RollingDrawdownGate,
    default_drawdown_gate,
)
from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
from kilimanjaro.return_calc.episode import EpisodeReturn
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn
from kilimanjaro.return_calc.trailing_stop import TrailingStopReturn
from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn
from kilimanjaro.return_calc.vol_triple_barrier import VolScaledTripleBarrierReturn
from kilimanjaro.signal.expression import ExpressionSignal

CSV_FIELDS = [
    "expr",
    "threshold",
    "min_age",
    "max_age",
    "invert",
    "horizon",
    "max_hold_s",
    "barrier_bps",
    "time_cap_s",
    "k",
    "vol_lookback_s",
    "cost_floor_bps",
    "train_n",
    "train_n_episodes",
    "train_n_eff",
    "train_avg_gross",
    "train_avg_net",
    "train_hit_rate",
    "train_ic",
    "train_avg_hold_s",
    "holdout_avg_hold_s",
    "train_t_stat",
    "train_t_stat_eff",
    "train_p_value_bootstrap",
    "train_total_net_bps",
    "train_rank_pct",
    "holdout_n",
    "holdout_n_episodes",
    "holdout_n_eff",
    "holdout_avg_gross",
    "holdout_avg_net",
    "holdout_hit_rate",
    "holdout_ic",
    "holdout_t_stat",
    "holdout_t_stat_eff",
    "holdout_p_value_bootstrap",
    "holdout_total_net_bps",
    "holdout_rank_pct",
    "train_pos_month_share",
    "holdout_pos_month_share",
    "train_plateau",
    "verdict",
    "stable",
    "mt_significant",
    # Rolling-drawdown gate effect (0 when the gate never triggered / is off).
    "train_n_dd_blocked",
    "train_n_dd_triggers",
    "holdout_n_dd_blocked",
    "holdout_n_dd_triggers",
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
    # Expression signals fire on the 1s composite grid, so the realistic taker fill is
    # the next available price after the bar closes: fill_delay_seconds=1.0 (default).
    # Certified flip mechanism fires on a tick and constructs its calcs directly (delay 0).
    fd = float(spec.get("fill_delay_seconds", 1.0))
    if kind == "triple_barrier":
        return [
            TripleBarrierReturn(barrier_bps=x, time_cap_seconds=t, fill_delay_seconds=fd)
            for x in spec["barriers"]
            for t in spec.get("time_caps", [300])
        ]
    if kind == "vol_triple_barrier":
        return [
            VolScaledTripleBarrierReturn(
                k=k,
                time_cap_seconds=t,
                vol_lookback_seconds=spec.get("vol_lookback_s", 1800),
                cost_floor_bps=spec.get("cost_floor_bps", 0.0),
            )
            for k in spec["ks"]
            for t in spec.get("time_caps", [300])
        ]
    if kind == "trailing_stop":  # aquila v3: trailing stop + flip exit, NO time cap
        return [
            TrailingStopReturn(barrier_bps=x, time_cap_seconds=None, fill_delay_seconds=fd)
            for x in spec["barriers"]
        ]
    if kind == "maker_fep":  # resting limit-order economics (fees baked in; mirrors qate)
        from kilimanjaro.return_calc.maker_fep import MakerFEPReturn
        m = spec.get("maker", {})
        return [MakerFEPReturn(
            offset_bps=m.get("offset_bps", 0.0), fill_window_s=m.get("fill_window_s", 30.0),
            hold_s=m.get("hold_s", 30.0), exit_window_s=m.get("exit_window_s", 60.0),
            maker_fee_bps=m.get("maker_fee_bps", 0.0), taker_fee_bps=m.get("taker_fee_bps", 5.0),
            long_only=m.get("long_only", False), price_unit=m.get("price_unit", 0.0))]
    raise ValueError(
        f"unknown return_calc {kind!r} (want fixed_horizon, episode, "
        "triple_barrier, vol_triple_barrier, trailing_stop, or maker_fep)"
    )


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


def gates_for_spec(spec: dict, return_calc: str | None = None) -> list:
    """Risk gates for a spec. The rolling-drawdown gate is ON by default — it is
    the live-trading risk rule (pause after an extreme loss), so the scan
    measures the signal under the same overlay live/Himalaya run under.

    It applies ONLY to non-overlapping trades. The gate sums return_bps over a
    trailing window; on the overlapping per-second fixed_horizon grid that sum
    double-counts (each loss is seen ~horizon times), the rolling total blows
    past the threshold almost immediately, and the gate blocks ~94% of the
    signal — meaningless. So it is skipped for fixed_horizon and applied for
    episode (one non-overlapping trade per episode), which is where the verdict
    is decided anyway. `return_calc` overrides the spec's own field, so the
    episode re-test can request the gate even though its screening spec still
    says fixed_horizon.

    Opt out or tune per spec via the `drawdown_gate` key:
      - omitted / true  -> the canonical default gate
      - false           -> no gate (raw, ungated signal)
      - {window_seconds, threshold_bps, cooldown_seconds} -> custom params
    """
    rc = return_calc or spec.get("return_calc", "fixed_horizon")
    if rc not in (
        "episode",
        "triple_barrier",
        "vol_triple_barrier",
        "trailing_stop",
    ):  # overlapping -> rolling-sum gate invalid
        return []
    g = spec.get("drawdown_gate", True)
    if g is False:
        return []
    if isinstance(g, dict):
        return [
            RollingDrawdownGate(
                window_seconds=g.get("window_seconds", DEFAULT_DD_WINDOW_SECONDS),
                threshold_bps=g.get("threshold_bps", DEFAULT_DD_THRESHOLD_BPS),
                cooldown_seconds=g.get("cooldown_seconds", DEFAULT_DD_COOLDOWN_SECONDS),
            )
        ]
    return [default_drawdown_gate()]


def _target_and_schedule(spec: dict):
    """Target MarketConfig + schedule filter for a spec — shared by run_spec and
    the episode re-test so both execute under identical venue rules."""
    t = spec["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    # Unified schedule: orion is the source of truth (framework/orion_schedule).
    schedule = (
        OrionScheduleFilter(spec.get("schedule_version", "nofilter"))
        if t["exchange"].upper() == "GMO"
        else None
    )
    return target, schedule


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
    schedule=None,
) -> tuple[dict, Path, str]:
    """Expression-scan flavour: build combos from a spec, run through the core.

    `schedule` overrides the spec's default venue schedule when given (e.g. to
    restrict trading to specific JST hours) — otherwise the target's own venue
    rules apply.
    """
    target, default_schedule = _target_and_schedule(spec)
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
        schedule=schedule if schedule is not None else default_schedule,
        gates=gates_for_spec(spec),
        costs_bps=spec.get("costs_bps"),
        rank_metric=rank_metric,
        top_quantile=top_quantile,
        min_n=min_n,
        on_result=on_result,
    )
