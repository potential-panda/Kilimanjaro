"""Unit tests for the general parquet↔InfluxDB comparison engine
(`framework.verify.compare_frames`) — the shared core both verifiers use.

No InfluxDB or parquet: pure DataFrame in / stats out, so alignment and the
diff statistics are pinned independently of the two callers.
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework.verify import (
    _greedy_match,
    compare_frames,
)


class TestExactJoin(unittest.TestCase):
    def test_all_match_field_stats(self):
        local = pd.DataFrame({"ts": [1.0, 2.0, 3.0], "px": [100.0, 200.0, 300.0]})
        remote = pd.DataFrame({"ts": [1.0, 2.0, 3.0], "px": [100.0, 201.0, 300.0]})
        res = compare_frames(
            local, remote, on="ts", fields=[("px", "px", "px")], tolerance=None
        )
        cov = res["coverage"]
        self.assertEqual(cov["n_matched"], 3)
        self.assertEqual(cov["n_local_only"], 0)
        self.assertEqual(cov["n_remote_only"], 0)
        s = res["fields"]["px"]
        self.assertEqual(s["n"], 3)
        # diffs are [0, -1, 0] → signed avg -1/3, one non-exact
        self.assertAlmostEqual(s["avg"], -1.0 / 3.0)
        self.assertAlmostEqual(s["max_abs"], 1.0)
        self.assertAlmostEqual(s["pct_exact"], 2 / 3 * 100)

    def test_partial_overlap_coverage(self):
        local = pd.DataFrame({"ts": [1.0, 2.0, 3.0], "v": [1.0, 2.0, 3.0]})
        remote = pd.DataFrame({"ts": [2.0, 3.0, 4.0], "v": [2.0, 3.0, 4.0]})
        res = compare_frames(
            local, remote, on="ts", fields=[("v", "v", "v")], tolerance=None
        )
        cov = res["coverage"]
        self.assertEqual(cov["n_matched"], 2)  # ts 2,3
        self.assertEqual(cov["n_local_only"], 1)  # ts 1
        self.assertEqual(cov["n_remote_only"], 1)  # ts 4
        self.assertEqual(sorted(res["local_only"]["ts"]), [1.0])
        self.assertEqual(sorted(res["remote_only"]["ts"]), [4.0])

    def test_round_ndigits_key(self):
        local = pd.DataFrame({"ts": [1.0001, 2.0002], "v": [10.0, 20.0]})
        remote = pd.DataFrame({"ts": [1.0004, 2.0001], "v": [10.0, 20.0]})
        # rounding to 3 dp collapses 1.0001/1.0004 → 1.000 and 2.0002/2.0001 → 2.000
        res = compare_frames(
            local, remote, on="ts", fields=[("v", "v", "v")], tolerance=None,
            round_ndigits=3,
        )
        self.assertEqual(res["coverage"]["n_matched"], 2)

    def test_different_column_names(self):
        local = pd.DataFrame({"ts": [1.0], "flip_price": [100.0]})
        remote = pd.DataFrame({"ts": [1.0], "EXEC_PRICE": [100.5]})
        res = compare_frames(
            local, remote, on="ts",
            fields=[("price", "flip_price", "EXEC_PRICE")], tolerance=None,
        )
        s = res["fields"]["price"]
        self.assertAlmostEqual(s["avg"], -0.5)
        self.assertAlmostEqual(res["matched"]["price_diff"].iloc[0], -0.5)


class TestFuzzyMatch(unittest.TestCase):
    def test_greedy_match_within_tolerance(self):
        local = np.array([1.0, 5.0, 9.0])
        remote = np.array([1.4, 5.1, 20.0])  # 1↔1.4 (0.4), 5↔5.1 (0.1), 9 unmatched
        l_pos, r_pos = _greedy_match(local, remote, tolerance=0.5)
        self.assertEqual(list(l_pos), [0, 1])
        self.assertEqual(list(r_pos), [0, 1])

    def test_fuzzy_coverage_and_gaps(self):
        local = pd.DataFrame({"ts": [1.0, 5.0, 9.0], "p": [10.0, 50.0, 90.0]})
        remote = pd.DataFrame({"ts": [1.4, 5.1, 20.0], "p": [10.0, 51.0, 200.0]})
        res = compare_frames(
            local, remote, on="ts", fields=[("p", "p", "p")], tolerance=0.5
        )
        cov = res["coverage"]
        self.assertEqual(cov["n_matched"], 2)
        self.assertEqual(cov["n_local_only"], 1)  # ts 9
        self.assertEqual(cov["n_remote_only"], 1)  # ts 20
        self.assertEqual(list(res["local_only"]["ts"]), [9.0])
        self.assertEqual(list(res["remote_only"]["ts"]), [20.0])
        # matched diffs: [0, -1] → max_abs 1
        self.assertAlmostEqual(res["fields"]["p"]["max_abs"], 1.0)

    def test_fuzzy_unsorted_input(self):
        # engine must sort internally before matching
        local = pd.DataFrame({"ts": [9.0, 1.0, 5.0], "p": [90.0, 10.0, 50.0]})
        remote = pd.DataFrame({"ts": [5.1, 1.4], "p": [50.0, 10.0]})
        res = compare_frames(
            local, remote, on="ts", fields=[("p", "p", "p")], tolerance=0.5
        )
        self.assertEqual(res["coverage"]["n_matched"], 2)
        self.assertAlmostEqual(res["fields"]["p"]["pct_exact"], 100.0)


class TestEmpty(unittest.TestCase):
    def test_no_overlap(self):
        local = pd.DataFrame({"ts": [1.0], "v": [1.0]})
        remote = pd.DataFrame({"ts": [99.0], "v": [1.0]})
        res = compare_frames(
            local, remote, on="ts", fields=[("v", "v", "v")], tolerance=None
        )
        self.assertEqual(res["coverage"]["n_matched"], 0)
        self.assertEqual(res["fields"]["v"]["n"], 0)
        self.assertTrue(np.isnan(res["fields"]["v"]["avg"]))


if __name__ == "__main__":
    unittest.main()
