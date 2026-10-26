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
from zoneinfo import ZoneInfo

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
_JST = ZoneInfo("Asia/Tokyo")


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
        param_meta={
            "spec": spec,
            "tier": "episode",
            "screening_scan_id": screening_scan_id,
        },
        schedule=schedule,
        # Drawdown gate applies here (episodes are non-overlapping), even though
        # the screening spec's own return_calc still says fixed_horizon.
        gates=gates_for_spec(spec, return_calc="episode"),
        costs_bps=spec.get("costs_bps"),
        scan_id=scan_id,
        out_dir=results_root / scan_id,
    )


def _evaluate_continuous_spec(
    spec: dict,
    target,
    start: str,
    end: str,
    params=None,
    *,
    cost_bps: float = 1.0,
    dd_threshold_bps: float | None = None,
    data_root=None,
):
    """Stage 15 for the CONTINUOUS-BARRIER framework: pluggable clock x pluggable direction.

    A clock (`clock.kind`: time/volume/dollar/tick/range) emits neutral decision points;
    `direction_expr` picks the side at each; consecutive same-direction bars are held as
    ONE position, so cost is paid per direction change. SAR is then just one possible
    direction rule rather than also being the clock — which is what made the earlier
    same-clock comparison unfair to every non-SAR rule.

    Swept over the clock parameter (seconds / units / notional / move).
    """
    from datetime import datetime, timedelta

    import numpy as np
    import pandas as pd

    from kilimanjaro.data.bars import bar_closes, hysteresis, runs_to_signals
    from kilimanjaro.framework.gate import (
        DEFAULT_DD_COOLDOWN_SECONDS,
        DEFAULT_DD_THRESHOLD_BPS,
        DEFAULT_DD_WINDOW_SECONDS,
        RollingDrawdownGate,
    )
    from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
    from kilimanjaro.return_calc.between_signal import BetweenSignalReturn
    from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

    clock = spec["clock"]
    kind = clock["kind"]
    params = params if params is not None else clock.get("params", [300])
    dir_expr = spec["direction_expr"]
    qty_col = clock.get("qty_col", "a_ta_total_qty_1s")
    dir_thr = float(spec.get("direction_threshold", 0.0))
    sched = OrionScheduleFilter(spec.get("schedule", "nofilter"))
    dd_bps = DEFAULT_DD_THRESHOLD_BPS if dd_threshold_bps is None else dd_threshold_bps

    load_start = (
        datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_JST) - timedelta(days=2)
    ).strftime("%Y-%m-%d")
    need = f"({dir_expr}) + (a_mid)" + (
        f" + ({qty_col})" if kind in ("volume", "dollar") else ""
    )
    frame = ExpressionSignal.create(
        markets=spec["markets"],
        expr=need,
        threshold=0.0,
        min_age=None,
        max_age=None,
        invert=False,
        label="cont",
    ).load_data(load_start, end, data_root)
    ts = frame["ts"].to_numpy(dtype=float)
    mid = np.asarray(eval_expr("a_mid", frame), dtype=float)
    qty = (
        np.asarray(eval_expr(qty_col, frame), dtype=float)
        if kind in ("volume", "dollar")
        else None
    )
    dvals = np.asarray(eval_expr(dir_expr, frame), dtype=float)
    start_ts = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_JST).timestamp()

    rows = []
    for p in params:
        ci = bar_closes(kind, float(p), ts=ts, mid=mid, qty=qty)
        ci = ci[ci > 0]
        # Causal read: a 1s value for second t is not knowable until t closes, so the
        # decision at a bar closing on row i uses row i-1.
        # Hysteresis dead band: hold the side until the signal crosses the OPPOSITE
        # threshold. Without it sign(z) wobbles across zero and churns (~2 bars/trade).
        d_at_bar = (
            hysteresis(dvals[ci - 1], float(dir_thr))
            if dir_thr
            else np.sign(dvals[ci - 1])
        )
        e_ts, e_dir, x_ts = runs_to_signals(ci, d_at_bar, ts)
        # CLOCK CAUSALITY. A data-dependent close (range/volume/dollar) is only KNOWN
        # once its second is complete, so acting at ts[ci] would front-run the close by
        # up to 1s — the same lookahead class as THE FILL LEAK, and worth ~5 bps on a
        # lead-lag trade. Time/tick closes are deterministic and need no delay.
        if kind in ("range", "volume", "dollar"):
            e_ts, x_ts = e_ts + 1.0, x_ts + 1.0
        sigs = pd.DataFrame(
            {"ts": e_ts, "direction": e_dir, "strength": np.abs(e_dir), "exit_ts": x_ts}
        )
        sigs = sigs[sigs.ts >= start_ts]
        n_bars = int((ci >= np.searchsorted(ts, start_ts)).sum())
        tr = BetweenSignalReturn().compute(sigs, target, start, end, data_root)
        tr, _ = sched.apply(tr)
        n_pre = len(tr)
        if dd_bps and np.isfinite(dd_bps):
            tr, _ = RollingDrawdownGate(
                window_seconds=DEFAULT_DD_WINDOW_SECONDS,
                threshold_bps=dd_bps,
                cooldown_seconds=DEFAULT_DD_COOLDOWN_SECONDS,
            ).apply(tr)
        tr = tr.dropna(subset=["return_bps"])
        n = len(tr)
        gross = float(tr.return_bps.sum()) if n else 0.0
        net = gross - cost_bps * n
        sd = float(tr.return_bps.std()) if n > 1 else 0.0
        rows.append(
            {
                "clock": kind,
                "param": float(p),
                "dir_thr": dir_thr,
                "n_bars": n_bars,
                "n_trades": n,
                "bars_per_trade": round(n_bars / n, 1) if n else 0.0,
                "n_gated_out": n_pre - n,
                "total_bps": round(gross, 1),
                "avg_bps": round(gross / n, 3) if n else 0.0,
                "net_total_bps": round(net, 1),
                "net_avg_bps": round(net / n, 3) if n else 0.0,
                "sharpe": round(
                    float(tr.return_bps.sub(cost_bps).mean() / sd * np.sqrt(n)), 2
                )
                if sd > 0
                else 0.0,
                "avg_hold_s": round(
                    float((tr.exit_exec_ts - tr.entry_exec_ts).mean()), 0
                )
                if n
                else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _evaluate_flip_spec(
    spec: dict,
    target,
    start: str,
    end: str,
    boxes=None,
    *,
    cost_bps: float = 1.0,
    dd_threshold_bps: float | None = None,
    data_root=None,
):
    """Stage 15 for the flip mechanism: sweep box size, one row per box.

    Same standardized economics as the expression path, so flip and expression
    candidates are directly comparable — plus `box`/`step` in place of `threshold`.
    """
    import numpy as np
    import pandas as pd

    from kilimanjaro.framework.gate import (
        DEFAULT_DD_COOLDOWN_SECONDS,
        DEFAULT_DD_THRESHOLD_BPS,
        DEFAULT_DD_WINDOW_SECONDS,
        RollingDrawdownGate,
    )
    from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
    from kilimanjaro.return_calc.between_signal import BetweenSignalReturn
    from kilimanjaro.signal.sar_flip import SARFlipSignal

    flip = spec["flip"]
    step = float(spec.get("step", 0.04))
    boxes = boxes if boxes is not None else spec.get("boxes", [0.003])
    sched = OrionScheduleFilter(spec.get("schedule", "nofilter"))
    # Flip-mechanism gate defaults (3h/500bps/6h) — NOT aquila's (6h/200bps/1h).
    dd_bps = DEFAULT_DD_THRESHOLD_BPS if dd_threshold_bps is None else dd_threshold_bps

    # CONTINUOUS-BET FRAMING: the flip events are only a CLOCK (when to re-decide);
    # the direction rule is pluggable. `direction_expr` swaps SAR's "which way did the
    # leader just move" for any measured factor, holding the clock fixed so the two are
    # compared on identical entry/exit times — the only difference is the side taken.
    dir_expr = spec.get("direction_expr")
    dir_vals = None
    if dir_expr:
        from datetime import datetime, timedelta

        from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

        load_start = (
            datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_JST)
            - timedelta(days=2)
        ).strftime("%Y-%m-%d")
        frame = ExpressionSignal.create(
            markets=spec["markets"],
            expr=dir_expr,
            threshold=0.0,
            min_age=None,
            max_age=None,
            invert=False,
            label="dir",
        ).load_data(load_start, end, data_root)
        dir_ts = frame["ts"].to_numpy(dtype=float)
        dir_vals = (dir_ts, np.asarray(eval_expr(dir_expr, frame), dtype=float))

    rows = []
    for box in boxes:
        sig = SARFlipSignal(flip["exchange"], flip["symbol"], float(box), step)
        sigs = sig.generate(sig.load_data(start, end, data_root))
        if dir_vals is not None:
            # Causal read: the 1s value for second t is not knowable until t closes, so
            # take the last completed second STRICTLY before the flip (searchsorted on
            # ts-1). Zero/NaN factor values leave the flip untraded rather than guessing.
            gts, gv = dir_vals
            idx = (
                np.searchsorted(
                    gts, sigs["ts"].to_numpy(dtype=float) - 1.0, side="right"
                )
                - 1
            )
            ok = idx >= 0
            v = np.where(ok, gv[np.clip(idx, 0, len(gv) - 1)], np.nan)
            sigs = sigs.assign(direction=np.sign(v))
            sigs = sigs[np.isfinite(sigs.direction) & (sigs.direction != 0)]
        tr = BetweenSignalReturn().compute(sigs, target, start, end, data_root)
        tr, _ = sched.apply(tr)
        n_pre = len(tr)
        if dd_bps and np.isfinite(dd_bps):
            tr, _ = RollingDrawdownGate(
                window_seconds=DEFAULT_DD_WINDOW_SECONDS,
                threshold_bps=dd_bps,
                cooldown_seconds=DEFAULT_DD_COOLDOWN_SECONDS,
            ).apply(tr)
        tr = tr.dropna(subset=["return_bps"])
        n = len(tr)
        gross = float(tr.return_bps.sum()) if n else 0.0
        net = gross - cost_bps * n
        sd = float(tr.return_bps.std()) if n > 1 else 0.0
        rows.append(
            {
                "box": float(box),
                "step": step,
                "n_trades": n,
                "n_gated_out": n_pre - n,
                "total_bps": round(gross, 1),
                "avg_bps": round(gross / n, 3) if n else 0.0,
                "net_total_bps": round(net, 1),
                "net_avg_bps": round(net / n, 3) if n else 0.0,
                "sharpe": round(
                    float(tr.return_bps.sub(cost_bps).mean() / sd * np.sqrt(n)), 2
                )
                if sd > 0
                else 0.0,
                "avg_hold_s": round(
                    float((tr.exit_exec_ts - tr.entry_exec_ts).mean()), 0
                )
                if n
                else 0.0,
            }
        )
    return pd.DataFrame(rows)


# ── stage 15: evaluate a candidate spec as the calibrated trading strategy ─────
# The screening tiers (13/14) measure PREDICTION (IC). This measures whether the
# candidate PAYS: run its formula through the calibrated mechanism (v3 trailing /
# v1 triple barrier, fill_delay=1, US session, rolling-DD gate = aquila config) over
# a threshold sweep, and report standardized per-row trade economics. Repeat-op made
# code so every candidate is judged identically (no ad-hoc scripts).

_CALIB_JST_HOURS = (22, 23, 0, 1, 2)  # aquila US-session
_CALIB_DD = {
    "threshold_bps": 200.0,
    "window_s": 21600,
    "cooldown_s": 3600,
}  # aquila DD gate
from kilimanjaro.data.root import data_root as _resolve_data_root

_DEFAULT_DATA_ROOT = None


def evaluate_spec(
    spec: dict,
    start: str,
    end: str,
    thresholds=None,
    *,
    fill_delay_seconds: float = 1.0,
    cost_bps: float = 1.0,
    jst_hours=_CALIB_JST_HOURS,
    dd_threshold_bps: float = _CALIB_DD["threshold_bps"],
    dd_window_s: float = _CALIB_DD["window_s"],
    dd_cooldown_s: float = _CALIB_DD["cooldown_s"],
    warmup_days: int = 2,
    data_root: Path | None = _DEFAULT_DATA_ROOT,
):
    """Run `spec` through its trading mechanism over a threshold sweep; one row per
    threshold with standardized economics:
      n_trades, total_bps, avg_bps  (GROSS: bid/ask execution, spread already paid)
      net_total_bps, net_avg_bps    (minus `cost_bps` latency slippage — the verdict)
      sharpe, avg_hold_s
    The frame is loaded once (warmup-padded) and reused across thresholds."""
    data_root = _resolve_data_root(data_root)
    from datetime import datetime, timedelta

    import numpy as np
    import pandas as pd

    from kilimanjaro.framework.gate import RollingDrawdownGate
    from kilimanjaro.framework.protocol import MarketConfig
    from kilimanjaro.framework.schedule import ScheduleFilter
    from kilimanjaro.return_calc.maker_fep import MakerFEPReturn
    from kilimanjaro.return_calc.trailing_stop import TrailingStopReturn
    from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn

    t = spec["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])

    # ── flip mechanism (SAR flips + between-signal) ───────────────────────────
    # The one CALIBRATED mechanism (ETH, 98.3% PnL-exact vs the backtest), so it runs
    # through its own certified pieces: SARFlipSignal (exit_ts = the NEXT flip, so exits
    # are driven by ALL flips even when the schedule blocks an entry — the backtest's
    # NO_ENTRY close behaviour), BetweenSignalReturn, orion's OWN schedule module, and
    # the flip gate defaults from framework.gate. Swept over box size, not threshold.
    if spec.get("signal") == "sar_flip":
        return _evaluate_flip_spec(
            spec,
            target,
            start,
            end,
            thresholds,
            cost_bps=cost_bps,
            dd_threshold_bps=dd_threshold_bps,
            data_root=data_root,
        )
    if spec.get("signal") == "continuous":
        return _evaluate_continuous_spec(
            spec,
            target,
            start,
            end,
            thresholds,
            cost_bps=cost_bps,
            dd_threshold_bps=dd_threshold_bps,
            data_root=data_root,
        )

    e = spec["expressions"][0]
    markets = spec["markets"]
    thresholds = thresholds if thresholds is not None else spec.get("thresholds", [3.0])
    barrier = spec.get("barriers", [10.0])[0]
    cap = spec.get("time_caps", [300])[0]
    rc_name = spec.get("return_calc", "trailing_stop")
    # maker return_bps already nets the maker/taker fees, so no separate latency cost applies.
    if rc_name == "maker_fep":
        cost_bps = 0.0
    invert = e.get("invert", [False])[0]
    label = e.get("label")

    load_start = (
        datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_JST)
        - timedelta(days=warmup_days)
    ).strftime("%Y-%m-%d")
    frame = ExpressionSignal.create(
        markets=markets,
        expr=e["expr"],
        threshold=0.0,
        min_age=None,
        max_age=None,
        invert=invert,
        label=label,
    ).load_data(load_start, end, data_root)
    blocked = [h for h in range(24) if h not in jst_hours]
    sched = ScheduleFilter(
        block_weekday_hours=blocked,
        block_weekend_hours=blocked,
        block_gmo_maintenance=False,
        name="us",
    )
    start_ts = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_JST).timestamp()

    rows = []
    for thr in thresholds:
        sig = ExpressionSignal.create(
            markets=markets,
            expr=e["expr"],
            threshold=float(thr),
            min_age=None,
            max_age=None,
            invert=invert,
            label=label,
        )
        if rc_name == "triple_barrier":
            rc = TripleBarrierReturn(
                barrier, cap, fill_delay_seconds=fill_delay_seconds
            )
        elif (
            rc_name == "maker_fep"
        ):  # maker limit-order economics (fees baked into return_bps)
            m = spec.get("maker", {})
            rc = MakerFEPReturn(
                offset_bps=m.get("offset_bps", 0.0),
                fill_window_s=m.get("fill_window_s", 30.0),
                hold_s=m.get("hold_s", 30.0),
                exit_window_s=m.get("exit_window_s", 60.0),
                maker_fee_bps=m.get("maker_fee_bps", 0.0),
                taker_fee_bps=m.get("taker_fee_bps", 5.0),
                long_only=m.get("long_only", False),
                price_unit=m.get("price_unit", 0.0),
                # same causality the taker calcs get — post at ts+delay, never at ts
                fill_delay_seconds=m.get("fill_delay_seconds", fill_delay_seconds),
            )
        else:  # trailing_stop (v3): no binding time cap
            rc = TrailingStopReturn(
                barrier, time_cap_seconds=None, fill_delay_seconds=fill_delay_seconds
            )
        tr = rc.compute(sig.generate(frame), target, load_start, end, data_root)
        tr, _ = sched.apply(tr)
        if dd_threshold_bps and np.isfinite(dd_threshold_bps):
            tr, _ = RollingDrawdownGate(
                window_seconds=dd_window_s,
                threshold_bps=dd_threshold_bps,
                cooldown_seconds=dd_cooldown_s,
            ).apply(tr)
        tr = tr.dropna(subset=["return_bps"])
        tr = tr[tr.entry_exec_ts >= start_ts]
        n = len(tr)
        gross = float(tr.return_bps.sum()) if n else 0.0
        net = gross - cost_bps * n
        sd = float(tr.return_bps.std()) if n > 1 else 0.0
        sharpe = (
            float(tr.return_bps.sub(cost_bps).mean() / sd * np.sqrt(n))
            if sd > 0
            else 0.0
        )
        hold = float((tr.exit_exec_ts - tr.entry_exec_ts).mean()) if n else 0.0
        row = {
            "threshold": float(thr),
            "n_trades": n,
            "total_bps": round(gross, 1),
            "avg_bps": round(gross / n, 3) if n else 0.0,
            "net_total_bps": round(net, 1),
            "net_avg_bps": round(net / n, 3) if n else 0.0,
            "sharpe": round(sharpe, 2),
            "avg_hold_s": round(hold, 0),
        }
        if rc_name == "maker_fep":
            # Mechanism diagnostics — a maker result is only readable alongside these.
            # fill_rate: share of posted limits the book actually crossed (mechanism-level,
            # before the schedule/DD filters). forced_share: of the trades that survive the
            # gates, how many had to force-close as a taker (the expensive path on a
            # fee-charging venue). Both decide whether a net number is trustworthy.
            st = rc.last_stats or {}
            row["fill_rate"] = round(st.get("fill_rate", float("nan")), 4)
            row["n_posted"] = st.get("n_posted", 0)
            row["forced_share"] = round(float(tr.exit_forced.mean()), 4) if n else 0.0
        rows.append(row)
    return pd.DataFrame(rows)
