"""Generic parameter-grid runner.

Sweeps a list of (SignalDef, ReturnCalc) combos, groups by SignalDef.cache_key
to reuse expensive data loads, and returns one result row per combo.

Usage
-----
    from kilimanjaro.framework import run_grid, MarketConfig, ScheduleFilter
    from kilimanjaro.signal.obi_age import OBIAgeSignal
    from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn

    combos = [
        (OBIAgeSignal("BINANCE", "ETH_USDT", depth=5, halflife=300, threshold=0.5),
         FixedHorizonReturn(60))
        for threshold in [0.3, 0.4, 0.5]
        for horizon in [30, 60, 120]
        ...
    ]
    target = MarketConfig("GMO", "ETH_JPY", "0.3")
    rows = run_grid(combos, target, "2026-05-01", "2026-06-30", costs_bps=1.0)
"""

from __future__ import annotations

from itertools import groupby
from pathlib import Path
from typing import Any, Callable

from kilimanjaro.framework.protocol import MarketConfig, ReturnCalc, SignalDef
from kilimanjaro.framework.schedule import ScheduleFilter
from kilimanjaro.framework.stats import compute_stats

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


def run_grid(
    combos: list[tuple[SignalDef, ReturnCalc]],
    target: MarketConfig,
    start_date: str,
    end_date: str,
    costs_bps: float = 1.0,
    schedule: ScheduleFilter | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    on_data_loaded: Callable[[tuple, Any], None] | None = None,
    on_result: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run the full parameter grid and return result rows.

    Parameters
    ----------
    combos : list of (SignalDef, ReturnCalc) pairs
        Each pair is one grid cell. Combos sharing the same SignalDef.cache_key
        reuse a single load_data() call.
    target : MarketConfig
        Execution market for return calculation (e.g. GMO/ETH_JPY).
    start_date, end_date : str
        Date range YYYY-MM-DD.
    costs_bps : float
        Round-trip cost subtracted from gross returns.
    schedule : ScheduleFilter, optional
        Filters applied to trades after return calculation.
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
        One dict per combo. Always has "status": "ok" | "ERROR: ..." | "no_signals".
        "ok" rows also have all sig.params, rc.params, and compute_stats() fields.
    """
    rows: list[dict] = []

    # Sort combos by cache_key so groupby works
    indexed = [(sig.cache_key, sig, rc) for sig, rc in combos]
    indexed.sort(key=lambda x: x[0])

    for cache_key, group_iter in groupby(indexed, key=lambda x: x[0]):
        group = list(group_iter)
        sig0 = group[0][1]

        try:
            data = sig0.load_data(start_date, end_date, data_root)
        except Exception as e:
            for _, sig, rc in group:
                row = {**sig.params, **rc.params, "status": f"ERROR loading: {e}"}
                rows.append(row)
                if on_result:
                    on_result(row)
            continue

        if on_data_loaded:
            on_data_loaded(cache_key, data)

        for _, sig, rc in group:
            try:
                signals = sig.generate(data)
                if signals.empty:
                    row = {**sig.params, **rc.params, "status": "no_signals"}
                    rows.append(row)
                    if on_result:
                        on_result(row)
                    continue

                trades = rc.compute(signals, target, start_date, end_date, data_root)

                if schedule:
                    trades, _ = schedule.apply(trades)

                valid = trades.dropna(subset=["return_bps"])

                stat_kwargs: dict = {}
                if "strength" in valid.columns:
                    stat_kwargs["signal_strength"] = valid["strength"]
                if "holding_seconds" in valid.columns:
                    stat_kwargs["holding_seconds"] = valid["holding_seconds"]

                stats = compute_stats(valid["return_bps"], costs_bps, **stat_kwargs)
                row = {**sig.params, **rc.params, **stats, "status": "ok"}

            except Exception as e:
                row = {**sig.params, **rc.params, "status": f"ERROR: {e}"}

            rows.append(row)
            if on_result:
                on_result(row)

    return rows
