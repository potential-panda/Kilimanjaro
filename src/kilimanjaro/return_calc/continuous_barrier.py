"""Continuous barrier: hold a side, exit on whichever comes first —
   direction change, take-profit, stop-loss, or timeout.

This completes the continuous-TRIPLE-BARRIER idea. The `between_signal` calc exits
only when the direction rule flips, so a position rides the full run whatever happens
in between; every result in the 1,712-cell continuous grid was measured that way. Here
the run is still the outer structure, but the position can also be closed early by a
price barrier or a clock.

Why it can differ rather than just re-sampling the same distribution: the measured
profile is a ~36% hit rate with positive mean — a minority of large winners carrying a
majority of small losers. That is exactly the shape a stop (truncate the left tail) or
a take-profit (harvest the right tail before it decays) acts on. It may help or hurt,
but it is a genuinely different estimator, not a re-slice.

EXIT PRECEDENCE at equal timestamps: stop before take-profit. When a single tick has
moved far enough to satisfy both (a gap through the band), assuming the favourable one
would be optimistic, and this codebase has been burned by optimistic fill assumptions
three times. Barriers are evaluated on the target's MID; the fill is then taken at the
passive side, matching the base cost policy (aggressive in, passive out).

`tp_bps`/`sl_bps`/`max_hold_s` are each optional — None disables that barrier, so
(None, None, None) reduces exactly to between-signal behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.price_cache import load_target_prices
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.framework.schedule import add_jst_columns

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

_COLS = ["ts", "direction", "strength", "entry_exec_ts", "entry_price",
         "exit_exec_ts", "exit_price", "return_bps", "holding_seconds", "exit_reason"]


@dataclass(frozen=True)
class ContinuousBarrierReturn:
    tp_bps: float | None = None       # take-profit, bps in favour of the position
    sl_bps: float | None = None       # stop-loss, bps against
    max_hold_s: float | None = None   # timeout
    fill_delay_seconds: float = 0.0   # entries already carry the clock delay upstream

    @property
    def params(self) -> dict:
        return {"return_calc": "continuous_barrier", "tp_bps": self.tp_bps,
                "sl_bps": self.sl_bps, "max_hold_s": self.max_hold_s}

    def compute(self, signals: pd.DataFrame, target: MarketConfig, start_date: str,
                end_date: str, data_root: Path = _DEFAULT_DATA_ROOT) -> pd.DataFrame:
        mp = load_target_prices(target.exchange, target.symbol, target.execution_size,
                                start_date, end_date, data_root)
        n_mp = len(mp.ts)
        sig = signals.sort_values("ts", kind="stable").reset_index(drop=True)
        if len(sig) == 0 or n_mp == 0:
            return add_jst_columns(pd.DataFrame(columns=_COLS),
                                   ts_col="entry_exec_ts", date_ts_col="ts")

        ts, bid, ask, mid = mp.ts, mp.bid, mp.ask, mp.mid
        s_ts = sig["ts"].to_numpy(dtype=float)
        d_all = sig["direction"].to_numpy(dtype=float)
        x_ts = sig["exit_ts"].to_numpy(dtype=float)
        strength = sig["strength"].to_numpy() if "strength" in sig else np.abs(d_all)
        i_in = mp.first_at_or_after(s_ts + self.fill_delay_seconds)

        rows = []
        for k in range(len(sig)):
            a = int(i_in[k])
            if a >= n_mp or not np.isfinite(x_ts[k]):
                continue
            d = d_all[k]
            # The run's natural end, capped by the timeout.
            hard = x_ts[k] if self.max_hold_s is None else min(x_ts[k], ts[a] + self.max_hold_s)
            b = int(min(np.searchsorted(ts, hard, side="left"), n_mp - 1))
            if b <= a:
                continue

            m0 = mid[a]
            j, reason = b, ("timeout" if (self.max_hold_s is not None
                                          and hard < x_ts[k]) else "direction")
            if self.tp_bps is not None or self.sl_bps is not None:
                seg = (mid[a:b + 1] - m0) / m0 * 1e4 * d      # signed move in our favour
                hit_sl = (np.flatnonzero(seg <= -self.sl_bps) if self.sl_bps is not None
                          else np.empty(0, dtype=int))
                hit_tp = (np.flatnonzero(seg >= self.tp_bps) if self.tp_bps is not None
                          else np.empty(0, dtype=int))
                first_sl = a + int(hit_sl[0]) if hit_sl.size else None
                first_tp = a + int(hit_tp[0]) if hit_tp.size else None
                if first_sl is not None and (first_tp is None or first_sl <= first_tp):
                    j, reason = first_sl, "stop"       # ties go to the stop, never the TP
                elif first_tp is not None:
                    j, reason = first_tp, "take_profit"

            entry = ask[a] if d > 0 else bid[a]
            exit_ = bid[j] if d > 0 else ask[j]
            rows.append({
                "ts": s_ts[k], "direction": d, "strength": strength[k],
                "entry_exec_ts": ts[a], "entry_price": entry,
                "exit_exec_ts": ts[j], "exit_price": exit_,
                "return_bps": d * (exit_ - entry) / entry * 1e4,
                "holding_seconds": ts[j] - ts[a], "exit_reason": reason,
            })
        trades = pd.DataFrame(rows) if rows else pd.DataFrame(columns=_COLS)
        return add_jst_columns(trades, ts_col="entry_exec_ts", date_ts_col="ts")
