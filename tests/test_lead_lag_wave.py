"""Tests for tools/lead_lag_wave.py.

test_query_at_* are pure unit tests over a synthetic table. test_compute_wave_*
is a real integration test against bronze OrderBook data -- skipped
automatically if it isn't present, since mocking the actual cross-correlation
output would prove nothing.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from lead_lag_wave import compute_wave, compute_wave_for_known_asset, query_at  # noqa: E402

_MAKALU_DATA_ROOT = Path("/panda/makalu-data")


def _synthetic_wave() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"feature_exchange": "BINANCE", "feature_symbol": "BTC_USDT", "target_exchange": "GMO", "target_symbol": "BTC_JPY",
             "bucket": 18, "bucket_start_jst": "09:00",
             "feature_leads_fraction": 0.81, "mean_best_lag_seconds": 1.2, "mean_best_corr": 0.17, "mean_corr_at_zero": 0.10, "n_days": 60},
        ]
    )


class TestQueryAt(unittest.TestCase):
    def test_finds_correct_bucket(self):
        result = query_at(_synthetic_wave(), "09:00")
        self.assertEqual(result["bucket"], 18)
        self.assertEqual(result["feature_leads_fraction"], 0.81)

    def test_time_within_bucket_rounds_down(self):
        result = query_at(_synthetic_wave(), "09:20")
        self.assertEqual(result["bucket"], 18)

    def test_missing_bucket_reports_error(self):
        result = query_at(_synthetic_wave(), "03:00")
        self.assertIn("error", result)


class TestUnsupportedAsset(unittest.TestCase):
    def test_unknown_asset_raises(self):
        with self.assertRaises(ValueError):
            compute_wave_for_known_asset("DOGE")


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestComputeWave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A real 60-day window -- empirically checked that anything much
        # shorter (e.g. 2 weeks) is too noisy per-bucket for a stable read;
        # some buckets dip well below 50% leading with only ~15 days.
        cls.df = compute_wave("BINANCE", "BTC_USDT", "GMO", "BTC_JPY", start_date="2026-01-01", end_date="2026-03-01")

    def test_covers_all_48_buckets(self):
        self.assertEqual(set(self.df["bucket"]), set(range(48)))

    def test_leading_fraction_is_a_probability(self):
        self.assertTrue((self.df["feature_leads_fraction"] >= 0).all())
        self.assertTrue((self.df["feature_leads_fraction"] <= 1).all())

    def test_binance_leads_most_of_the_time(self):
        # The whole premise of the user's prior strategy: BINANCE should be
        # the leading market most of the day. Not literally every single
        # bucket clears 50% -- the low-activity daytime hours (e.g. 13:30
        # JST measured at ~49%) are measurably less reliably led than the
        # high-activity overnight hours, a real finding, not noise to
        # paper over with a uniform assertion.
        self.assertGreater(self.df["feature_leads_fraction"].mean(), 0.6)
        self.assertGreater((self.df["feature_leads_fraction"] > 0.5).mean(), 0.85)

    def test_best_lag_corr_at_least_as_strong_as_zero_lag(self):
        # The whole point of searching lags: the peak should never be worse
        # than assuming no lag at all.
        self.assertTrue((self.df["mean_best_corr"] >= self.df["mean_corr_at_zero"]).all())

    def test_same_market_raises(self):
        with self.assertRaises(ValueError):
            compute_wave("GMO", "BTC_JPY", "GMO", "BTC_JPY", start_date="2026-01-01", end_date="2026-01-02")


if __name__ == "__main__":
    unittest.main()
