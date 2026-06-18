"""Tests for tools/volatility_wave.py.

test_query_at_* are pure unit tests over a synthetic table. test_compute_wave_*
is a real integration test against Makalu's smallest dataset (COINCHECK/BTC_SPOT,
7 days) -- skipped automatically if that Makalu checkout isn't present, since
mocking the actual price data would prove nothing about correctness.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from volatility_wave import compute_wave, query_at  # noqa: E402

_MAKALU_DATA_ROOT = Path("/panda/makalu-data")


def _synthetic_wave() -> pd.DataFrame:
    rows = [
        {"bucket": 18, "bucket_start_jst": "09:00", "horizon_seconds": 1, "avg_spread": 100.0,
         "n": 1000, "p_gt_1x": 0.10, "p_gt_2x": 0.03, "p_gt_3x": 0.01},
        {"bucket": 18, "bucket_start_jst": "09:00", "horizon_seconds": 30, "avg_spread": 100.0,
         "n": 1000, "p_gt_1x": 0.40, "p_gt_2x": 0.15, "p_gt_3x": 0.05},
        {"bucket": 18, "bucket_start_jst": "09:00", "horizon_seconds": 120, "avg_spread": 100.0,
         "n": 1000, "p_gt_1x": 0.65, "p_gt_2x": 0.40, "p_gt_3x": 0.20},
    ]
    return pd.DataFrame(rows)


class TestQueryAt(unittest.TestCase):
    def test_finds_correct_bucket(self):
        result = query_at(_synthetic_wave(), "09:00")
        self.assertEqual(result["bucket"], 18)
        self.assertEqual(result["avg_spread"], 100.0)

    def test_time_within_bucket_rounds_down(self):
        result = query_at(_synthetic_wave(), "09:15")
        self.assertEqual(result["bucket"], 18)

    def test_recommended_min_horizon_is_smallest_clearing_threshold(self):
        result = query_at(_synthetic_wave(), "09:00", target_probability=0.5)
        self.assertEqual(result["recommended_min_horizon_seconds"], 120)

    def test_lower_threshold_picks_a_smaller_horizon(self):
        result = query_at(_synthetic_wave(), "09:00", target_probability=0.35)
        self.assertEqual(result["recommended_min_horizon_seconds"], 30)

    def test_unreachable_threshold_returns_none(self):
        result = query_at(_synthetic_wave(), "09:00", target_probability=0.99)
        self.assertIsNone(result["recommended_min_horizon_seconds"])

    def test_missing_bucket_reports_error(self):
        result = query_at(_synthetic_wave(), "03:00")
        self.assertIn("error", result)


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestComputeWave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # COINCHECK/BTC_SPOT is the cheapest real dataset (7 days) -- keeps
        # this test fast while still proving the DuckDB pipeline against
        # real bronze OrderBook data, not a mock.
        cls.df = compute_wave("COINCHECK", "BTC_SPOT", horizons=[1, 30, 120])

    def test_covers_all_buckets_and_horizons(self):
        self.assertEqual(len(self.df), 48 * 3)
        self.assertEqual(set(self.df["bucket"]), set(range(48)))
        self.assertEqual(set(self.df["horizon_seconds"]), {1, 30, 120})

    def test_probability_increases_with_horizon_on_average(self):
        by_horizon = self.df.groupby("horizon_seconds")["p_gt_1x"].mean()
        self.assertLess(by_horizon[1], by_horizon[30])
        self.assertLess(by_horizon[30], by_horizon[120])

    def test_probabilities_are_well_formed(self):
        for col in ("p_gt_1x", "p_gt_2x", "p_gt_3x"):
            self.assertTrue((self.df[col] >= 0).all())
            self.assertTrue((self.df[col] <= 1).all())
        # clearing a bigger multiple of spread can never be more likely
        # than clearing a smaller one, in the same bucket/horizon.
        self.assertTrue((self.df["p_gt_1x"] >= self.df["p_gt_2x"]).all())
        self.assertTrue((self.df["p_gt_2x"] >= self.df["p_gt_3x"]).all())

    def test_spread_is_positive(self):
        self.assertTrue((self.df["avg_spread"] > 0).all())


if __name__ == "__main__":
    unittest.main()
