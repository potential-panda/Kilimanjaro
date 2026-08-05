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

non_overlapping (default True): **one position at a time, re-entering on the first
fire AFTER the previous trade's actual exit** — exactly how the live strategy /
Himalaya behaves (a barrier that hits at +50 s frees a re-entry at +51 s, not after a
fixed time_cap). Fires that land inside a still-open trade are skipped. This replaced
the old fixed time_cap decimation, which left the scanner idle for the rest of the cap
after every early exit and so under-counted trades vs the live strategy. Trades are
still non-overlapping in time, so the plain statistics stay honest. (non_overlapping=
False keeps every fire as an overlapping trade, as before.)
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


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ts", "direction", "strength", "entry_exec_ts", "entry_price",
            "exit_exec_ts", "exit_price", "return_bps", "holding_seconds",
        ]
    )


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
        n_mp = len(mp.ts)
        if len(sig) == 0 or n_mp == 0:
            return add_jst_columns(_empty(), ts_col="entry_exec_ts", date_ts_col="ts")

        ts = sig["ts"].to_numpy(dtype=float)
        direction = sig["direction"].to_numpy(dtype=float)
        strength = sig["strength"].to_numpy()
        episode = sig["episode"].to_numpy() if "episode" in sig.columns else None
        mid = mp.mid  # materialize once (property recomputes each access)

        entry_idx = mp.first_at_or_after(ts)
        cap_idx = np.clip(mp.first_at_or_after(ts + self.time_cap_seconds), 0, n_mp - 1)
        b = self.barrier_bps / 10000.0
        SENT = np.iinfo(np.int64).max

        rows: list[dict] = []
        last_exit_ts = -np.inf
        for i in range(len(sig)):
            # ONE position at a time: skip fires inside a still-open trade, then
            # re-enter on the first fire AFTER the previous exit (matches the live
            # strategy — an early barrier exit frees an immediate re-entry).
            if self.non_overlapping and ts[i] <= last_exit_ts:
                continue
            e = int(entry_idx[i])
            if e >= n_mp:
                continue
            d = direction[i]
            hi = int(cap_idx[i])
            if hi <= e:
                exit_j = e
            else:
                wm = mid[e : hi + 1]
                m0 = mid[e]
                up = np.flatnonzero(wm >= m0 * (1.0 + b))
                dn = np.flatnonzero(wm <= m0 * (1.0 - b))
                first_up = up[0] if up.size else SENT
                first_dn = dn[0] if dn.size else SENT
                off = min(first_up, first_dn)
                exit_j = e + (hi - e if off == SENT else off)  # time cap else barrier

            entry_price = mp.ask[e] if d > 0 else mp.bid[e]
            exit_price = mp.bid[exit_j] if d > 0 else mp.ask[exit_j]
            row = {
                "ts": ts[i],
                "direction": d,
                "strength": strength[i],
                "entry_exec_ts": mp.ts[e],
                "entry_price": entry_price,
                "exit_exec_ts": mp.ts[exit_j],
                "exit_price": exit_price,
                "return_bps": d * (exit_price - entry_price) / entry_price * 10000.0,
                "holding_seconds": mp.ts[exit_j] - mp.ts[e],
            }
            if episode is not None:
                row["episode"] = episode[i]
            rows.append(row)
            if self.non_overlapping:
                last_exit_ts = mp.ts[exit_j]

        trades = pd.DataFrame(rows) if rows else _empty()
        return add_jst_columns(trades, ts_col="entry_exec_ts", date_ts_col="ts")
