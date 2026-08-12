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


class TestDecayTable(unittest.TestCase):
    """decay_table reshapes the long IC table into one row per factor × market pair."""

    def _ic(self):
        import pandas as pd
        rows = []
        # weak factor peaking late, strong factor peaking early (classic lead-lag decay)
        for h, a, b in [(5, 0.01, 0.09), (30, 0.02, 0.05), (300, 0.03, 0.01)]:
            rows.append(dict(family="f1", factor="slow", expr="e1", horizon=h, n=100,
                             ic=a, rank_ic=a))
            rows.append(dict(family="f2", factor="fast", expr="e2", horizon=h, n=100,
                             ic=b, rank_ic=b))
        return pd.DataFrame(rows)

    def test_shape_columns_and_provenance(self):
        from kilimanjaro.driver.signal_corr import decay_table
        d = decay_table(self._ic(), "BINANCE:BTC_USDT", "GMO:BTC_JPY")
        self.assertEqual(len(d), 2)  # one row per factor
        for c in ("market_a", "market_b", "corr5", "ic5", "corr300", "ic300", "peak_h"):
            self.assertIn(c, d.columns)
        self.assertEqual(set(d.market_a), {"BINANCE:BTC_USDT"})
        self.assertEqual(set(d.market_b), {"GMO:BTC_JPY"})

    def test_peak_is_the_max_abs_horizon_and_rows_rank_by_it(self):
        from kilimanjaro.driver.signal_corr import decay_table
        d = decay_table(self._ic(), "A", "B").set_index("factor")
        self.assertEqual(d.loc["fast", "peak_h"], 5)     # 0.09 at h=5
        self.assertEqual(d.loc["slow", "peak_h"], 300)   # 0.03 at h=300
        # strongest |peak| sorts first
        self.assertEqual(decay_table(self._ic(), "A", "B").factor.iloc[0], "fast")

    def test_negative_ic_peaks_on_magnitude_not_sign(self):
        from kilimanjaro.driver.signal_corr import decay_table
        ic = self._ic()
        ic.loc[ic.factor == "fast", ["ic", "rank_ic"]] *= -1  # inverted factor
        d = decay_table(ic, "A", "B").set_index("factor")
        self.assertEqual(d.loc["fast", "peak_h"], 5)
        self.assertLess(d.loc["fast", "peak_ic"], 0)
