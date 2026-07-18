"""Reward-function hardening: cost policy (flat round-trip latency slippage,
bid/ask returns) and Gap 2 (multiple-testing / Benjamini-Hochberg FDR).
Pure unit tests, no data."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework.costs import (  # noqa: E402
    PER_ORDER_SLIPPAGE_BPS,
    ROUND_TRIP_COST_BPS,
    round_trip_cost_bps,
)
from kilimanjaro.framework.evaluate import _bh_significant  # noqa: E402


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


class TestCostPolicy(unittest.TestCase):
    """The base cost policy: bid/ask returns already pay the spread, so the only
    cost is flat latency slippage — 0.5 bps/order, 1.0 bps round-trip. We never
    estimate/add a spread cost (that double-counts)."""

    def test_round_trip_is_two_orders_of_slippage(self):
        self.assertEqual(PER_ORDER_SLIPPAGE_BPS, 0.5)
        self.assertEqual(ROUND_TRIP_COST_BPS, 1.0)
        self.assertEqual(ROUND_TRIP_COST_BPS, 2 * PER_ORDER_SLIPPAGE_BPS)

    def test_helper_returns_the_constant(self):
        self.assertEqual(round_trip_cost_bps(), ROUND_TRIP_COST_BPS)

    def test_no_spread_estimator_exists(self):
        # the spread-estimate cost path was removed on purpose
        import kilimanjaro.framework.costs as costs
        self.assertFalse(hasattr(costs, "estimate_round_trip_cost_bps"))


if __name__ == "__main__":
    unittest.main()
