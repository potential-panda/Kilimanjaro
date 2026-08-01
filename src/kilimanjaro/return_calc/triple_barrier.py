"""Triple-barrier return: exit on price move, not the clock.

Where FixedHorizonReturn exits after a fixed time and EpisodeReturn exits when
the signal condition clears, TripleBarrierReturn (López de Prado's triple
barrier, used here as an *execution* rule) exits at whichever comes first:

    take-profit  : mid moves +barrier_bps from entry
    stop-loss    : mid moves -barrier_bps from entry   (symmetric)
    vertical/time: time_cap_seconds elapse             (the fallback)

Motivation for this repo: our signal's directional edge concentrates in *big*
moves (event days far out-predict quiet ones), while a fixed clock dilutes those
winners with many sub-spread small-move trades. A price barrier preferentially
resolves the big-move trades (they hit ±barrier) and lets small-move trades time
out near breakeven — an *exit-side* selection for the regime the signal is good
at. It does NOT create edge (for a driftless path E[return]=0 by optional
stopping); it reshapes the existing directional edge, and only wins when the
signal's drift makes the +barrier hit more often than the −barrier, by enough to
clear the spread. Set barrier_bps > the round-trip spread so winners clear it.

Cost policy (framework/costs.py): entry at the aggressive side (ask BUY / bid
SELL), exit at the passive side (bid BUY / ask SELL) — spread already inside the
return; the flat latency slippage is added upstream. The barrier test is on
**mid**; execution is at bid/ask at the resolving tick.

non_overlapping (default True): decimate fired signals to a time_cap-spaced
subset first, so trades don't overlap and the plain statistics are honest
(mirrors FixedHorizonReturn / doc/overlapping-returns.md). Because holding time
is variable and capped at time_cap, spacing entries by time_cap bounds overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.price_cache import load_target_prices
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.framework.schedule import add_jst_columns
from kilimanjaro.return_calc.fixed_horizon import _nonoverlap_indices

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")


@dataclass(frozen=True)
class TripleBarrierReturn:
    """Exit at the first of ±barrier_bps (on mid) or time_cap_seconds.

    barrier_bps      : symmetric take-profit / stop distance, in bps of entry mid.
    time_cap_seconds : the vertical barrier — max holding time.
    """

    barrier_bps: float
    time_cap_seconds: int
    non_overlapping: bool = True

    @property
    def overlap_window_s(self) -> float:
        return 0.0 if self.non_overlapping else float(self.time_cap_seconds)

    @property
    def params(self) -> dict:
        return {"barrier_bps": self.barrier_bps, "time_cap_s": self.time_cap_seconds}

    def compute(
        self,
        signals: pd.DataFrame,
        target: MarketConfig,
        start_date: str,
        end_date: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
    ) -> pd.DataFrame:
        """Input signals: [ts, direction, strength]. Returns one row per trade
        with [ts, direction, strength, entry_exec_ts, entry_price, exit_exec_ts,
        exit_price, return_bps, holding_seconds, jst_*]."""
        mp = load_target_prices(
            target.exchange, target.symbol, target.execution_size,
            start_date, end_date, data_root,
        )
        sig = signals.sort_values("ts", kind="stable").reset_index(drop=True)
        if self.non_overlapping and len(sig) > 1:
            keep = _nonoverlap_indices(
                sig["ts"].to_numpy(dtype=float), float(self.time_cap_seconds)
            )
            sig = sig.iloc[keep].reset_index(drop=True)

        ts = sig["ts"].to_numpy(dtype=float)
        direction = sig["direction"].to_numpy()
        n_mp = len(mp.ts)
        mid = mp.mid  # materialize once (property recomputes each access)

        entry_idx = mp.first_at_or_after(ts)
        has_entry = entry_idx < n_mp
        e = np.clip(entry_idx, 0, max(n_mp - 1, 0))
        cap = np.clip(mp.first_at_or_after(ts + self.time_cap_seconds), 0, max(n_mp - 1, 0))

        b = self.barrier_bps / 10000.0
        SENT = np.iinfo(np.int64).max
        exit_i = e.copy()
        for i in range(len(sig)):
            lo, hi = int(e[i]), int(cap[i])
            if not has_entry[i] or hi <= lo:
                continue
            wm = mid[lo : hi + 1]
            m0 = mid[lo]
            up = np.flatnonzero(wm >= m0 * (1.0 + b))
            dn = np.flatnonzero(wm <= m0 * (1.0 - b))
            first_up = up[0] if up.size else SENT
            first_dn = dn[0] if dn.size else SENT
            off = min(first_up, first_dn)
            exit_i[i] = lo + (hi - lo if off == SENT else off)  # time cap else barrier

        entry_price = np.where(
            has_entry, np.where(direction > 0, mp.ask[e], mp.bid[e]), np.nan
        )
        exit_price = np.where(
            has_entry, np.where(direction > 0, mp.bid[exit_i], mp.ask[exit_i]), np.nan
        )
        trades = pd.DataFrame(
            {
                "ts": ts,
                "direction": direction,
                "strength": sig["strength"].to_numpy(),
                "entry_exec_ts": np.where(has_entry, mp.ts[e], np.nan),
                "entry_price": entry_price,
                "exit_exec_ts": np.where(has_entry, mp.ts[exit_i], np.nan),
                "exit_price": exit_price,
                "return_bps": direction * (exit_price - entry_price) / entry_price * 10000.0,
                "holding_seconds": np.where(has_entry, mp.ts[exit_i] - mp.ts[e], np.nan),
            }
        )
        if "episode" in sig.columns:
            trades["episode"] = sig["episode"].to_numpy()
        trades = trades[has_entry].reset_index(drop=True)
        return add_jst_columns(trades, ts_col="entry_exec_ts", date_ts_col="ts")
