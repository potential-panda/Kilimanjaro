"""Candidate tier: re-measure screening survivors on non-overlapping episodes.

The screening scan (fixed_horizon, per-second) inflates significance through
overlapping return windows, so it can only *screen* — it selects candidates by
rank stability across a train/holdout split, which overlap cannot fake
(doc/overlapping-returns.md). This module takes those rank-stable candidates and
re-measures each with EpisodeReturn: exactly one non-overlapping trade per
episode, real bid/ask execution, a true holding-period distribution. On
non-overlapping returns the plain significance test is legitimate again, so the
verdict's FDR runs on these honest numbers — never on the screening tier's.

This is step 9b/9c of the scanner loop: screening scan -> attach -> episode
re-test -> attach -> verdict. It runs only when screening produced at least one
rank-stable candidate, so most specs still write a single result folder.
"""

from __future__ import annotations

from pathlib import Path

from kilimanjaro.driver.scan_runner import (
    CSV_FIELDS,
    _target_and_schedule,
    gates_for_spec,
    run_grid_scan,
)
from kilimanjaro.return_calc.episode import EpisodeReturn
from kilimanjaro.signal.expression import ExpressionSignal

# Combo-identity fields on a screening row (ExpressionSignal.params), minus the
# label alias, used to recover which signals to re-measure.
_IDENTITY = ("threshold", "min_age", "max_age", "invert")


def _expr_by_label(spec: dict) -> dict[str, str]:
    """label (or raw expr) -> raw expr. A screening row stores `expr` as the
    label when one exists (ExpressionSignal.params), so map back to the formula."""
    out: dict[str, str] = {}
    for e in spec["expressions"]:
        raw = e["expr"]
        out[e.get("label") or raw] = raw
        out[raw] = raw
    return out


def build_episode_combos(spec: dict, candidate_rows: list[dict]) -> list[tuple]:
    """(ExpressionSignal, EpisodeReturn) for exactly the rank-stable candidates.

    Built directly from the survivor tuples — NOT via the cartesian build_combos
    — so the candidate tier measures the combos that actually survived, and no
    others. Each is swept over spec.max_holds (default: hold to natural episode
    end), the one dimension episode adds over fixed_horizon.
    """
    markets = spec["markets"]
    by_label = _expr_by_label(spec)
    max_holds = spec.get("max_holds", [None])

    seen: set[tuple] = set()
    combos: list[tuple] = []
    for r in candidate_rows:
        raw_expr = by_label.get(r["expr"])
        if raw_expr is None:  # row not from this spec — skip defensively
            continue
        key = (raw_expr, r["threshold"], r["min_age"], r["max_age"], r["invert"])
        if key in seen:
            continue
        seen.add(key)
        sig = ExpressionSignal.create(
            markets=markets,
            expr=raw_expr,
            threshold=r["threshold"],
            min_age=r["min_age"],
            max_age=r["max_age"],
            invert=bool(r["invert"]),
            label=r["expr"] if r["expr"] != raw_expr else None,
        )
        for mh in max_holds:
            combos.append((sig, EpisodeReturn(max_hold_seconds=mh)))
    return combos


def run_episode_retest(
    spec: dict,
    candidate_rows: list[dict],
    start: str,
    end: str,
    *,
    results_root: Path,
    screening_scan_id: str,
    split: str | None = None,
) -> tuple[dict, Path, str] | None:
    """Re-measure the rank-stable candidates under EpisodeReturn.

    Returns (res, out_dir, scan_id) for the episode scan, or None when there are
    no candidates to re-test (the common case — the caller then fails the spec
    at the screening tier). The episode scan lands in a sibling `*_episode`
    folder so the two tiers stay paired in cycle/15_return_eval.
    """
    combos = build_episode_combos(spec, candidate_rows)
    if not combos:
        return None
    target, schedule = _target_and_schedule(spec)
    scan_id = f"{screening_scan_id}_episode"
    return run_grid_scan(
        combos,
        target,
        start,
        end,
        results_root=results_root,
        scan_name=scan_id,
        csv_fields=CSV_FIELDS,
        split=split,
        param_meta={"spec": spec, "tier": "episode", "screening_scan_id": screening_scan_id},
        schedule=schedule,
        # Drawdown gate applies here (episodes are non-overlapping), even though
        # the screening spec's own return_calc still says fixed_horizon.
        gates=gates_for_spec(spec, return_calc="episode"),
        costs_bps=spec.get("costs_bps"),
        scan_id=scan_id,
        out_dir=results_root / scan_id,
    )


# ── stage 15: evaluate a candidate spec as the calibrated trading strategy ─────
# The screening tiers (13/14) measure PREDICTION (IC). This measures whether the
# candidate PAYS: run its formula through the calibrated mechanism (v3 trailing /
# v1 triple barrier, fill_delay=1, US session, rolling-DD gate = aquila config) over
# a threshold sweep, and report standardized per-row trade economics. Repeat-op made
# code so every candidate is judged identically (no ad-hoc scripts).

