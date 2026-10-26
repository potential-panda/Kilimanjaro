"""Batch runner for the continuous-barrier framework: direction x clock x dead band.

Stage 15 evaluates ONE direction rule per invocation and reloads the multi-month
feature frame each time (~1-2 min). A grid of a few hundred cells would spend nearly
all of its wall time re-reading identical data, so this runner inverts the loop:

    load the frame ONCE  ->  evaluate every direction expression on it
                         ->  build each clock ONCE (clocks are direction-independent)
                         ->  cross with every dead band

Only the target prices and the gate are per-cell. That turns an O(cells) sequence of
frame loads into O(1), which is what makes a wide sweep practical on this box.

Every cell still goes through the SAME stage-15 pieces as a single run — orion's
schedule, the rolling drawdown gate, BetweenSignalReturn — so batch results are
directly comparable with `cli.py return-eval` output rather than a parallel universe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.root import data_root as _resolve_data_root

_DEFAULT_DATA_ROOT = None


def run_batch(
    cfg: dict,
    start: str,
    end: str,
    *,
    cost_bps: float = 1.0,
    data_root: Path | None = _DEFAULT_DATA_ROOT,
    progress=print,
) -> pd.DataFrame:
    """Run the full direction x clock x dead-band grid for one market pair."""
    data_root = _resolve_data_root(data_root)
    from kilimanjaro.data.bars import bar_closes, hysteresis, runs_to_signals
    from kilimanjaro.framework.gate import (
        DEFAULT_DD_COOLDOWN_SECONDS,
        DEFAULT_DD_THRESHOLD_BPS,
        DEFAULT_DD_WINDOW_SECONDS,
        RollingDrawdownGate,
    )
    from kilimanjaro.framework.orion_schedule import OrionScheduleFilter
    from kilimanjaro.framework.protocol import MarketConfig
    from kilimanjaro.return_calc.between_signal import BetweenSignalReturn
    from kilimanjaro.return_calc.continuous_barrier import ContinuousBarrierReturn
    from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

    markets, t = cfg["markets"], cfg["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    directions: dict[str, str] = cfg["directions"]
    clocks: list[dict] = cfg["clocks"]
    bands: list[float] = cfg.get("dead_bands", [0.0, 1.0, 2.0])
    # Barrier geometry: each entry is {tp, sl, timeout}; all-None reduces to
    # between-signal (exit only on direction change), so the default reproduces
    # the earlier grid exactly and any difference is attributable to the barrier.
    barriers: list[dict] = cfg.get("barriers", [{}])
    qty_col = cfg.get("qty_col", "a_ta_total_qty_1s")
    # Schedule is a swept axis, not a fixed setting: orion's versions are (weekday, hour)
    # tables and `default_active`/`default_inactive` OVERLAP rather than partition, so the
    # only honest way to compare them is to run the same cells under each.
    sched_names = cfg.get("schedules") or [cfg.get("schedule", "nofilter")]
    scheds = [(nm, OrionScheduleFilter(nm)) for nm in sched_names]

    # ── one frame for everything ──────────────────────────────────────────────
    load_start = (
        datetime.strptime(
            start,
            "%Y-%m-%d",
        ).replace(tzinfo=UTC)
        - timedelta(days=2)
    ).strftime("%Y-%m-%d")
    need = (
        " + ".join(f"({e})" for e in directions.values()) + f" + (a_mid) + ({qty_col})"
    )
    progress(f"loading frame {load_start}..{end} for {len(directions)} directions")
    frame = ExpressionSignal.create(
        markets=markets,
        expr=need,
        threshold=0.0,
        min_age=None,
        max_age=None,
        invert=False,
        label="cont_batch",
    ).load_data(load_start, end, data_root)
    ts = frame["ts"].to_numpy(dtype=float)
    mid = np.asarray(eval_expr("a_mid", frame), dtype=float)
    qty = np.asarray(eval_expr(qty_col, frame), dtype=float)
    start_ts = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    progress(f"  frame {len(ts):,} rows")

    dvals = {}
    for name, expr in directions.items():
        try:
            dvals[name] = np.asarray(eval_expr(expr, frame), dtype=float)
        except Exception as exc:  # noqa: BLE001 - an invalid optional direction is reported and skipped
            progress(f"  direction {name} FAILED: {exc}")  # skipped, not fatal
    # Clocks do not depend on the direction rule, so build each exactly once.
    closes = {}
    for c in clocks:
        key = (c["kind"], float(c["param"]))
        if key not in closes:
            ci = bar_closes(c["kind"], float(c["param"]), ts=ts, mid=mid, qty=qty)
            closes[key] = ci[ci > 0]
    progress(
        f"  built {len(closes)} clocks; "
        + ", ".join(f"{k[0]}:{k[1]:g}={len(v):,}" for k, v in list(closes.items())[:6])
    )

    rows = []
    total = len(dvals) * len(closes) * len(bands) * len(barriers) * len(scheds)
    done = 0
    for (kind, param), ci in closes.items():
        # A data-dependent close is only KNOWN once its second completes — acting at
        # ts[ci] would front-run it by up to 1s (the fill-leak class, worth ~5 bps here).
        delay = 1.0 if kind in ("range", "volume", "dollar") else 0.0
        n_bars = int((ci >= np.searchsorted(ts, start_ts)).sum())
        for dname, dv in dvals.items():
            at_bar = dv[ci - 1]
            for band in bands:
                d = hysteresis(at_bar, float(band)) if band else np.sign(at_bar)
                e_ts, e_dir, x_ts = runs_to_signals(ci, d, ts)
                done += 1
                if len(e_ts) == 0:
                    continue
                sigs = pd.DataFrame(
                    {
                        "ts": e_ts + delay,
                        "direction": e_dir,
                        "strength": np.abs(e_dir),
                        "exit_ts": x_ts + delay,
                    }
                )
                sigs = sigs[sigs.ts >= start_ts]
                for bar in barriers:
                    tp, sl, to = bar.get("tp"), bar.get("sl"), bar.get("timeout")
                    calc = (
                        BetweenSignalReturn()
                        if tp is None and sl is None and to is None
                        else ContinuousBarrierReturn(
                            tp_bps=tp, sl_bps=sl, max_hold_s=to
                        )
                    )
                    tr_all = calc.compute(sigs, target, start, end, data_root)
                    for sched_name, sched in scheds:
                        tr, _ = sched.apply(tr_all)
                        n_pre_gate = len(tr)
                        tr, _ = RollingDrawdownGate(
                            window_seconds=DEFAULT_DD_WINDOW_SECONDS,
                            threshold_bps=DEFAULT_DD_THRESHOLD_BPS,
                            cooldown_seconds=DEFAULT_DD_COOLDOWN_SECONDS,
                        ).apply(tr)
                        tr = tr.dropna(subset=["return_bps"])
                        n = len(tr)
                        if n == 0:
                            continue
                        gross = float(tr.return_bps.sum())
                        sd = float(tr.return_bps.std()) if n > 1 else 0.0
                        net_avg = gross / n - cost_bps
                        rows.append(
                            {
                                "schedule": sched_name,
                                # Recorded so the gate is DEMONSTRABLE in the output, not merely
                                # present in the code path.
                                "n_gated_out": n_pre_gate - len(tr),
                                "direction": dname,
                                "clock": kind,
                                "param": param,
                                "dead_band": band,
                                "tp": tp,
                                "sl": sl,
                                "timeout": to,
                                "stop_share": round(
                                    float((tr.exit_reason == "stop").mean()), 3
                                )
                                if "exit_reason" in tr
                                else 0.0,
                                "tp_share": round(
                                    float((tr.exit_reason == "take_profit").mean()), 3
                                )
                                if "exit_reason" in tr
                                else 0.0,
                                "n_bars": n_bars,
                                "n_trades": n,
                                "bars_per_trade": round(n_bars / n, 1),
                                "avg_bps": round(gross / n, 3),
                                "net_avg_bps": round(net_avg, 3),
                                "net_total_bps": round(gross - cost_bps * n, 1),
                                # se of the NET mean: a wide grid manufactures winners on thin cells,
                                # so every row carries its own error bar and t.
                                "se": round(sd / np.sqrt(n), 3)
                                if sd > 0
                                else float("nan"),
                                "t": round(net_avg / (sd / np.sqrt(n)), 2)
                                if sd > 0
                                else float("nan"),
                                "sharpe": round(
                                    float(
                                        tr.return_bps.sub(cost_bps).mean()
                                        / sd
                                        * np.sqrt(n)
                                    ),
                                    2,
                                )
                                if sd > 0
                                else 0.0,
                                "avg_hold_s": round(
                                    float((tr.exit_exec_ts - tr.entry_exec_ts).mean()),
                                    0,
                                ),
                                "expr": directions[dname],
                            }
                        )
            if done % 60 < len(bands):
                progress(f"  {done}/{total} cells")
    out = pd.DataFrame(rows)
    return (
        out.sort_values("net_avg_bps", ascending=False).reset_index(drop=True)
        if len(out)
        else out
    )
