"""Tests for tools/regime_window.py -- the static, history-derived
favorable-trading-window artifact (vol/spread + lead-lag + fill-ease,
intersected, never computed live on the window being scored).
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from regime_window import compute_favorable_buckets, regime_key  # noqa: E402


def _vol_wave(values: dict[int, float]) -> pd.DataFrame:
    return pd.DataFrame([{"bucket": b, "horizon_seconds": 30, "p_gt_1x": v} for b, v in values.items()])


def _lead_lag_wave(values: dict[int, float]) -> pd.DataFrame:
    return pd.DataFrame([{"bucket": b, "mean_best_corr": v} for b, v in values.items()])


def _fill_wave(values: dict[int, float], horizon_seconds: int = 120) -> pd.DataFrame:
    return pd.DataFrame([
        {"bucket": b, "horizon_seconds": horizon_seconds, "p_fill_buy_x5bps": v, "p_fill_sell_x5bps": v}
        for b, v in values.items()
    ])


class TestComputeFavorableBuckets(unittest.TestCase):
    def test_single_criterion_picks_top_tercile(self):
        vol = _vol_wave({i: float(i) for i in range(9)})  # 0..8, top tercile = {6,7,8}
        result = compute_favorable_buckets(vol_wave=vol, tercile=1 / 3)
        self.assertEqual(set(result["favorable_buckets"]), {6, 7, 8})
        self.assertEqual(set(result["criteria"].keys()), {"volatility_spread"})

    def test_intersection_of_two_criteria_is_narrower_than_either_alone(self):
        vol = _vol_wave({0: 1.0, 1: 9.0, 2: 9.0, 3: 1.0})  # top half: {1, 2}
        leadlag = _lead_lag_wave({0: 9.0, 1: 9.0, 2: 1.0, 3: 1.0})  # top half: {0, 1}
        result = compute_favorable_buckets(vol_wave=vol, lead_lag_wave=leadlag, tercile=0.5)
        # only bucket 1 clears both
        self.assertEqual(result["favorable_buckets"], [1])
        self.assertEqual(set(result["criteria"].keys()), {"volatility_spread", "lead_lag"})

    def test_omitted_criterion_is_not_silently_assumed_favorable(self):
        vol = _vol_wave({0: 1.0, 1: 9.0})
        result = compute_favorable_buckets(vol_wave=vol, tercile=0.5)
        self.assertNotIn("lead_lag", result["criteria"])
        self.assertNotIn("fill_ease", result["criteria"])

    def test_fill_wave_filters_to_one_horizon_not_any_horizon(self):
        # Regression: before this was fixed, every bucket "passed" because
        # SOME horizon (out of several rows per bucket) always cleared the
        # threshold -- longer horizons trivially have higher fill
        # probability, silently defeating the whole filter.
        fill = pd.concat([
            _fill_wave({0: 0.01, 1: 0.5}, horizon_seconds=1),
            _fill_wave({0: 0.9, 1: 0.9}, horizon_seconds=300),  # would make bucket 0 "pass" if not filtered
        ])
        result = compute_favorable_buckets(fill_wave=fill, fill_cols=("p_fill_buy_x5bps", "p_fill_sell_x5bps"), fill_horizon_seconds=1, tercile=0.5)
        self.assertEqual(result["favorable_buckets"], [1])

    def test_no_criteria_raises(self):
        with self.assertRaises(ValueError):
            compute_favorable_buckets()


class TestRegimeKey(unittest.TestCase):
    def test_same_market_key_has_no_feature_prefix(self):
        self.assertEqual(regime_key("BITBANK", "XRP_SPOT", "BITBANK", "XRP_SPOT"), "BITBANK_XRP_SPOT")

    def test_cross_market_key_includes_feature(self):
        self.assertEqual(regime_key("BINANCE", "BTC_USDT", "GMO", "BTC_JPY"), "BINANCE_BTC_USDT__GMO_BTC_JPY")

    def test_no_feature_market_given_falls_back_to_target_only(self):
        self.assertEqual(regime_key(None, None, "GMO", "BTC_JPY"), "GMO_BTC_JPY")


if __name__ == "__main__":
    unittest.main()
