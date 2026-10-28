"""Path metrics: bps (equal-weight) and JPY per-fill. Guards the cost/curve work.

The JPY per-fill curve (Enoshima-faithful) is what fixed the scan-vs-Enoshima
ranking; these lock in its arithmetic and the bps↔jpy distinction.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework.performance import (
    compute_path_metrics,
    compute_path_metrics_jpy,
)

_DAY = 86400
_BASE = 1780000000  # ~2026, a real second so JST-day bucketing is sane


def _daily_ts(n):
    return np.array([_BASE + i * _DAY for i in range(n)], dtype=float)


class TestBpsMetrics(unittest.TestCase):
    def test_total_and_ratios_sane(self):
        net = np.array([5.0, -2.0, 8.0, -1.0, 4.0, 3.0, -3.0, 6.0], dtype=float)
        m = compute_path_metrics(net, _daily_ts(len(net)))
        self.assertAlmostEqual(m["total_net_bps"], net.sum(), places=6)
        self.assertGreater(m["max_dd_bps"], 0)  # it dips somewhere
        self.assertTrue(np.isfinite(m["martin"]))

    def test_too_few_points_is_nan(self):
        m = compute_path_metrics(np.array([1.0, 2.0]), _daily_ts(2))
        self.assertTrue(np.isnan(m["martin"]))


class TestJpyPerFill(unittest.TestCase):
    def test_jpy_total_scales_by_notional(self):
        # net_jpy per trade = net_bps/1e4 × notional; jpy_total = its sum
        net = np.array([10.0, -5.0, 20.0, -8.0, 15.0, 4.0], dtype=float)
        notional = np.full(len(net), 120_000.0)
        m = compute_path_metrics_jpy(net, _daily_ts(len(net)), notional)
        expected = float((net / 1e4 * notional).sum())
        self.assertAlmostEqual(m["jpy_total"], expected, places=4)

    def test_notional_weighting_differs_from_bps(self):
        # bps nets to 0 (symmetric wins/losses), but every LOSS lands on a
        # bigger book -> notional weighting makes the JPY total negative. So the
        # JPY curve is NOT a scaled copy of the bps curve (the whole reason it
        # tracks Enoshima where bps does not).
        net = np.array([10.0, -10.0, 10.0, -10.0, 10.0, -10.0], dtype=float)
        notional = np.array([1e5, 3e5, 1e5, 3e5, 1e5, 3e5])  # losses on big book
        mj = compute_path_metrics_jpy(net, _daily_ts(len(net)), notional)
        mb = compute_path_metrics(net, _daily_ts(len(net)))
        self.assertAlmostEqual(mb["total_net_bps"], 0.0, places=6)  # bps symmetric
        self.assertLess(mj["jpy_total"], 0.0)  # JPY: losses weighted heavier

    def test_empty_is_nan(self):
        m = compute_path_metrics_jpy(np.array([]), np.array([]), np.array([]))
        self.assertTrue(np.isnan(m["jpy_total"]))


if __name__ == "__main__":
    unittest.main()
