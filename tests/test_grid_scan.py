"""Unit tests for tools/grid_scan.py pure logic: the schedule registry
construction (range-based hour lists must reproduce the original hand-written
literals) and combo building. Window-split and filter logic live in the
framework now and are tested in test_framework.py. No scan data is touched.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from grid_scan import (  # noqa: E402
    MARKET_CONFIGS,
    _build_combos,
    _make_schedules,
)


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
        self.assertEqual(s["inactive"]["block_weekend_hours"], [])
        self.assertEqual(s["us_on"]["block_weekday_hours"], list(range(6, 22)))
        self.assertEqual(
            sorted(s["us_off"]["block_weekday_hours"]), [0, 1, 2, 3, 4, 5, 22, 23]
        )
        self.assertEqual(s["nofilter"]["block_weekday_hours"], [])
        self.assertEqual(s["nofilter"]["block_weekend_hours"], [])

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


class TestBuildCombos(unittest.TestCase):
    def test_combo_count_and_shape(self):
        combos = _build_combos(MARKET_CONFIGS["eth"])
        self.assertEqual(len(combos), 7 * 5 * 7)  # boxes × steps × schedules
        sig, rc, sched = combos[0]
        self.assertEqual(sig.params, {"box": 0.001, "step": 0.01})
        self.assertEqual(sched.name, "nofilter")
        self.assertTrue(sched.block_gmo_maintenance)  # GMO target
        self.assertTrue(sched.block_jst_dates)  # BINANCE ETH outage dates
        self.assertEqual(sched.has_exit_col, "exit_ts")

    def test_schedule_names_cycle(self):
        combos = _build_combos(MARKET_CONFIGS["eth"])
        names = {c[2].name for c in combos}
        self.assertEqual(
            names,
            {"nofilter", "active", "inactive", "weekday", "weekend", "us_on", "us_off"},
        )


if __name__ == "__main__":
    unittest.main()
