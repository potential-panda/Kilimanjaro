"""MakerFEPReturn — the maker fill contract (mirrors qate simulator)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np
import pandas as pd

from kilimanjaro.return_calc.maker_fep import MakerFEPReturn


class TestMakerFill(unittest.TestCase):
    def setUp(self):
        self.mf = MakerFEPReturn(price_unit=0.0)
        self.ask = np.array([101., 101., 100., 99., 100.])
        self.bid = np.array([100., 100., 99., 98., 99.])

    def test_buy_fills_at_ask_when_book_crosses_down(self):
        # BUY@100.5: first tick ask<=100.5 is idx2 (ask=100) → fill at ask
        self.assertEqual(self.mf._maker_fill(1, 100.5, 0, 4, self.ask, self.bid, 0.0), (2, 100.0))

    def test_sell_fills_at_bid_when_book_crosses_up(self):
        # SELL@99.5: first tick bid>=99.5 is idx0 (bid=100) → fill at bid
        self.assertEqual(self.mf._maker_fill(-1, 99.5, 0, 4, self.ask, self.bid, 0.0), (0, 100.0))

    def test_no_cross_returns_none(self):
        self.assertIsNone(self.mf._maker_fill(1, 98.0, 0, 4, self.ask, self.bid, 0.0))

    def test_price_unit_requires_deeper_cross(self):
        # with price_unit=1, BUY@100.5 needs ask<=99.5 → idx3 (ask=99)
        self.assertEqual(self.mf._maker_fill(1, 100.5, 0, 4, self.ask, self.bid, 1.0), (3, 99.0))

    def test_long_only_skips_short_signals(self):
        # a SELL signal should produce no trade under long_only
        sig = pd.DataFrame({"ts": [10.0], "direction": [-1.0], "strength": [3.0]})
        # empty target prices → compute returns empty regardless, but long_only must not crash
        out = MakerFEPReturn(long_only=True).compute(sig, __import__("kilimanjaro.framework.protocol",
              fromlist=["MarketConfig"]).MarketConfig("GMO", "BTC_JPY", "0.01"),
              "2099-01-01", "2099-01-01")
        self.assertEqual(len(out), 0)


class TestFillDelayCausality(unittest.TestCase):
    """You cannot rest a limit order on a signal you don't have yet — the order goes on
    the book at ts+fill_delay. Guards the class of lookahead that was THE FILL LEAK."""

    def _run(self, delay):
        import kilimanjaro.return_calc.maker_fep as mod
        from kilimanjaro.data.price_cache import TargetPrices
        from kilimanjaro.framework.protocol import MarketConfig

        # A BUY signal at t=100. The ask dips to a fillable level ONLY at t=100.5 —
        # inside the signal's own second, i.e. before the signal is knowable.
        ts = np.array([100.0, 100.5, 101.0, 102.0, 140.0, 200.0])
        bid = np.array([99.0, 90.0, 99.0, 99.0, 99.0, 99.0])
        ask = np.array([101.0, 92.0, 101.0, 101.0, 101.0, 101.0])
        orig = mod.load_target_prices
        mod.load_target_prices = lambda *a, **k: TargetPrices(ts, bid, ask)
        try:
            sig = pd.DataFrame({"ts": [100.0], "direction": [1.0], "strength": [4.0]})
            return MakerFEPReturn(fill_delay_seconds=delay, hold_s=1.0, exit_window_s=1.0).compute(
                sig, MarketConfig("COINCHECK", "BTC_SPOT", "0.002"), "2026-01-01", "2026-01-02")
        finally:
            mod.load_target_prices = orig

    def test_zero_delay_takes_the_lookahead_fill(self):
        # posting at ts sees the t=100.5 dip → fills (this is the leak)
        self.assertEqual(len(self._run(0.0)), 1)

    def test_unit_delay_misses_it(self):
        # posting at ts+1 (t=101) — the dip is gone, so no trade
        self.assertEqual(len(self._run(1.0)), 0)


if __name__ == "__main__":
    unittest.main()
