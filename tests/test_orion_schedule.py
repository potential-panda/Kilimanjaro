"""Regression guards for the scan↔Himalaya schedule bug.

The scan's old hand-rolled 'active' schedule blocked BOTH weekend days, while
orion's 'default_active' blocks Saturday only and TRADES Sunday — an 18%
trade-count gap that broke scan↔Himalaya agreement. These lock in that the scan
now applies orion's rule (framework/orion_schedule = single source of truth).
"""

import datetime as dt
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework.orion_schedule import OrionScheduleFilter, entry_allowed  # noqa: E402


def _jst_ts(y, m, d, h) -> float:
    """Unix ts whose JST wall-clock is (y, m, d, h). Feb 2026: Mon=2 … Sun=8."""
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp() - 9 * 3600


try:
    from orion.schedule import get_schedule  # noqa: E402
    from qate.core.model import TradingMode  # noqa: E402

    _HAS_ORION = True
except Exception:
    _HAS_ORION = False


class TestDefaultActiveWeekendRule(unittest.TestCase):
    def test_trades_sunday_blocks_saturday_and_weekday_offhours(self):
        cases = {
            "sun_noon": _jst_ts(2026, 2, 8, 12),  # Sunday -> ALLOWED (the fix)
            "sat_noon": _jst_ts(2026, 2, 7, 12),  # Saturday -> blocked
            "mon_noon": _jst_ts(2026, 2, 2, 12),  # Mon 12:00 -> allowed (06-21)
            "mon_3am": _jst_ts(2026, 2, 2, 3),  # Mon 03:00 -> blocked (off-hours)
        }
        df = pd.DataFrame({"ts": list(cases.values())})
        kept, info = OrionScheduleFilter("default_active").apply(df)
        kept_ts = set(kept["ts"])
        self.assertIn(cases["sun_noon"], kept_ts)  # Sunday traded — regression
        self.assertIn(cases["mon_noon"], kept_ts)
        self.assertNotIn(cases["sat_noon"], kept_ts)
        self.assertNotIn(cases["mon_3am"], kept_ts)
        self.assertEqual(info["n_schedule_blocked"], 2)

    def test_weekday_blocks_both_weekend_days(self):
        # 'weekday' is the one that blocks Sat AND Sun (unlike default_active)
        sun = _jst_ts(2026, 2, 8, 12)
        sat = _jst_ts(2026, 2, 7, 12)
        mon = _jst_ts(2026, 2, 2, 12)
        kept, _ = OrionScheduleFilter("weekday").apply(pd.DataFrame({"ts": [sun, sat, mon]}))
        self.assertEqual(set(kept["ts"]), {mon})


@unittest.skipUnless(_HAS_ORION, "orion/qate not importable")
class TestOrionParity(unittest.TestCase):
    def test_entry_allowed_matches_orion_check(self):
        """The scan's mask is exactly orion.schedule.check() — the whole point."""
        probes = [(2026, 2, 2, 12), (2026, 2, 7, 10), (2026, 2, 8, 3), (2026, 2, 4, 23),
                  (2026, 2, 2, 3), (2026, 2, 6, 21)]
        for version in ("default_active", "default_inactive", "weekday", "us_on", "us_off", "nofilter"):
            sch = get_schedule(version)
            for (y, m, d, h) in probes:
                ts = _jst_ts(y, m, d, h)
                got = bool(entry_allowed(version, np.array([ts]))[0])
                truth = sch.check(ts) == TradingMode.DEFAULT
                self.assertEqual(got, truth, (version, y, m, d, h))


if __name__ == "__main__":
    unittest.main()
