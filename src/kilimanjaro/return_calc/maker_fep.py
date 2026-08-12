"""Maker (limit-order) return calc — the FEP-style offline model of a resting order.

Taker crosses the spread → fill is a lookup. A maker rests → the fill is conditional
(the book must cross to you), delayed, and — in reality — queue-contended. This models it.

FILL CONTRACT — mirrors qate's simulator (exchange/simulator/gateway.py::match_maker_order)
so scan↔Himalaya calibration holds, exactly as `fill_delay` mirrored the taker fill:
  BUY  limit@P fills when the ASK drops to  P - price_unit  → fill at that ask (price improve)
  SELL limit@P fills when the BID rises to  P + price_unit  → fill at that bid
evaluated per tick on the target's bid/ask (price_cache). No queue model (qate has none) —
so this is OPTIMISTIC on queue position; that's the contract we must match, not exceed.

STRUCTURE — from richmanbtc/mlbot_tutorial's Force-Entry-Price idea:
  - post the entry limit a distance `offset_bps` from the mid (0 = at the mid);
  - wait up to `fill_window_s`; if the book never crosses → NO TRADE (the honest treatment
    of the misses — you didn't participate, no loss);
  - on fill, hold `hold_s`, then post the exit limit `offset_bps` the other side of the mid;
  - wait up to `exit_window_s`; if the exit never fills, force-close as a TAKER at the touch
    (a resting order can't be held forever) and charge the taker fee on that leg.

ECONOMICS: return_bps is the full maker P&L per round-trip — spread captured by the passive
fills, MINUS fees. `maker_fee_bps` is per fill and NEGATIVE for a rebate (e.g. -1 GMO SPOT,
-2 BITBANK); `taker_fee_bps` applies only to a forced exit. `long_only` (spot venues) skips
SELL signals. The break-even the whole maker thesis rests on is explicit in the number:
  (spread captured + rebate) − adverse_selection − forced-exit tolls > 0 ?

NOT YET CALIBRATED: like every mechanism, trust it only after an exact-match against a
Himalaya maker run (a few param sets). Until then it is direction+magnitude, not gospel.
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
    return pd.DataFrame(columns=[
        "ts", "direction", "strength", "entry_exec_ts", "entry_price",
        "exit_exec_ts", "exit_price", "return_bps", "holding_seconds",
        "filled", "exit_forced",
    ])


@dataclass
class MakerFEPReturn:
    offset_bps: float = 0.0        # post distance from mid (0 = at the mid; >0 = further out)
    fill_window_s: float = 30.0    # max wait for the entry maker fill; else no trade
    hold_s: float = 30.0           # hold after entry fill before posting the exit
    exit_window_s: float = 60.0    # max wait for the exit maker fill; else force-take
    maker_fee_bps: float = 0.0     # PER-FILL fee; NEGATIVE = rebate (GMO SPOT -1, BITBANK -2)
    taker_fee_bps: float = 5.0     # per-fill fee on a FORCED exit (GMO SPOT 5, BITBANK 12)
    long_only: bool = False        # spot venues: skip SELL (short) signals
    price_unit: float = 0.0        # tick; qate fills at ask<=P-price_unit (0 = ask<=P)
    non_overlapping: bool = True

    @property
    def params(self) -> dict:
        return {
            "return_calc": "maker_fep", "offset_bps": self.offset_bps,
            "fill_window_s": self.fill_window_s, "hold_s": self.hold_s,
            "exit_window_s": self.exit_window_s, "maker_fee_bps": self.maker_fee_bps,
            "taker_fee_bps": self.taker_fee_bps, "long_only": self.long_only,
        }

    def compute(self, signals: pd.DataFrame, target: MarketConfig,
                start_date: str, end_date: str,
                data_root: Path = _DEFAULT_DATA_ROOT) -> pd.DataFrame:
        mp = load_target_prices(target.exchange, target.symbol, target.execution_size,
                                start_date, end_date, data_root)
        n_mp = len(mp.ts)
        sig = signals.sort_values("ts", kind="stable").reset_index(drop=True)
        if len(sig) == 0 or n_mp == 0:
            return add_jst_columns(_empty(), ts_col="entry_exec_ts", date_ts_col="ts")

        ts, bid, ask, mid = mp.ts, mp.bid, mp.ask, mp.mid
        sig_ts = sig["ts"].to_numpy(dtype=float)
        direction = sig["direction"].to_numpy(dtype=float)
        strength = sig["strength"].to_numpy()
        pu = self.price_unit
        post_idx = mp.first_at_or_after(sig_ts)  # tick we post the entry limit on

        rows: list[dict] = []
        last_exit_ts = -np.inf
        for i in range(len(sig)):
            d = direction[i]
            if self.long_only and d < 0:
                continue
            e0 = int(post_idx[i])
            if e0 >= n_mp:
                continue
            if self.non_overlapping and ts[e0] <= last_exit_ts:
                continue

            m0 = mid[e0]
            off = self.offset_bps / 10000.0
            # ── entry: post a passive limit, wait for the book to cross it ──
            hi_e = self._idx_at(ts, ts[e0] + self.fill_window_s, n_mp)
            entry_fill = self._maker_fill(d, m0 * (1 - off) if d > 0 else m0 * (1 + off),
                                          e0, hi_e, ask, bid, pu)
            if entry_fill is None:  # book never crossed within the window → no trade
                continue
            ej, entry_price = entry_fill

            # ── hold, then post the exit limit; force-take if it doesn't fill ──
            x0 = self._idx_at(ts, ts[ej] + self.hold_s, n_mp)
            hi_x = self._idx_at(ts, ts[x0] + self.exit_window_s, n_mp)
            xm = mid[x0]
            xd = -d  # exit is the opposite side
            exit_fill = self._maker_fill(xd, xm * (1 - off) if xd > 0 else xm * (1 + off),
                                         x0, hi_x, ask, bid, pu)
            if exit_fill is not None:
                xj, exit_price = exit_fill
                exit_forced = False
                exit_fee = self.maker_fee_bps
            else:  # forced close as a taker at the touch (buy@ask / sell@bid)
                xj = min(hi_x, n_mp - 1)
                exit_price = ask[xj] if xd > 0 else bid[xj]
                exit_forced = True
                exit_fee = self.taker_fee_bps

            gross = d * (exit_price - entry_price) / entry_price * 10000.0
            net = gross - self.maker_fee_bps - exit_fee  # entry always maker; exit maker|taker
            rows.append({
                "ts": sig_ts[i], "direction": d, "strength": strength[i],
                "entry_exec_ts": ts[ej], "entry_price": entry_price,
                "exit_exec_ts": ts[xj], "exit_price": exit_price,
                "return_bps": net, "holding_seconds": ts[xj] - ts[ej],
                "filled": True, "exit_forced": exit_forced,
            })
            if self.non_overlapping:
                last_exit_ts = ts[xj]

        trades = pd.DataFrame(rows) if rows else _empty()
        return add_jst_columns(trades, ts_col="entry_exec_ts", date_ts_col="ts")

    @staticmethod
    def _idx_at(ts: np.ndarray, when: float, n_mp: int) -> int:
        return int(min(np.searchsorted(ts, when, side="left"), n_mp - 1))

    @staticmethod
    def _maker_fill(d: float, limit: float, lo: int, hi: int,
                    ask: np.ndarray, bid: np.ndarray, pu: float):
        """qate contract: BUY fills at the first tick ask<=limit-pu (fill=ask);
        SELL fills at the first tick bid>=limit+pu (fill=bid). None if no cross in [lo,hi]."""
        if d > 0:
            cross = np.flatnonzero(ask[lo:hi + 1] <= limit - pu)
            if cross.size:
                j = lo + int(cross[0])
                return j, ask[j]
        else:
            cross = np.flatnonzero(bid[lo:hi + 1] >= limit + pu)
            if cross.size:
                j = lo + int(cross[0])
                return j, bid[j]
        return None
