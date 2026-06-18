"""Tests for tools/fill_probability.py.

test_query_at_* and test_x_label_* are pure unit tests. test_compute_wave_*
is a real integration test against Makalu's smallest negative-fee market
(GMO/BTC_SPOT, 7 days) -- skipped automatically if that Makalu checkout
isn't present, since mocking real trade/order-book data would prove nothing.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from fill_probability import _x_label, _x_value_from_label, compute_wave, query_at  # noqa: E402

_MAKALU_DATA_ROOT = Path("/panda/makalu-data")


def _synthetic_wave() -> pd.DataFrame:
    rows = [
        {"bucket": 18, "bucket_start_jst": "09:00", "horizon_seconds": 30, "n": 1000,
         "p_fill_buy_x1bps": 0.60, "p_fill_sell_x1bps": 0.58,
         "p_fill_buy_x5bps": 0.30, "p_fill_sell_x5bps": 0.28,
         "p_fill_buy_x10bps": 0.05, "p_fill_sell_x10bps": 0.04},
    ]
    return pd.DataFrame(rows)


class TestXLabelRoundTrip(unittest.TestCase):
    def test_integer_value(self):
        self.assertEqual(_x_label(10), "10")
        self.assertEqual(_x_value_from_label("10"), 10.0)

    def test_decimal_value(self):
        self.assertEqual(_x_label(1.5), "1p5")
        self.assertEqual(_x_value_from_label("1p5"), 1.5)

    def test_label_is_sql_identifier_safe(self):
        # no '.' or other characters that would break a bare SQL alias
        label = _x_label(2.5)
        self.assertNotIn(".", label)


class TestQueryAt(unittest.TestCase):
    def test_finds_correct_bucket_and_horizon(self):
        result = query_at(_synthetic_wave(), "09:00", horizon_seconds=30)
        self.assertEqual(result["bucket"], 18)
        self.assertEqual(len(result["by_distance"]), 3)

    def test_recommended_max_distance_is_largest_clearing_threshold(self):
        result = query_at(_synthetic_wave(), "09:00", horizon_seconds=30, target_probability=0.25)
        self.assertEqual(result["recommended_max_distance_bps"], 5.0)

    def test_unreachable_threshold_returns_none(self):
        result = query_at(_synthetic_wave(), "09:00", horizon_seconds=30, target_probability=0.99)
        self.assertIsNone(result["recommended_max_distance_bps"])

    def test_missing_bucket_reports_error(self):
        result = query_at(_synthetic_wave(), "03:00", horizon_seconds=30)
        self.assertIn("error", result)

    def test_sell_side_uses_sell_columns(self):
        result = query_at(_synthetic_wave(), "09:00", horizon_seconds=30, side="sell")
        self.assertEqual(result["by_distance"][0]["p_fill"], 0.58)


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestComputeWave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # GMO/BTC_SPOT: negative-fee, limit-only market, smallest dataset (7 days).
        cls.df = compute_wave("GMO", "BTC_SPOT", x_values_bps=[1, 10, 50], horizons=[30])

    def test_covers_all_48_buckets(self):
        self.assertEqual(set(self.df["bucket"]), set(range(48)))

    def test_probabilities_are_well_formed(self):
        for col in ("p_fill_buy_x1bps", "p_fill_buy_x10bps", "p_fill_buy_x50bps"):
            self.assertTrue((self.df[col] >= 0).all())
            self.assertTrue((self.df[col] <= 1).all())

    def test_fill_probability_decreases_with_distance(self):
        # Farther from the touch can never be more likely to fill than closer,
        # in the same bucket/horizon -- this is the core sanity check on the
        # "trade price crosses through this level" proxy.
        self.assertTrue((self.df["p_fill_buy_x1bps"] >= self.df["p_fill_buy_x10bps"]).all())
        self.assertTrue((self.df["p_fill_buy_x10bps"] >= self.df["p_fill_buy_x50bps"]).all())

    def test_buy_and_sell_side_are_both_present_and_roughly_symmetric(self):
        # Not exactly symmetric (real markets aren't), but should be in the
        # same ballpark on average -- a gross asymmetry would indicate a
        # sign error in one side's comparison.
        buy_mean = self.df["p_fill_buy_x1bps"].mean()
        sell_mean = self.df["p_fill_sell_x1bps"].mean()
        self.assertGreater(buy_mean, 0)
        self.assertGreater(sell_mean, 0)
        self.assertLess(abs(buy_mean - sell_mean), 0.2)


if __name__ == "__main__":
    unittest.main()
