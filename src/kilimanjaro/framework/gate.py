"""Risk gates: sequential, stateful filters over time-ordered trades.

Unlike ScheduleFilter (vectorized, row-independent), a Gate walks the trade
sequence in order and its keep/block decision for one trade depends on the
trades before it. Gates run in run_grid() after the schedule filter and
before stats — the same order flip_sar.scan() has always used.

A Gate's apply() must be pure (no instance state mutated), so one instance
can be shared across all combos of a grid.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd


class Gate(Protocol):
    """Sequential risk filter applied to a time-ordered trades DataFrame."""

    @property
    def params(self) -> dict:
        """Gate parameters, for params.json / reproducibility."""
        ...

    def apply(self, trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """Return (kept_trades, info). Scalar info values are merged into the
        result row by run_grid; non-scalar values (lists, dicts) are kept out
        of CSV rows but available to direct callers."""
        ...


@dataclass(frozen=True)
class RollingDrawdownGate:
    """Rolling drawdown gate (extracted verbatim from flip_sar).

    Closes the gate when cumulative return_bps within the trailing
    window_seconds falls below -threshold_bps. While closed, trades are
    blocked. The gate stays closed for cooldown_seconds, then reopens with
    the window reset.

    Expects columns: ts, return_bps (NaN allowed), and optionally exit_ts
    (NaN for the last flip; falls back to ts when absent).
    """

    window_seconds: float
    threshold_bps: float
    cooldown_seconds: float

    @property
    def params(self) -> dict:
        return {
            "rolling_dd_window_seconds": self.window_seconds,
            "rolling_dd_threshold_bps": self.threshold_bps,
            "rolling_dd_cooldown_seconds": self.cooldown_seconds,
        }

    def apply(self, trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        if trades.empty or self.threshold_bps <= 0 or self.window_seconds <= 0:
            return trades, {"n_dd_blocked": 0, "n_dd_triggers": 0, "dd_triggers": []}

        ts_arr = trades["ts"].values
        if "exit_ts" in trades.columns:
            exit_ts_arr = trades["exit_ts"].values
        else:
            exit_ts_arr = np.full(len(ts_arr), np.nan)
        pnl_arr = trades["return_bps"].values

        window: deque[tuple[float, float]] = deque()
        kept = np.ones(len(ts_arr), dtype=bool)
        gate_open = True
        gate_reopen_ts = -np.inf
        triggers: list[dict] = []
        current_blocked = 0

        for i in range(len(ts_arr)):
            t_entry = ts_arr[i]
            t_exit = exit_ts_arr[i] if not np.isnan(exit_ts_arr[i]) else ts_arr[i]

            if not gate_open:
                if t_entry >= gate_reopen_ts:
                    triggers[-1]["n_blocked"] = current_blocked
                    gate_open = True
                    window.clear()
                    current_blocked = 0
                else:
                    kept[i] = False
                    current_blocked += 1
                    continue

            while window and window[0][0] < t_exit - self.window_seconds:
                window.popleft()

            window_sum = sum(r for _, r in window)
            if window_sum < -self.threshold_bps:
                kept[i] = False
                gate_open = False
                gate_reopen_ts = t_entry + self.cooldown_seconds
                current_blocked = 1
                triggers.append({
                    "close_ts": t_entry,
                    "reopen_ts": gate_reopen_ts,
                    "window_sum_bps": window_sum,
                    "n_blocked": 0,
                })
                continue

            r = pnl_arr[i]
            if not np.isnan(r):
                window.append((t_exit, r))

        if not gate_open and triggers:
            triggers[-1]["n_blocked"] = current_blocked

        n_blocked = int((~kept).sum())
        info = {
            "n_dd_blocked": n_blocked,
            "n_dd_triggers": len(triggers),
            "dd_triggers": triggers,
        }
        return trades[kept].copy(), info
