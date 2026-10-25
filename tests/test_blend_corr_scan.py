"""Stage 14 blend_corr — pure helpers for the multi-method blend scan."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np

from kilimanjaro.driver.blend_corr import _greedy_weights, _ic


class TestBlendScan(unittest.TestCase):
    def test_greedy_selects_predictive_drops_noise(self):
        rng = np.random.default_rng(0)
        n = 5000
        r = rng.standard_normal(n)
        f_good = r + 0.5 * rng.standard_normal(n)      # predictive
        f_dup = f_good + 0.01 * rng.standard_normal(n)  # redundant with f_good
        f_noise = rng.standard_normal(n)                # no signal
        Z = np.column_stack([f_good, f_dup, f_noise])
        ic = np.array([_ic(Z[:, j], r) for j in range(3)])
        w = _greedy_weights(Z, ic, r)
        # the noise factor must not be selected; at least the good one is
        self.assertNotEqual(w[2], 0.0) if False else self.assertEqual(w[2], 0.0)
        self.assertNotEqual(w[0], 0.0)

    def test_greedy_stops(self):
        # all-noise → greedy selects nothing meaningful (blend IC never improves)
        rng = np.random.default_rng(1)
        n = 3000
        r = rng.standard_normal(n)
        Z = rng.standard_normal((n, 4))
        ic = np.array([_ic(Z[:, j], r) for j in range(4)])
        w = _greedy_weights(Z, ic, r, min_gain=0.01)
        self.assertLessEqual(int(np.sum(np.abs(w) > 0)), 1)  # 0 or 1 factor, no real blend


if __name__ == "__main__":
    unittest.main()
