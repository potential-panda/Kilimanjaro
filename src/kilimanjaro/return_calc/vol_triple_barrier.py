"""Volatility-scaled triple-barrier return.

Where `TripleBarrierReturn` uses a *fixed* bps barrier, this sets the take-profit/
stop **per entry** as a multiple of local volatility, floored by cost:

    barrier_i = max( k · σ_T(t_i) ,  cost_floor_bps )

σ_T(t_i) is the target-mid volatility over the horizon T, estimated causally from a
trailing window at entry t_i. The motivation (doc/triple-barrier-vol-spread.md): a
fixed bps barrier is a different strategy in every vol regime, and its best width is
window-unstable — tight (±10) wins Sharpe on train, mid-range on holdout. An
adaptive barrier widens only when local vol justifies it, so it can chase the
big-move runs (the avg-net upside of a wide barrier) without paying the blanket
drawdown a wide *fixed* barrier incurs in quiet periods.

Everything else matches TripleBarrierReturn: barrier test on mid; entry aggressive
(ask BUY / bid SELL), exit passive; the flat latency slippage is added upstream;
non-overlap by time_cap. `cost_floor_bps` guarantees the take-profit target clears
the round-trip cost (spread + latency) so a barrier winner is net-positive — set it
≈ the venue's median spread + latency.

Causality: σ at entry uses the rolling std of 1s log-returns up to the **previous
fully-closed second**, so no part of the entry second (or later) leaks in.
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
class VolScaledTripleBarrierReturn:
    """Triple barrier whose ±barrier = max(k·σ_T, cost_floor_bps) per entry.

    k                    : volatility multiple (barrier in units of σ over T).
    time_cap_seconds     : vertical barrier / horizon T (σ is scaled to this).
    vol_lookback_seconds : trailing window for the 1s realized-vol estimate.
    cost_floor_bps       : minimum barrier (≈ median spread + latency) so a
                           take-profit winner clears the round-trip cost.
    """

    k: float
    time_cap_seconds: int
    vol_lookback_seconds: int = 1800
    cost_floor_bps: float = 0.0
    non_overlapping: bool = True

    @property
    def overlap_window_s(self) -> float:
        return 0.0 if self.non_overlapping else float(self.time_cap_seconds)

    @property
    def params(self) -> dict:
        return {
            "k": self.k,
            "time_cap_s": self.time_cap_seconds,
            "vol_lookback_s": self.vol_lookback_seconds,
            "cost_floor_bps": self.cost_floor_bps,
        }

    def _sigma_bps_by_second(self, mp) -> tuple[np.ndarray, int]:
        """Per-second σ over the horizon, in bps: rolling std of 1s log-returns of
        target mid, scaled by √time_cap. Returns (sigma_bps, base_second) where
        sigma_bps[s - base_second] is the vol as of the *close* of second s."""
        sec = np.floor(mp.ts).astype(np.int64)
        last = pd.Series(mp.mid, index=sec).groupby(level=0).last()
        base, top = int(last.index[0]), int(last.index[-1])
        grid = np.arange(base, top + 1)
        mid1s = last.reindex(grid).ffill().to_numpy(dtype=float)
        logret = np.diff(np.log(mid1s), prepend=np.log(mid1s[0]))
        win = self.vol_lookback_seconds
        sig = (
            pd.Series(logret)
            .rolling(win, min_periods=max(2, win // 4))
            .std()
            .to_numpy()
        )
        return sig * np.sqrt(self.time_cap_seconds) * 1e4, base

    def _entry_barriers_bps(self, ts: np.ndarray, mp) -> np.ndarray:
        """Per-entry barrier bps = max(k·σ_T(prev second), cost_floor). NaN σ
        (warmup) falls back to the cost floor."""
        sigma_bps, base = self._sigma_bps_by_second(mp)
        n = len(sigma_bps)
        # previous fully-closed second -> strictly causal
        idx = np.clip(np.floor(ts).astype(np.int64) - base - 1, 0, max(n - 1, 0))
        sig = sigma_bps[idx]
        scaled = self.k * sig
        scaled = np.where(np.isfinite(scaled), scaled, self.cost_floor_bps)
        return np.maximum(scaled, self.cost_floor_bps)

    def compute(
        self,
        signals: pd.DataFrame,
        target: MarketConfig,
        start_date: str,
        end_date: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
    ) -> pd.DataFrame:
        """Input signals: [ts, direction, strength]. Returns one row per trade with
        the same columns as TripleBarrierReturn plus `barrier_bps` (the realized
        per-entry barrier)."""
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
        mid = mp.mid

        entry_idx = mp.first_at_or_after(ts)
        has_entry = entry_idx < n_mp
        e = np.clip(entry_idx, 0, max(n_mp - 1, 0))
        cap = np.clip(mp.first_at_or_after(ts + self.time_cap_seconds), 0, max(n_mp - 1, 0))

        barrier_bps = self._entry_barriers_bps(ts, mp)
        b = barrier_bps / 10000.0
        SENT = np.iinfo(np.int64).max
        exit_i = e.copy()
        for i in range(len(sig)):
            lo, hi = int(e[i]), int(cap[i])
            if not has_entry[i] or hi <= lo:
                continue
            bi = b[i]
            if bi <= 0:  # degenerate barrier -> time cap only
                exit_i[i] = hi
                continue
            wm = mid[lo : hi + 1]
            m0 = mid[lo]
            up = np.flatnonzero(wm >= m0 * (1.0 + bi))
            dn = np.flatnonzero(wm <= m0 * (1.0 - bi))
            first_up = up[0] if up.size else SENT
            first_dn = dn[0] if dn.size else SENT
            off = min(first_up, first_dn)
            exit_i[i] = lo + (hi - lo if off == SENT else off)

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
                "barrier_bps": barrier_bps,
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
