"""Tests for tools/lead_lag_wave.py.

test_query_at_* are pure unit tests over a synthetic table. test_compute_wave_*
is a real integration test against Makalu's existing LeadLag gold dataset --
skipped automatically if it isn't present, since mocking the actual
cross-correlation output would prove nothing.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from lead_lag_wave import _max_lag_ms_from_param_id, compute_wave, query_at  # noqa: E402

_LEADLAG_PATH = Path(
    "/panda/makalu-data/gold/dataset=LeadLag/market_a=BINANCE:BTC_USDT/market_b=GMO:BTC_JPY"
)


def _synthetic_wave() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"asset": "BTC", "window_seconds": 60, "span_seconds": 30, "bucket": 18, "bucket_start_jst": "09:00",
             "avg_corr_at_zero": 0.02, "avg_max_corr": 0.17, "avg_alpha_gain_log": 1.5,
             "avg_opt_lag_ms": 1200.0, "avg_lag_stability": 2.4, "frac_binance_leads": 0.81, "n": 1000},
        ]
    )


class TestMaxLagFromParamId(unittest.TestCase):
    def test_parses_max_lag_ms(self):
        self.assertEqual(_max_lag_ms_from_param_id("l0_5100_100_s5_15_30_w10_20_30_60_wsz600_wst1"), 5100.0)


class TestQueryAt(unittest.TestCase):
    def test_finds_correct_bucket(self):
        result = query_at(_synthetic_wave(), "09:00")
        self.assertEqual(result["bucket"], 18)
        self.assertEqual(result["frac_binance_leads"], 0.81)

    def test_time_within_bucket_rounds_down(self):
        result = query_at(_synthetic_wave(), "09:20")
        self.assertEqual(result["bucket"], 18)

    def test_missing_bucket_reports_error(self):
        result = query_at(_synthetic_wave(), "03:00")
        self.assertIn("error", result)


@unittest.skipUnless(_LEADLAG_PATH.is_dir(), "real Makalu LeadLag data not present")
class TestComputeWave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = compute_wave("BTC")

    def test_covers_all_48_buckets(self):
        self.assertEqual(set(self.df["bucket"]), set(range(48)))

    def test_leading_fraction_is_a_probability(self):
        self.assertTrue((self.df["frac_binance_leads"] >= 0).all())
        self.assertTrue((self.df["frac_binance_leads"] <= 1).all())

    def test_binance_leads_most_of_the_time(self):
        # The whole premise of the user's prior strategy: BINANCE should be
        # the leading market essentially always, not just on average.
        self.assertTrue((self.df["frac_binance_leads"] > 0.5).all())

    def test_unsupported_asset_raises(self):
        with self.assertRaises(ValueError):
            compute_wave("DOGE")


if __name__ == "__main__":
    unittest.main()
