"""Flip-grid combo builder + schedule wiring (driver/flip_grid). No data.

Replaces the old tests/test_grid_scan.py (which tested the removed hand-rolled
_make_schedules; schedules are orion's now — see test_orion_schedule)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import flip_grid
from kilimanjaro.framework.orion_schedule import OrionScheduleFilter


class TestFlipGrid(unittest.TestCase):
    def test_schedule_versions_are_orion_names(self):
        v = flip_grid.schedule_versions(xrp=False)
        self.assertIn("default_active", v)
        self.assertIn("default_inactive", v)
        self.assertNotIn("active", v)  # scan-local names are gone; orion is truth
        # XRP shifts active/inactive one hour later -> the xrp_* versions
        vx = flip_grid.schedule_versions(xrp=True)
        self.assertIn("xrp_active", vx)
        self.assertIn("xrp_inactive", vx)
        self.assertNotIn("default_active", vx)

    def test_combo_count_and_shape(self):
        combos = flip_grid.build_combos(flip_grid.MARKET_CONFIGS["btc"])
        # 7 boxes × 5 steps × 7 schedule versions
        self.assertEqual(len(combos), len(flip_grid.BOXES) * len(flip_grid.STEPS) * 7)
        sig, _rc, sched = combos[0]
        self.assertIsInstance(sched, OrionScheduleFilter)  # orion-backed schedule
        self.assertEqual(sched.has_exit_col, "exit_ts")
        self.assertEqual(sig.flip_exchange, "BINANCE")

    def test_xrp_market_uses_xrp_schedules(self):
        combos = flip_grid.build_combos(flip_grid.MARKET_CONFIGS["xrp"])
        versions = {c[2].name for c in combos}
        self.assertIn("xrp_active", versions)
        self.assertNotIn("default_active", versions)

    def test_all_markets_wired(self):
        for mkt in ("btc", "eth", "xrp"):
            cfg = flip_grid.MARKET_CONFIGS[mkt]
            self.assertEqual(cfg["target_exchange"], "GMO")
            self.assertTrue(cfg["execution_size"])


if __name__ == "__main__":
    unittest.main()
