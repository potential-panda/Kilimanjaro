"""Stage 13 signal_corr — pure helpers (no data needed)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from kilimanjaro.driver.signal_corr import factors_from_blend_expr, _split_top_level, _shift_ratio
import numpy as np


class TestSignalCorr(unittest.TestCase):
    def test_split_top_level(self):
        self.assertEqual(_split_top_level("a+b+c", "+"), ["a", "b", "c"])
        # '+' inside parens is not a split point
        self.assertEqual(_split_top_level("f(a+b)+c", "+"), ["f(a+b)", "c"])

    def test_factors_from_blend(self):
        expr = ("zscore(0.02*(zscore(a_mid / b_mid, 900)) + "
                "-0.01*(sign(a_ta_net_flow_30s) * zscore(a_ta_max_trade_size_30s, 1800)) + "
                "0.05*(zscore(a_bs_weighted_imb_k5, 1800)), 1800)")
        fs = factors_from_blend_expr(expr)
        self.assertEqual([f.expr for f in fs], [
            "zscore(a_mid / b_mid, 900)",
            "sign(a_ta_net_flow_30s) * zscore(a_ta_max_trade_size_30s, 1800)",
            "zscore(a_bs_weighted_imb_k5, 1800)",
        ])
        self.assertTrue(all(f.name for f in fs))  # every factor gets a slug

    def test_factors_dedup(self):
        expr = "zscore(0.1*(zscore(x, 60)) + 0.2*(zscore(x, 60)), 1800)"
        self.assertEqual(len(factors_from_blend_expr(expr)), 1)  # identical terms deduped

    def test_shift_ratio(self):
        mid = np.array([100.0, 101.0, 102.0, 103.0])
        r = _shift_ratio(mid, 1)
        np.testing.assert_allclose(r[:3], [101/100, 102/101, 103/102])
        self.assertTrue(np.isnan(r[-1]))  # last has no t+h


if __name__ == "__main__":
    unittest.main()
