"""Episode return: one non-overlapping trade per signal episode.

Where FixedHorizonReturn measures a fixed look-ahead from *every* active
second — so neighbouring observations overlap and inflate significance (see
doc/overlapping-returns.md) — EpisodeReturn measures the trade the episode
actually implies:

    enter at the crossing second (episode start),
    hold while the condition persists,
    exit when it clears — capped at max_hold_seconds.

This yields exactly ONE return per episode, non-overlapping by construction, so
the plain t-test is legitimate again and `n` equals the real event count. It is
also the natural home for realistic bid/ask execution (enter at the aggressive
side, exit at the passive side), the spread-aware test a mid-to-mid screen
cannot give.

Requires the signal to carry an `episode` column (ExpressionSignal and
emit it via op.age.threshold_episode). Rows sharing an episode id
are one continuous run on the 1s grid; entry = first, natural exit = one grid
step past the last active second.

BUY (direction=+1): enter at ask, exit at bid.
SELL (direction=-1): enter at bid, exit at ask.

Prices come from the shared per-window target-price cache and are resolved with
searchsorted — same "first MarketPrice tick at or after ts" semantics as the
other return calcs.
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

# One 1s grid step: the episode clears one second after its last active second.
_GRID_STEP_S = 1.0


@dataclass(frozen=True)
class EpisodeReturn:
    """Collapse each signal episode into one trade held for its duration.

    entry_price: ask (BUY) / bid (SELL) at first tick >= episode start ts
    exit_price:  bid (BUY) / ask (SELL) at first tick >= exit ts
    exit ts      = min(last_active_ts + 1s, entry_ts + max_hold_seconds)
    return_bps   = direction × (exit_price - entry_price) / entry_price × 10000
    holding_seconds = exit_exec_ts - entry_exec_ts

    max_hold_seconds : cap on holding time; None = hold to natural episode end.
    """

    max_hold_seconds: int | None = None

    @property
    def overlap_window_s(self) -> float:
        """Zero: episodes are non-overlapping by construction, so n is already
        the count of independent events."""
        return 0.0

    @property
    def params(self) -> dict:
        return {
            "max_hold_s": self.max_hold_seconds
            if self.max_hold_seconds is not None
            else -1
        }

    def compute(
        self,
        signals: pd.DataFrame,
        target: MarketConfig,
        start_date: str,
        end_date: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
    ) -> pd.DataFrame:
        """Collapse episodes and compute per-episode bid/ask returns.

        Input signals must have [ts, direction, strength, episode].
        Returns [ts, direction, strength, episode, entry_exec_ts, entry_price,
                 exit_ts, exit_exec_ts, exit_price, return_bps, holding_seconds,
                 jst_hour, jst_weekday, jst_date] — one row per episode.
        """
        if "episode" not in signals.columns:
            raise ValueError(
                "EpisodeReturn needs an 'episode' column; use a signal that "
                "emits episodes (ExpressionSignal)."
            )
        mp = load_target_prices(
            target.exchange,
            target.symbol,
            target.execution_size,
            start_date,
            end_date,
            data_root,
        )
        sig = signals.sort_values("ts", kind="stable")

        # Collapse each episode to (start, end, entry direction/strength).
        # Direction/strength are taken at the episode's first (crossing) second.
        g = sig.groupby("episode", sort=True)
        episode = g["ts"].first().index.to_numpy()
        entry_ts = g["ts"].first().to_numpy()
        last_ts = g["ts"].last().to_numpy()
        direction = g["direction"].first().to_numpy()
        strength = g["strength"].first().to_numpy()

        exit_ts = last_ts + _GRID_STEP_S
        if self.max_hold_seconds is not None:
            exit_ts = np.minimum(exit_ts, entry_ts + self.max_hold_seconds)

        n_mp = len(mp.ts)

        entry_idx = mp.first_at_or_after(entry_ts)
        has_entry = entry_idx < n_mp
        e = np.clip(entry_idx, 0, max(n_mp - 1, 0))
        entry_exec_ts = np.where(has_entry, mp.ts[e], np.nan)
        entry_price = np.where(
            has_entry, np.where(direction > 0, mp.ask[e], mp.bid[e]), np.nan
        )

        exit_idx = mp.first_at_or_after(exit_ts)
        has_exit = exit_idx < n_mp
        x = np.clip(exit_idx, 0, max(n_mp - 1, 0))
        exit_exec_ts = np.where(has_exit, mp.ts[x], np.nan)
        exit_price = np.where(
            has_exit, np.where(direction > 0, mp.bid[x], mp.ask[x]), np.nan
        )

        trades = pd.DataFrame(
            {
                "ts": entry_ts,
                "direction": direction,
                "strength": strength,
                "episode": episode,
                "entry_exec_ts": entry_exec_ts,
                "entry_price": entry_price,
                "exit_ts": exit_ts,
                "exit_exec_ts": exit_exec_ts,
                "exit_price": exit_price,
                "return_bps": direction
                * (exit_price - entry_price)
                / entry_price
                * 10000.0,
                "holding_seconds": exit_exec_ts - entry_exec_ts,
            }
        )
        # INNER on entry (no tick at/after start → drop); LEFT on exit (NaN → dropna at stats)
        trades = trades[has_entry].reset_index(drop=True)

        # jst from the entry execution instant (the decision/entry point)
        return add_jst_columns(trades, ts_col="entry_exec_ts", date_ts_col="ts")
