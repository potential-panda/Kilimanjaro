"""Unit tests for tools/grid_scan.py pure logic: the train/holdout window
split, the output filters, and the schedule registry construction (the
range-based hour lists must reproduce the original hand-written literals).
No scan data is touched.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from grid_scan import (  # noqa: E402
    MARKET_CONFIGS,
    _make_schedules,
    _passes_filters,
    split_windows,
)

FILTERS = {"avg_net_min": 0.0, "t_stat_min": 1.5, "p_value_max": 0.05}


class TestSplitWindows(unittest.TestCase):
    def test_split_date_goes_to_holdout(self):
        train_start, train_end, holdout_start, holdout_end = split_windows(
            "2025-07-01", "2026-03-01", "2026-06-30"
        )
        self.assertEqual(train_start, "2025-07-01")
        self.assertEqual(train_end, "2026-02-28")  # day before split, no overlap
        self.assertEqual(holdout_start, "2026-03-01")
        self.assertEqual(holdout_end, "2026-06-30")

    def test_split_equal_to_end_is_allowed(self):
        _, train_end, holdout_start, holdout_end = split_windows(
            "2025-07-01", "2026-06-30", "2026-06-30"
        )
        self.assertEqual(train_end, "2026-06-29")
        self.assertEqual(holdout_start, holdout_end)

    def test_split_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            split_windows("2025-07-01", "2025-07-01", "2026-06-30")  # split == start
        with self.assertRaises(ValueError):
            split_windows("2025-07-01", "2026-07-01", "2026-06-30")  # split > end


class TestPassesFilters(unittest.TestCase):
    def test_passing_row(self):
        row = {"avg_net": "2.5", "t_stat": "3.0", "p_value": "0.001"}
        self.assertTrue(_passes_filters(row, FILTERS))

    def test_each_filter_rejects(self):
        base = {"avg_net": "2.5", "t_stat": "3.0", "p_value": "0.001"}
        self.assertFalse(_passes_filters({**base, "avg_net": "-0.1"}, FILTERS))
        self.assertFalse(_passes_filters({**base, "t_stat": "1.4"}, FILTERS))
        self.assertFalse(_passes_filters({**base, "p_value": "0.06"}, FILTERS))

    def test_malformed_row_rejected(self):
        self.assertFalse(_passes_filters({}, FILTERS))
        self.assertFalse(_passes_filters({"avg_net": "nan?"}, FILTERS))


class TestMakeSchedules(unittest.TestCase):
    """The range-built hour lists must equal the original literals."""

    def test_eth_btc_schedules_match_original_literals(self):
        s = _make_schedules(6)
        all_hours = list(range(24))
        self.assertEqual(
            sorted(s["active"]["block_weekday_hours"]), [0, 1, 2, 3, 4, 5, 22, 23]
        )
        self.assertEqual(s["active"]["block_weekend_hours"], all_hours)
        self.assertEqual(s["inactive"]["block_weekday_hours"], list(range(6, 22)))
        self.assertIsNone(s["inactive"]["block_weekend_hours"])
        self.assertEqual(s["us_on"]["block_weekday_hours"], list(range(6, 22)))
        self.assertEqual(
            sorted(s["us_off"]["block_weekday_hours"]), [0, 1, 2, 3, 4, 5, 22, 23]
        )
        self.assertIsNone(s["nofilter"]["block_weekday_hours"])
        self.assertIsNone(s["nofilter"]["block_weekend_hours"])

    def test_xrp_active_starts_one_hour_later(self):
        s = _make_schedules(7)
        self.assertEqual(
            sorted(s["active"]["block_weekday_hours"]), [0, 1, 2, 3, 4, 5, 6, 22, 23]
        )
        self.assertEqual(s["inactive"]["block_weekday_hours"], list(range(7, 22)))
        # us_on/us_off use the fixed US-session boundary, not the market's
        self.assertEqual(s["us_on"]["block_weekday_hours"], list(range(6, 22)))

    def test_market_configs_wired(self):
        self.assertEqual(
            MARKET_CONFIGS["eth"]["schedules"]["inactive"]["block_weekday_hours"],
            list(range(6, 22)),
        )
        self.assertEqual(
            MARKET_CONFIGS["xrp"]["schedules"]["inactive"]["block_weekday_hours"],
            list(range(7, 22)),
        )
        for market in MARKET_CONFIGS.values():
            self.assertEqual(len(market["schedules"]), 7)


if __name__ == "__main__":
    unittest.main()
