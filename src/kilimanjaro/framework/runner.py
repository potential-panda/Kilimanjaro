"""Generic parameter-grid runner.

Sweeps a list of (SignalDef, ReturnCalc[, ScheduleFilter]) combos, groups by
SignalDef.cache_key to reuse expensive data loads, and returns one result row
per combo.

Usage
-----
    from kilimanjaro.framework import run_grid, MarketConfig, ScheduleFilter
    from kilimanjaro.signal import ExpressionSignal
    from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn

    market = {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT", "execution_size": None}}
    combos = [
        (ExpressionSignal.create(markets=market, expr="a_obi_ema_k5_300s", threshold=t),
         FixedHorizonReturn(h))
        for t in [0.3, 0.4, 0.5]
        for h in [30, 60, 120]
    ]
    target = MarketConfig("GMO", "ETH_JPY", "0.3")
    rows = run_grid(combos, target, "2026-05-01", "2026-06-30", costs_bps=1.0)

Pipeline per combo (the canonical scan order):
    signals → ReturnCalc.compute → ScheduleFilter → Gates → dropna → stats
"""

from __future__ import annotations

from itertools import groupby
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from kilimanjaro.framework.costs import ROUND_TRIP_COST_BPS
from kilimanjaro.framework.gate import Gate
from kilimanjaro.framework.performance import (
    compute_path_metrics,
    compute_path_metrics_jpy,
)
from kilimanjaro.framework.protocol import MarketConfig, ReturnCalc, SignalDef
from kilimanjaro.framework.schedule import ScheduleFilter
from kilimanjaro.framework.stats import compute_stats

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


def _base_row(
    sig: SignalDef,
    rc: ReturnCalc,
    sched: ScheduleFilter | None,
    combo_id: int,
    n_combos: int,
) -> dict:
    row = {**sig.params, **rc.params}
    if sched is not None and sched.name:
        row["schedule"] = sched.name
    row["_combo_id"] = combo_id
    row["n_combos_tested"] = n_combos
    return row