_CALIB_JST_HOURS = (22, 23, 0, 1, 2)   # aquila US-session
_CALIB_DD = dict(threshold_bps=200.0, window_s=21600, cooldown_s=3600)  # aquila DD gate
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


def evaluate_spec(spec: dict, start: str, end: str, thresholds=None, *,
                  fill_delay_seconds: float = 1.0, cost_bps: float = 1.0,
                  jst_hours=_CALIB_JST_HOURS, dd_threshold_bps: float = _CALIB_DD["threshold_bps"],
                  dd_window_s: float = _CALIB_DD["window_s"], dd_cooldown_s: float = _CALIB_DD["cooldown_s"],
                  warmup_days: int = 2, data_root: Path = _DEFAULT_DATA_ROOT):
    """Run `spec` through its trading mechanism over a threshold sweep; one row per
    threshold with standardized economics:
      n_trades, total_bps, avg_bps  (GROSS: bid/ask execution, spread already paid)
      net_total_bps, net_avg_bps    (minus `cost_bps` latency slippage — the verdict)
      sharpe, avg_hold_s
    The frame is loaded once (warmup-padded) and reused across thresholds."""
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta

    from kilimanjaro.framework.gate import RollingDrawdownGate
    from kilimanjaro.framework.protocol import MarketConfig
    from kilimanjaro.framework.schedule import ScheduleFilter
    from kilimanjaro.return_calc.maker_fep import MakerFEPReturn
    from kilimanjaro.return_calc.trailing_stop import TrailingStopReturn
    from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn

    e = spec["expressions"][0]
    markets, t = spec["markets"], spec["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    thresholds = thresholds if thresholds is not None else spec.get("thresholds", [3.0])
    barrier = spec.get("barriers", [10.0])[0]
    cap = spec.get("time_caps", [300])[0]
    rc_name = spec.get("return_calc", "trailing_stop")
    # maker return_bps already nets the maker/taker fees, so no separate latency cost applies.
    if rc_name == "maker_fep":
        cost_bps = 0.0
    invert = e.get("invert", [False])[0]
    label = e.get("label")

    load_start = (datetime.strptime(start, "%Y-%m-%d")
                  - timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    frame = ExpressionSignal.create(markets=markets, expr=e["expr"], threshold=0.0,
                                    min_age=None, max_age=None, invert=invert,
                                    label=label).load_data(load_start, end, data_root)
    blocked = [h for h in range(24) if h not in jst_hours]
    sched = ScheduleFilter(block_weekday_hours=blocked, block_weekend_hours=blocked,
                           block_gmo_maintenance=False, name="us")
    start_ts = datetime.strptime(start, "%Y-%m-%d").timestamp()

    rows = []
    for thr in thresholds:
        sig = ExpressionSignal.create(markets=markets, expr=e["expr"], threshold=float(thr),
                                      min_age=None, max_age=None, invert=invert, label=label)
        if rc_name == "triple_barrier":
            rc = TripleBarrierReturn(barrier, cap, fill_delay_seconds=fill_delay_seconds)
        elif rc_name == "maker_fep":  # maker limit-order economics (fees baked into return_bps)
            m = spec.get("maker", {})
            rc = MakerFEPReturn(
                offset_bps=m.get("offset_bps", 0.0), fill_window_s=m.get("fill_window_s", 30.0),
                hold_s=m.get("hold_s", 30.0), exit_window_s=m.get("exit_window_s", 60.0),
                maker_fee_bps=m.get("maker_fee_bps", 0.0), taker_fee_bps=m.get("taker_fee_bps", 5.0),
                long_only=m.get("long_only", False), price_unit=m.get("price_unit", 0.0))
        else:  # trailing_stop (v3): no binding time cap
            rc = TrailingStopReturn(barrier, time_cap_seconds=None,
                                    fill_delay_seconds=fill_delay_seconds)
        tr = rc.compute(sig.generate(frame), target, load_start, end, data_root)
        tr, _ = sched.apply(tr)
        if dd_threshold_bps and np.isfinite(dd_threshold_bps):
            tr, _ = RollingDrawdownGate(window_seconds=dd_window_s, threshold_bps=dd_threshold_bps,
                                        cooldown_seconds=dd_cooldown_s).apply(tr)
        tr = tr.dropna(subset=["return_bps"])
        tr = tr[tr.entry_exec_ts >= start_ts]
        n = len(tr)
        gross = float(tr.return_bps.sum()) if n else 0.0
        net = gross - cost_bps * n
        sd = float(tr.return_bps.std()) if n > 1 else 0.0
        sharpe = float(tr.return_bps.sub(cost_bps).mean() / sd * np.sqrt(n)) if sd > 0 else 0.0
        hold = float((tr.exit_exec_ts - tr.entry_exec_ts).mean()) if n else 0.0
        rows.append({
            "threshold": float(thr), "n_trades": n,
            "total_bps": round(gross, 1), "avg_bps": round(gross / n, 3) if n else 0.0,
            "net_total_bps": round(net, 1), "net_avg_bps": round(net / n, 3) if n else 0.0,
            "sharpe": round(sharpe, 2), "avg_hold_s": round(hold, 0),
        })
    return pd.DataFrame(rows)
