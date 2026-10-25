"""ContinuousBarrierReturn: direction-change / TP / SL / timeout, whichever is first."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np
import pandas as pd

import kilimanjaro.return_calc.continuous_barrier as mod
from kilimanjaro.data.price_cache import TargetPrices
from kilimanjaro.framework.protocol import MarketConfig
from kilimanjaro.return_calc.continuous_barrier import ContinuousBarrierReturn

MKT = MarketConfig("COINCHECK", "BTC_SPOT", "0.002")


def run(calc, mid_path, direction=1.0, exit_ts=100.0):
    ts = np.arange(len(mid_path), dtype=float)
    mid = np.asarray(mid_path, dtype=float)
    prices = TargetPrices(ts=ts, bid=mid - 0.0, ask=mid + 0.0)   # zero spread: isolate logic
    orig = mod.load_target_prices
    mod.load_target_prices = lambda *a, **k: prices
    try:
        sig = pd.DataFrame({"ts": [0.0], "direction": [direction],
                            "strength": [1.0], "exit_ts": [exit_ts]})
        return calc.compute(sig, MKT, "2026-01-01", "2026-01-02")
    finally:
        mod.load_target_prices = orig


class TestExits(unittest.TestCase):
    def test_no_barriers_reduces_to_direction_change(self):
        out = run(ContinuousBarrierReturn(), [100.0] * 10, exit_ts=5.0)
        self.assertEqual(out.exit_reason.iloc[0], "direction")
        self.assertEqual(out.exit_exec_ts.iloc[0], 5.0)

    def test_take_profit_fires_at_first_crossing(self):
        # +100 bps at idx 3 (101.0 vs 100.0)
        out = run(ContinuousBarrierReturn(tp_bps=100), [100., 100.2, 100.5, 101., 102.])
        self.assertEqual(out.exit_reason.iloc[0], "take_profit")
        self.assertEqual(out.exit_exec_ts.iloc[0], 3.0)

    def test_stop_loss_fires_and_returns_negative(self):
        out = run(ContinuousBarrierReturn(sl_bps=50), [100., 99.9, 99.9, 99.4])
        self.assertEqual(out.exit_reason.iloc[0], "stop")
        self.assertLess(out.return_bps.iloc[0], 0)

    def test_timeout_caps_the_hold(self):
        out = run(ContinuousBarrierReturn(max_hold_s=4), [100.0] * 20, exit_ts=50.0)
        self.assertEqual(out.exit_reason.iloc[0], "timeout")
        self.assertEqual(out.exit_exec_ts.iloc[0], 4.0)

    def test_stop_wins_ties_with_take_profit(self):
        # one tick gaps through BOTH bands; assuming the favourable one is optimistic
        out = run(ContinuousBarrierReturn(tp_bps=50, sl_bps=50), [100., 100., 94.0])
        self.assertEqual(out.exit_reason.iloc[0], "stop")

    def test_short_direction_mirrors(self):
        # price FALLS 100 bps -> a short takes profit
        out = run(ContinuousBarrierReturn(tp_bps=100), [100., 99.5, 99.0], direction=-1.0)
        self.assertEqual(out.exit_reason.iloc[0], "take_profit")
        self.assertGreater(out.return_bps.iloc[0], 0)

    def test_earliest_barrier_wins_over_later_direction_change(self):
        out = run(ContinuousBarrierReturn(tp_bps=100), [100., 101., 100.], exit_ts=2.0)
        self.assertEqual(out.exit_reason.iloc[0], "take_profit")
        self.assertEqual(out.exit_exec_ts.iloc[0], 1.0)
