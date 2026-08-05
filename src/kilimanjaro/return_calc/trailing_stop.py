"""Trailing (moving) stop return — the fast Kilimanjaro read of Aquila v3.

Mirror of `potential-panda-aquila`'s v3 exit: a moving stop that ratchets on each new
same-side signal so a winning position stays open to capture more of the move, while
still capping the downside.

Per trade (entries are naturally non-overlapping — the next entry is the first fire
after the previous exit):
  - enter at the first fire of an episode; the stop reference `anchor` starts at the
    entry mid.
  - as the scan walks forward, on every second that carries a SAME-side fire
    (|z| >= threshold, matching direction) the anchor ratchets in the favourable
    direction toward the current mid (up for BUY, down for SELL).
  - **no time cap** — a same-side fire never closes the position (it only ratchets the
    stop), so the position is held for as long as the trend keeps confirming. It exits
    on exactly two conditions:
      - 'stop': the mid retraces `barrier_bps` from the anchor (the moving stop, whose
        initial value caps the downside at entry ∓ barrier), or
      - 'flip': a second carries an OPPOSITE-side fire (|z| >= threshold, other sign) —
        the signal now says the other way, so we reverse out.
    (`time_cap_seconds` is retained only as a far safety bound on the walk; set it large
    or None so it is not the binding exit — the design is stop/flip only.)

Cost policy (framework/costs.py): entry aggressive (ask BUY / bid SELL), exit passive
(bid BUY / ask SELL); the flat latency slippage is added upstream. Barriers are tested
on mid; execution is at bid/ask at the resolving tick. This does NOT create edge — it
reshapes the signal's existing directional drift, letting winners run past a fixed
take-profit while the trailing stop protects gains.
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


@dataclass(frozen=True)
class TrailingStopReturn:
    """Moving stop ratcheted on same-side signals; exit on trailing retrace or time.

    barrier_bps      : trailing distance (and initial downside cap), bps of the anchor.
    time_cap_seconds : the vertical barrier — max holding time.
    """

    barrier_bps: float
    time_cap_seconds: int | None = None  # far safety bound only; stop/flip are the exits
    non_overlapping: bool = True  # trades are one-at-a-time by construction

    @property
    def overlap_window_s(self) -> float:
        return 0.0 if self.non_overlapping else float(self.time_cap_seconds or 0)

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
        """Input signals: [ts, direction, strength] (ALL fires, not decimated — the
        subsequent same-side fires ratchet the stop). Returns one row per trade."""
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
        mid = mp.mid  # materialize once
        mp_secs = np.floor(mp.ts).astype(np.int64)

        # seconds carrying a same-side / opposite-side fire
        fire_secs = np.floor(ts).astype(np.int64)
        buy_fire = set(fire_secs[direction > 0].tolist())
        sell_fire = set(fire_secs[direction < 0].tolist())

        entry_idx = mp.first_at_or_after(ts)
        capped = self.time_cap_seconds is not None
        cap_idx = (
            np.clip(mp.first_at_or_after(ts + self.time_cap_seconds), 0, n_mp - 1)
            if capped
            else None
        )
        b = self.barrier_bps / 10000.0

        rows: list[dict] = []
        last_exit_ts = -np.inf
        for i in range(len(sig)):
            if ts[i] <= last_exit_ts:  # inside a still-open trade -> non-overlap skip
                continue
            e = int(entry_idx[i])
            if e >= n_mp:
                continue
            d = direction[i]
            same = buy_fire if d > 0 else sell_fire  # confirming -> ratchet
            opp = sell_fire if d > 0 else buy_fire    # reversal -> flip out
            # No time cap: walk to end of data; time_cap (if set) is only a far safety.
            hi = int(cap_idx[i]) if capped else n_mp - 1
            anchor = mid[e]
            exit_j = hi
            reason = "time" if capped else "eod"
            for j in range(e, hi + 1):
                s = int(mp_secs[j])
                if s in same:  # confirming same-side signal -> ratchet, keep open
                    anchor = max(anchor, mid[j]) if d > 0 else min(anchor, mid[j])
                elif s in opp:  # opposite-side signal -> reverse out
                    exit_j, reason = j, "flip"
                    break
                if d > 0:
                    if mid[j] <= anchor * (1.0 - b):
                        exit_j, reason = j, "stop"
                        break
                else:
                    if mid[j] >= anchor * (1.0 + b):
                        exit_j, reason = j, "stop"
                        break

            entry_price = mp.ask[e] if d > 0 else mp.bid[e]
            exit_price = mp.bid[exit_j] if d > 0 else mp.ask[exit_j]
            rows.append({
                "ts": ts[i],
                "direction": d,
                "strength": strength[i],
                "entry_exec_ts": mp.ts[e],
                "entry_price": entry_price,
                "exit_exec_ts": mp.ts[exit_j],
                "exit_price": exit_price,
                "return_bps": d * (exit_price - entry_price) / entry_price * 10000.0,
                "holding_seconds": mp.ts[exit_j] - mp.ts[e],
                "exit_reason": reason,
            })
            last_exit_ts = mp.ts[exit_j]

        trades = pd.DataFrame(rows) if rows else _empty()
        if "episode" in sig.columns and rows:
            pass  # episode id not meaningful for trailing trades; omit
        return add_jst_columns(trades, ts_col="entry_exec_ts", date_ts_col="ts")


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ts", "direction", "strength", "entry_exec_ts", "entry_price",
            "exit_exec_ts", "exit_price", "return_bps", "holding_seconds", "exit_reason",
        ]
    )