def run_grid(
    combos: list[tuple],
    target: MarketConfig,
    start_date: str,
    end_date: str,
    costs_bps: float = ROUND_TRIP_COST_BPS,
    schedule: ScheduleFilter | None = None,
    gates: list[Gate] | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    on_data_loaded: Callable[[tuple, Any], None] | None = None,
    on_result: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run the full parameter grid and return result rows.

    Parameters
    ----------
    combos : list of (SignalDef, ReturnCalc) or (SignalDef, ReturnCalc, ScheduleFilter)
        Each tuple is one grid cell. Combos sharing the same SignalDef.cache_key
        reuse a single load_data() call. A 3-tuple's ScheduleFilter overrides
        the grid-wide `schedule` for that combo (schedules-as-grid-dimension).
    target : MarketConfig
        Execution market for return calculation (e.g. GMO/ETH_JPY).
    start_date, end_date : str
        Date range YYYY-MM-DD.
    costs_bps : float
        Round-trip cost subtracted from gross returns.
    schedule : ScheduleFilter, optional
        Default schedule for 2-tuple combos.
    gates : list of Gate, optional
        Sequential risk gates applied after the schedule filter, in order.
        Scalar values from each gate's info dict (e.g. n_dd_blocked) are
        merged into the result row.
    data_root : Path
        Root of the parquet data store.
    on_data_loaded : callable(cache_key, data), optional
        Called once per cache_key group right after load_data() succeeds.
        Use for side effects like computing age histograms.
    on_result : callable(row), optional
        Called for each result row as it is produced (progress reporting).

    Returns
    -------
    list[dict]
        One dict per combo. Always has "status": "ok" | "ERROR: ..." | "no_signals",
        "_combo_id" (index into the input combos list) and "n_combos_tested".
        "ok" rows also have all sig.params, rc.params, schedule name (when the
        combo's ScheduleFilter has one), "n_trades" (post-gate, pre-dropna),
        gate scalar counts, and compute_stats() fields.
    """
    n_combos = len(combos)

    # Normalize to (cache_key, combo_id, sig, rc, sched); sort so groupby works
    indexed = []
    for combo_id, combo in enumerate(combos):
        sig, rc = combo[0], combo[1]
        sched = combo[2] if len(combo) > 2 else schedule
        indexed.append((sig.cache_key, combo_id, sig, rc, sched))
    indexed.sort(key=lambda x: x[0])

    rows_by_id: dict[int, dict] = {}

    def _emit(row: dict) -> None:
        rows_by_id[row["_combo_id"]] = row
        if on_result:
            on_result(row)

    for cache_key, group_iter in groupby(indexed, key=lambda x: x[0]):
        group = list(group_iter)
        sig0 = group[0][2]

        try:
            data = sig0.load_data(start_date, end_date, data_root)
        except Exception as e:
            for _, combo_id, sig, rc, sched in group:
                _emit(
                    {
                        **_base_row(sig, rc, sched, combo_id, n_combos),
                        "status": f"ERROR loading: {e}",
                    }
                )
            continue

        if on_data_loaded:
            on_data_loaded(cache_key, data)

        for _, combo_id, sig, rc, sched in group:
            base = _base_row(sig, rc, sched, combo_id, n_combos)
            try:
                signals = sig.generate(data)
                if signals.empty:
                    _emit({**base, "status": "no_signals"})
                    continue

                trades = rc.compute(signals, target, start_date, end_date, data_root)

                if sched:
                    trades, _ = sched.apply(trades)

                for gate in gates or []:
                    trades, info = gate.apply(trades)
                    base.update(
                        {
                            k: v
                            for k, v in info.items()
                            if isinstance(v, (int, float, str))
                        }
                    )

                valid = trades.dropna(subset=["return_bps"])

                stat_kwargs: dict = {}
                if "strength" in valid.columns:
                    stat_kwargs["signal_strength"] = valid["strength"]
                if "holding_seconds" in valid.columns:
                    stat_kwargs["holding_seconds"] = valid["holding_seconds"]
                if "episode" in valid.columns:
                    stat_kwargs["episodes"] = valid["episode"]

                stats = compute_stats(valid["return_bps"], costs_bps, **stat_kwargs)
                # Path/drawdown metrics on the net-return equity curve — the
                # trade-distribution stats above are path-blind (see performance.py).
                net = valid["return_bps"].to_numpy() - costs_bps
                ts_arr = valid["ts"].to_numpy()
                path = compute_path_metrics(net, ts_arr)
                row = {**base, "n_trades": len(trades), **stats, **path, "status": "ok"}
                # JPY per-fill curve (Himalaya-faithful) when we know the fill
                # price and execution size — notional = size × entry_price.
                if "entry_price" in valid.columns:
                    try:
                        size = float(target.execution_size)
                        notional = size * valid["entry_price"].to_numpy()
                        row.update(compute_path_metrics_jpy(net, ts_arr, notional))
                    except (TypeError, ValueError):
                        pass

                # Per-month sub-stats from the trades already in memory —
                # this is what makes decay/regime classification ~free
                # (no extra IO or joins; see evaluate.classify_verdicts).
                if len(valid):
                    # JST months, consistent with jst_date/jst_hour everywhere else
                    months = pd.to_datetime(
                        valid["ts"] + 9 * 3600, unit="s", utc=True
                    ).dt.strftime("%Y-%m")
                    row["_monthly"] = {
                        m: (int(len(g)), float(g.mean() - costs_bps))
                        for m, g in valid["return_bps"].groupby(months)
                    }

            except Exception as e:
                row = {**base, "status": f"ERROR: {e}"}

            _emit(row)

    # Return rows in input-combos order
    return [rows_by_id[i] for i in sorted(rows_by_id)]
