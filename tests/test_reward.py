"""Reward-function hardening: Gap 1 (real spread cost) and Gap 2 (multiple-
testing / Benjamini-Hochberg FDR). Pure unit tests, no data."""

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.data.price_cache import TargetPrices  # noqa: E402
from kilimanjaro.framework.costs import estimate_round_trip_cost_bps  # noqa: E402
from kilimanjaro.framework.evaluate import _bh_significant  # noqa: E402
from kilimanjaro.framework.protocol import MarketConfig  # noqa: E402

TARGET = MarketConfig("GMO", "ETH_JPY", "0.3")


class TestBenjaminiHochberg(unittest.TestCase):
    def test_all_tiny_p_all_significant(self):
        self.assertEqual(_bh_significant([0.001] * 5, 0.10), [True] * 5)

    def test_all_large_p_none_significant(self):
        self.assertEqual(_bh_significant([0.9, 0.8, 0.95], 0.10), [False, False, False])

    def test_only_the_clear_one_survives(self):
        # p=0.001 clears the BH line; the 0.2..0.5 do not
        flags = _bh_significant([0.001, 0.2, 0.3, 0.4, 0.5], 0.05)
        self.assertEqual(flags, [True, False, False, False, False])

    def test_nan_and_none_excluded_never_flagged(self):
        flags = _bh_significant([0.001, float("nan"), None, 0.002], 0.10)
        self.assertTrue(flags[0] and flags[3])
        self.assertFalse(flags[1] or flags[2])

    def test_tightens_as_family_grows(self):
        # p=0.02 is significant on its own but not among five with high others
        self.assertEqual(_bh_significant([0.02], 0.05), [True])
        flags = _bh_significant([0.02, 0.9, 0.9, 0.9, 0.9], 0.05)
        self.assertEqual(flags, [False] * 5)

    def test_empty_and_all_invalid(self):
        self.assertEqual(_bh_significant([], 0.10), [])
        self.assertEqual(_bh_significant([None, float("nan")], 0.10), [False, False])


class TestSpreadCost(unittest.TestCase):
    def _prices(self, bids, asks):
        return TargetPrices(ts=np.arange(len(bids), dtype=float),
                            bid=np.array(bids, dtype=float), ask=np.array(asks, dtype=float))

    def _estimate(self, prices):
        with patch("kilimanjaro.framework.costs.load_target_prices", return_value=prices):
            return estimate_round_trip_cost_bps(TARGET, "2026-01-01", "2026-01-31")

    def test_median_spread_in_bps(self):
        # mids 100, 110; spreads 2/100, 2/110 in bps -> 200, ~181.8; median ~190.9
        got = self._estimate(self._prices([99.0, 109.0], [101.0, 111.0]))
        self.assertAlmostEqual(got, (200.0 + 2 / 110 * 1e4) / 2, places=3)

    def test_floor_applies_on_tight_book(self):
        # zero spread -> floor 1.0
        got = self._estimate(self._prices([100.0, 100.0], [100.0, 100.0]))
        self.assertEqual(got, 1.0)

    def test_empty_prices_returns_floor(self):
        got = self._estimate(self._prices([], []))
        self.assertEqual(got, 1.0)


if __name__ == "__main__":
    unittest.main()
