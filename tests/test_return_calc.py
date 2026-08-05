"""Return calcs on the in-memory price cache: the searchsorted joins must
reproduce the DuckDB ASOF semantics they replaced — first tick at or after
ts, INNER on entry, LEFT on exit. Synthetic prices only; the real-data
equivalence is asserted against recorded grid numbers (see commit message)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.data.price_cache import TargetPrices  # noqa: E402
from kilimanjaro.framework.protocol import MarketConfig  # noqa: E402
from kilimanjaro.return_calc.between_signal import BetweenSignalReturn  # noqa: E402
from kilimanjaro.return_calc.episode import EpisodeReturn  # noqa: E402
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn  # noqa: E402

TARGET = MarketConfig("GMO", "ETH_JPY", "0.3")

# ticks at t=10, 20, 30, 40; spread 2 around mids 100, 110, 120, 130
MP = TargetPrices(
    ts=np.array([10.0, 20.0, 30.0, 40.0]),
    bid=np.array([99.0, 109.0, 119.0, 129.0]),
    ask=np.array([101.0, 111.0, 121.0, 131.0]),
)


def _signals(rows):
    return pd.DataFrame(rows)


class TestTargetPrices(unittest.TestCase):
    def test_first_at_or_after(self):
        idx = MP.first_at_or_after(np.array([5.0, 10.0, 15.0, 40.0, 41.0, np.nan]))
        self.assertEqual(list(idx), [0, 0, 1, 3, 4, 4])
        # exact tick matches itself (ASOF >= semantics), 41 and NaN → no match


class TestBetweenSignalReturn(unittest.TestCase):
    def _compute(self, rows):
        with patch("kilimanjaro.return_calc.between_signal.load_target_prices",
                   return_value=MP):
            return BetweenSignalReturn().compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_buy_enters_ask_exits_bid_at_next_ticks(self):
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 1.0, "exit_ts": 25.0},
        ])
        r = out.iloc[0]
        self.assertEqual(r["entry_exec_ts"], 20.0)   # first tick >= 12
        self.assertEqual(r["entry_price"], 111.0)    # BUY → ask
        self.assertEqual(r["exit_exec_ts"], 30.0)    # first tick >= 25
        self.assertEqual(r["exit_price"], 119.0)     # BUY → bid
        self.assertAlmostEqual(
            r["return_bps"], (119.0 - 111.0) / 111.0 * 1e4)
        self.assertEqual(r["holding_seconds"], 10.0)

    def test_sell_enters_bid_exits_ask(self):
        out = self._compute([
            {"ts": 10.0, "direction": -1.0, "strength": -1.0, "exit_ts": 30.0},
        ])
        r = out.iloc[0]
        self.assertEqual(r["entry_price"], 99.0)     # SELL → bid, tick ts=10 itself
        self.assertEqual(r["exit_price"], 121.0)     # SELL → ask at ts=30
        self.assertAlmostEqual(
            r["return_bps"], -1 * (121.0 - 99.0) / 99.0 * 1e4)

    def test_last_flip_without_exit_kept_with_nan_return(self):
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 1.0, "exit_ts": np.nan},
        ])
        self.assertEqual(len(out), 1)                # LEFT join on exit
        self.assertTrue(np.isnan(out.iloc[0]["return_bps"]))
        self.assertTrue(np.isnan(out.iloc[0]["exit_price"]))

    def test_signal_after_last_tick_dropped(self):
        out = self._compute([
            {"ts": 50.0, "direction": 1.0, "strength": 1.0, "exit_ts": 60.0},
        ])
        self.assertEqual(len(out), 0)                # INNER join on entry

    def test_exit_beyond_data_is_nan_but_row_kept(self):
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 1.0, "exit_ts": 45.0},
        ])
        self.assertEqual(len(out), 1)
        self.assertTrue(np.isnan(out.iloc[0]["return_bps"]))


class TestFixedHorizonReturn(unittest.TestCase):
    def _compute(self, rows, horizon):
        with patch("kilimanjaro.return_calc.fixed_horizon.load_target_prices",
                   return_value=MP):
            return FixedHorizonReturn(horizon_seconds=horizon).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_buy_ask_to_bid_at_horizon(self):
        # bid/ask execution (spread paid): BUY enters ask, exits bid
        out = self._compute(
            [{"ts": 12.0, "direction": 1.0, "strength": 0.5}], horizon=15)
        r = out.iloc[0]
        self.assertEqual(r["entry_price"], 111.0)    # first tick >= 12 → ts=20, ask
        self.assertEqual(r["exit_price"], 119.0)     # first tick >= 27 → ts=30, bid
        self.assertAlmostEqual(r["return_bps"], (119 - 111) / 111 * 1e4)

    def test_sell_bid_to_ask(self):
        # SELL enters bid, exits ask — spread costs it in the opposite direction,
        # so it is NOT the exact negative of the BUY (that is the point of bid/ask)
        dn = self._compute([{"ts": 12.0, "direction": -1.0, "strength": -1.0}], 15)
        r = dn.iloc[0]
        self.assertEqual(r["entry_price"], 109.0)    # SELL → bid at ts=20
        self.assertEqual(r["exit_price"], 121.0)     # SELL → ask at ts=30
        self.assertAlmostEqual(r["return_bps"], -1 * (121 - 109) / 109 * 1e4)

    def test_horizon_beyond_data_nan(self):
        out = self._compute([{"ts": 35.0, "direction": 1.0, "strength": 1.0}], 100)
        self.assertEqual(len(out), 1)
        self.assertTrue(np.isnan(out.iloc[0]["return_bps"]))


class TestEpisodeReturn(unittest.TestCase):
    def _compute(self, rows, max_hold=None):
        with patch("kilimanjaro.return_calc.episode.load_target_prices",
                   return_value=MP):
            return EpisodeReturn(max_hold_seconds=max_hold).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_one_trade_per_episode_natural_exit(self):
        # Episode 1 spans ts 12,13 → enter @12 (ask), exit @ last+1 = 14 (bid).
        # Episode 2 is a single second at ts 22 → enter @22, exit @23.
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 2.0, "episode": 1},
            {"ts": 13.0, "direction": 1.0, "strength": 2.5, "episode": 1},
            {"ts": 22.0, "direction": -1.0, "strength": -2.0, "episode": 2},
        ])
        self.assertEqual(len(out), 2)                       # one row per episode
        r1 = out.iloc[0]
        self.assertEqual(r1["ts"], 12.0)                    # entry = crossing second
        self.assertEqual(r1["strength"], 2.0)               # strength taken at entry
        self.assertEqual(r1["entry_exec_ts"], 20.0)         # first tick >= 12
        self.assertEqual(r1["entry_price"], 111.0)          # BUY → ask
        self.assertEqual(r1["exit_ts"], 14.0)               # last active (13) + 1
        self.assertEqual(r1["exit_exec_ts"], 20.0)          # first tick >= 14 → ts=20
        self.assertEqual(r1["exit_price"], 109.0)           # BUY → bid
        r2 = out.iloc[1]
        self.assertEqual(r2["exit_ts"], 23.0)               # single-second episode → +1
        self.assertEqual(r2["entry_price"], 119.0)          # SELL @ ts30 tick → bid
        self.assertEqual(r2["exit_price"], 121.0)           # SELL exit → ask

    def test_max_hold_caps_exit(self):
        # Long episode ts 10..40; hold cap 12 → exit at 10+12 = 22, not 41.
        rows = [{"ts": float(t), "direction": 1.0, "strength": 1.0, "episode": 1}
                for t in range(10, 41)]
        out = self._compute(rows, max_hold=12)
        self.assertEqual(out.iloc[0]["exit_ts"], 22.0)

    def test_requires_episode_column(self):
        with self.assertRaises(ValueError):
            self._compute([{"ts": 12.0, "direction": 1.0, "strength": 1.0}])

    def test_signal_after_last_tick_dropped(self):
        out = self._compute([
            {"ts": 50.0, "direction": 1.0, "strength": 1.0, "episode": 1},
        ])
        self.assertEqual(len(out), 0)                       # INNER on entry


if __name__ == "__main__":
    unittest.main()


# --- Triple-barrier -------------------------------------------------------

from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn  # noqa: E402

# ticks every 1s at t=0..4; spread 2 (bid=mid-1, ask=mid+1)
def _mp(mids):
    mids = np.asarray(mids, dtype=float)
    return TargetPrices(ts=np.arange(len(mids), dtype=float),
                        bid=mids - 1.0, ask=mids + 1.0)


class TestTripleBarrierReturn(unittest.TestCase):
    def _compute(self, mids, rows, barrier_bps, time_cap):
        with patch("kilimanjaro.return_calc.triple_barrier.load_target_prices",
                   return_value=_mp(mids)):
            return TripleBarrierReturn(barrier_bps=barrier_bps,
                                       time_cap_seconds=time_cap).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_take_profit_exits_at_first_up_cross(self):
        # barrier 100bps of entry mid 100 = 101; mid jumps to 105 at t=2
        out = self._compute([100, 100.2, 105, 106, 107],
                            [{"ts": 0.0, "direction": 1.0, "strength": 2.0}],
                            barrier_bps=100, time_cap=100)
        r = out.iloc[0]
        self.assertEqual(r["entry_price"], 101.0)          # BUY enters ask@t0
        self.assertEqual(r["exit_exec_ts"], 2.0)           # first mid >= 101
        self.assertEqual(r["exit_price"], 104.0)           # BUY exits bid@t2
        self.assertAlmostEqual(r["return_bps"], (104.0 - 101.0) / 101.0 * 1e4)
        self.assertEqual(r["holding_seconds"], 2.0)

    def test_stop_exits_at_first_down_cross(self):
        # down barrier 99; mid drops to 94 at t=2
        out = self._compute([100, 99.5, 94, 90, 90],
                            [{"ts": 0.0, "direction": 1.0, "strength": 2.0}],
                            barrier_bps=100, time_cap=100)
        r = out.iloc[0]
        self.assertEqual(r["exit_exec_ts"], 2.0)
        self.assertEqual(r["exit_price"], 93.0)            # bid@t2
        self.assertLess(r["return_bps"], 0)

    def test_time_cap_when_no_barrier_hit(self):
        # never moves +-1%; vertical barrier at t=3
        out = self._compute([100, 100.1, 100.2, 100.3, 100.4],
                            [{"ts": 0.0, "direction": 1.0, "strength": 2.0}],
                            barrier_bps=100, time_cap=3)
        r = out.iloc[0]
        self.assertEqual(r["exit_exec_ts"], 3.0)           # capped, not t4
        self.assertEqual(r["holding_seconds"], 3.0)

    def test_non_overlapping_decimation_by_time_cap(self):
        # two signals 1s apart, time_cap 3 -> second is inside the first's window
        out = self._compute([100, 100, 100, 100, 100],
                            [{"ts": 0.0, "direction": 1.0, "strength": 1.0},
                             {"ts": 1.0, "direction": 1.0, "strength": 1.0}],
                            barrier_bps=100, time_cap=3)
        self.assertEqual(len(out), 1)                       # only the first kept


class TestVolScaledTripleBarrierReturn(unittest.TestCase):
    def test_barrier_widens_with_vol(self):
        from kilimanjaro.return_calc.vol_triple_barrier import VolScaledTripleBarrierReturn
        rc = VolScaledTripleBarrierReturn(
            k=2.0, time_cap_seconds=100, vol_lookback_seconds=10, cost_floor_bps=0.0
        )
        low = _mp([100 + 0.01 * (i % 2) for i in range(60)])   # tiny wiggle
        high = _mp([100 + 1.00 * (i % 2) for i in range(60)])  # big wiggle
        b_low = rc._entry_barriers_bps(np.array([50.0]), low)[0]
        b_high = rc._entry_barriers_bps(np.array([50.0]), high)[0]
        self.assertGreater(b_high, b_low)     # more vol -> wider barrier
        self.assertGreaterEqual(b_low, 0.0)

    def test_cost_floor_binds_when_vol_low(self):
        from kilimanjaro.return_calc.vol_triple_barrier import VolScaledTripleBarrierReturn
        rc = VolScaledTripleBarrierReturn(
            k=2.0, time_cap_seconds=100, vol_lookback_seconds=10, cost_floor_bps=5.0
        )
        low = _mp([100 + 0.001 * (i % 2) for i in range(60)])  # ~0 vol
        b = rc._entry_barriers_bps(np.array([50.0]), low)[0]
        self.assertAlmostEqual(b, 5.0)        # floor dominates a ~0-vol window

    def test_k_zero_reduces_to_fixed_floor_barrier(self):
        # k=0 -> barrier == cost_floor everywhere; behaves like a fixed barrier.
        from kilimanjaro.return_calc.vol_triple_barrier import VolScaledTripleBarrierReturn
        mids = [100.0] * 5 + [101.5] + [102.0] * 5   # +1.5% at t=5
        rc = VolScaledTripleBarrierReturn(
            k=0.0, time_cap_seconds=100, vol_lookback_seconds=3, cost_floor_bps=100
        )
        with patch("kilimanjaro.return_calc.vol_triple_barrier.load_target_prices",
                   return_value=_mp(mids)):
            out = rc.compute(_signals([{"ts": 0.0, "direction": 1.0, "strength": 2.0}]),
                             TARGET, "2026-01-01", "2026-01-02")
        r = out.iloc[0]
        self.assertAlmostEqual(r["barrier_bps"], 100.0)   # floor, k*sigma=0
        self.assertEqual(r["exit_exec_ts"], 5.0)          # first mid >= 100*1.01


class TestTrailingStopReturn(unittest.TestCase):
    def _compute(self, mids, rows, barrier_bps, time_cap):
        from kilimanjaro.return_calc.trailing_stop import TrailingStopReturn
        with patch("kilimanjaro.return_calc.trailing_stop.load_target_prices",
                   return_value=_mp(mids)):
            return TrailingStopReturn(barrier_bps=barrier_bps,
                                      time_cap_seconds=time_cap).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_ratchets_then_exits_on_retrace(self):
        # BUY fires t0..2, price 100->101->102 (anchor ratchets to 102), retrace at t3
        out = self._compute([100, 101, 102, 100.9, 100],
                            [{"ts": float(t), "direction": 1.0, "strength": 4.0} for t in range(3)],
                            barrier_bps=100, time_cap=100)
        self.assertEqual(len(out), 1)
        r = out.iloc[0]
        self.assertEqual(r["exit_reason"], "stop")
        self.assertEqual(r["exit_exec_ts"], 3.0)  # mid 100.9 < anchor 102 * (1-1%) = 100.98

    def test_lets_winner_run_past_fixed_tp(self):
        # a fixed +100bps TP would exit at t1; trailing holds the whole 100->105 run
        out = self._compute([100, 101, 102, 103, 104, 105, 103.9],
                            [{"ts": float(t), "direction": 1.0, "strength": 4.0} for t in range(6)],
                            barrier_bps=100, time_cap=100)
        r = out.iloc[0]
        self.assertEqual(r["exit_reason"], "stop")
        self.assertEqual(r["exit_exec_ts"], 6.0)  # retrace from anchor 105 only at t6

    def test_time_cap(self):
        out = self._compute([100, 100.1, 100.2, 100.3, 100.4],
                            [{"ts": 0.0, "direction": 1.0, "strength": 4.0}],
                            barrier_bps=1000, time_cap=3)  # 10% barrier never hit
        r = out.iloc[0]
        self.assertEqual(r["exit_reason"], "time")
        self.assertEqual(r["exit_exec_ts"], 3.0)

    def test_non_overlapping(self):
        out = self._compute([100] * 10,
                            [{"ts": 0.0, "direction": 1.0, "strength": 4.0},
                             {"ts": 1.0, "direction": 1.0, "strength": 4.0}],
                            barrier_bps=1000, time_cap=5)
        self.assertEqual(len(out), 1)  # second fire is inside the first trade -> skipped


class TestTripleBarrierReentry(unittest.TestCase):
    """The sequential re-entry fix: after an early barrier exit, re-enter on the next
    fire (matches the live strategy), instead of the old fixed time_cap decimation."""

    def _compute(self, mids, rows, barrier_bps, time_cap):
        from kilimanjaro.return_calc.triple_barrier import TripleBarrierReturn
        with patch("kilimanjaro.return_calc.triple_barrier.load_target_prices",
                   return_value=_mp(mids)):
            return TripleBarrierReturn(barrier_bps=barrier_bps,
                                      time_cap_seconds=time_cap).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_reenters_after_early_close(self):
        # BUY fires at t=0,1,2,3; price hits +1% at t1 (exit), re-enter at t2, exit t3.
        # OLD decimation (time_cap 100s) would keep ONLY t=0 -> 1 trade; new logic -> 2.
        out = self._compute([100, 101, 100, 101, 100],
                            [{"ts": float(t), "direction": 1.0, "strength": 4.0} for t in range(4)],
                            barrier_bps=100, time_cap=100)
        self.assertEqual(len(out), 2)
        self.assertEqual(out.iloc[0]["exit_exec_ts"], 1.0)   # first trade exits at t1 (+1%)
        self.assertEqual(out.iloc[1]["entry_exec_ts"], 2.0)  # re-enters at t2 (next fire after exit)

    def test_fire_inside_open_trade_skipped(self):
        # flat price, huge barrier -> first trade times out at cap; fires within skipped
        out = self._compute([100] * 10,
                            [{"ts": float(t), "direction": 1.0, "strength": 4.0} for t in range(5)],
                            barrier_bps=1000, time_cap=3)
        # entry t0 -> timeout at t3; fires t1,t2,t3 within [0,3] skipped; t4 re-enters
        self.assertEqual(out.iloc[0]["entry_exec_ts"], 0.0)
        self.assertEqual(out.iloc[0]["exit_exec_ts"], 3.0)
        self.assertEqual(out.iloc[1]["entry_exec_ts"], 4.0)
