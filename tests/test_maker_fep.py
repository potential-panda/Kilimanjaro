"""MakerFEPReturn — the maker fill contract (mirrors qate simulator)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np, pandas as pd
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


if __name__ == "__main__":
    unittest.main()
