"""Bar clocks: the neutral decision-point stream for the continuous framework."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np
from kilimanjaro.data.bars import bar_closes, runs_to_signals


class TestClocks(unittest.TestCase):
    def setUp(self):
        self.ts = np.arange(20, dtype=float)
        self.mid = np.full(20, 100.0)
        self.qty = np.ones(20)

    def test_time_clock_is_evenly_spaced_and_price_independent(self):
        a = bar_closes("time", 5, ts=self.ts, mid=self.mid, qty=self.qty)
        self.assertEqual(list(a), [4, 9, 14, 19])
        wild = self.mid.copy(); wild[7] = 999.0          # a price spike...
        b = bar_closes("time", 5, ts=self.ts, mid=wild, qty=self.qty)
        self.assertEqual(list(a), list(b))                # ...must not move a time bar

    def test_volume_clock_closes_on_cumulative_units(self):
        # 1 unit/row, param 4 -> the bar closes on the row where the 4th unit lands (idx 3)
        self.assertEqual(list(bar_closes("volume", 4, ts=self.ts, qty=self.qty)),
                         [3, 7, 11, 15, 19])

    def test_dollar_clock_weights_by_notional(self):
        # qty*mid = 100/row; param 400 -> a close every 4 rows, on the reaching row
        got = bar_closes("dollar", 400, ts=self.ts, mid=self.mid, qty=self.qty)
        self.assertEqual(list(got), [3, 7, 11, 15, 19])
        # half the notional per row -> half as many bars
        got2 = bar_closes("dollar", 400, ts=self.ts, mid=self.mid, qty=self.qty * 0.5)
        self.assertEqual(len(got2), 2)

    def test_range_clock_closes_on_price_move_only(self):
        mid = np.array([100., 100.5, 101.0, 99.0, 99.0])
        # rate 1%: idx2 closes (|101-100| >= 1.00) and re-bases the open to 101, so the
        # NEXT threshold is 1.01 -> idx3 closes on |99-101| = 2.0. Re-basing is the point:
        # a range bar measures from the last close, not from a fixed level.
        self.assertEqual(list(bar_closes("range", 0.01, ts=np.arange(5.), mid=mid)), [2, 3])
        # a flat path never closes a range bar, however long it runs
        self.assertEqual(len(bar_closes("range", 0.01, ts=self.ts, mid=self.mid)), 0)

    def test_unknown_clock_rejected(self):
        with self.assertRaises(ValueError):
            bar_closes("candles", 5, ts=self.ts)


class TestRunCompression(unittest.TestCase):
    """Consecutive same-direction bars are ONE position — cost per change, not per bar."""

    def test_runs_collapse_to_direction_changes(self):
        ts = np.arange(10, dtype=float)
        ci = np.array([1, 3, 5, 7, 9])
        d = np.array([1., 1., -1., -1., 1.])       # runs: ++ then -- then +
        e_ts, e_d, x_ts = runs_to_signals(ci, d, ts)
        self.assertEqual(list(e_ts), [1.0, 5.0])   # one entry per run, not per bar
        self.assertEqual(list(e_d), [1.0, -1.0])
        self.assertEqual(list(x_ts), [5.0, 9.0])   # exit = the next change

    def test_zero_and_nan_directions_are_skipped(self):
        ts = np.arange(8, dtype=float)
        ci = np.array([0, 2, 4, 6])
        d = np.array([1., np.nan, 0., -1.])
        e_ts, e_d, x_ts = runs_to_signals(ci, d, ts)
        self.assertEqual(list(e_d), [1.0])
        self.assertEqual(list(x_ts), [6.0])

    def test_single_run_yields_nothing(self):
        e_ts, _, _ = runs_to_signals(np.array([0, 1, 2]), np.ones(3), np.arange(3, dtype=float))
        self.assertEqual(len(e_ts), 0)


class TestHysteresis(unittest.TestCase):
    """Hold the side until the signal crosses the OPPOSITE threshold — kills churn."""

    def test_dead_band_carries_position_through_wobble(self):
        from kilimanjaro.data.bars import hysteresis
        z = np.array([3.0, 0.5, -0.5, 0.2, -3.0, 0.1, 3.0])
        got = hysteresis(z, 2.0)
        # +1 held through the three mid-band wobbles, flips only at -3, back at +3
        self.assertEqual(list(got), [1., 1., 1., 1., -1., -1., 1.])

    def test_plain_sign_churns_by_comparison(self):
        from kilimanjaro.data.bars import hysteresis
        z = np.array([3.0, 0.5, -0.5, 0.2, -3.0, 0.1, 3.0])
        flips = lambda d: int((np.diff(d) != 0).sum())
        self.assertGreater(flips(np.sign(z)), flips(hysteresis(z, 2.0)))

    def test_leading_indecision_is_flat(self):
        from kilimanjaro.data.bars import hysteresis
        self.assertEqual(list(hysteresis(np.array([0.1, 0.2, 2.5]), 2.0)), [0., 0., 1.])
