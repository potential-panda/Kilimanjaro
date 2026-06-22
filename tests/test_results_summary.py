"""Tests for tools/results_summary.py -- the triage join across specs/,
ledger/, and scan-reports/, plus a live bronze-coverage staleness check.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from results_summary import build_results, classify_triage  # noqa: E402

_ROOT = Path(__file__).parent.parent
_MAKALU_DATA_ROOT = Path("/panda/makalu-data")


class TestClassifyTriage(unittest.TestCase):
    def test_pass_status(self):
        self.assertEqual(classify_triage("pass", [], None), "pass")
        self.assertEqual(classify_triage("backtest-approved", [], None), "pass")

    def test_needs_data_statuses(self):
        self.assertEqual(classify_triage("needs-more-data", ["insufficient-data"], None), "needs_data")
        self.assertEqual(classify_triage("needs-tooling", ["failed-cost-buffer"], None), "needs_data")

    def test_weak_signal_reason_overrides_cost_buffer_ratio(self):
        # Even a generous cost-buffer ratio doesn't matter if the
        # underlying IC/hit-rate itself was too weak -- a fundamental
        # signal-quality problem, not a cost problem.
        cb = {"edge_bps": 0.9, "required_bps": 1.0}
        self.assertEqual(classify_triage("fail", ["failed-ic-threshold"], cb), "weak")
        self.assertEqual(classify_triage("fail", ["failed-hit-rate-threshold"], cb), "weak")

    def test_cost_buffer_only_above_threshold_is_close(self):
        cb = {"edge_bps": 0.8, "required_bps": 1.0}  # ratio 0.8
        self.assertEqual(classify_triage("fail", ["failed-cost-buffer"], cb), "close")

    def test_cost_buffer_only_below_threshold_is_weak(self):
        cb = {"edge_bps": 0.2, "required_bps": 1.0}  # ratio 0.2
        self.assertEqual(classify_triage("fail", ["failed-cost-buffer"], cb), "weak")

    def test_real_case_gmo_btc_spot_ratio_point_eight_is_close(self):
        # hyp-20260618-001-v03's actual original result.
        cb = {"edge_bps": 1.08664017781867, "required_bps": 1.364698831627169}
        self.assertEqual(classify_triage("fail", ["failed-cost-buffer"], cb), "close")

    def test_real_case_gmo_btc_jpy_ratio_point_two_five_is_weak(self):
        # hyp-20260618-001's actual result.
        cb = {"edge_bps": 0.439634060595849, "required_bps": 1.7709726978833007}
        self.assertEqual(classify_triage("fail", ["failed-cost-buffer"], cb), "weak")

    def test_other_failure_reason_is_review(self):
        self.assertEqual(classify_triage("fail", ["parameter-overfit"], None), "review")
        self.assertEqual(classify_triage("fail", ["autocorrelation-adjustment-failed"], None), "review")

    def test_zero_required_bps_does_not_crash(self):
        cb = {"edge_bps": 0.5, "required_bps": 0}
        # ratio computed as 0 in this degenerate case -- below threshold, so weak.
        self.assertEqual(classify_triage("fail", ["failed-cost-buffer"], cb), "weak")


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestBuildResultsIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = build_results(check_bronze=True)
        cls.by_id = {r["hyp_id"]: r for r in cls.rows}

    def test_covers_every_real_spec(self):
        expected = {f.stem for f in (_ROOT / "specs").glob("hyp-*.yaml")}
        self.assertEqual(set(self.by_id), expected)

    def test_depth_pull_refill_asymmetry_is_weak(self):
        # Confirmed real result this session: failed-ic-threshold on all
        # 3 assets, consistently -- a fundamental signal-quality problem.
        for hyp_id in ("hyp-20260618-004", "hyp-20260618-004-v01", "hyp-20260618-004-v02"):
            self.assertEqual(self.by_id[hyp_id]["triage"], "weak", hyp_id)

    def test_gmo_btc_spot_is_close(self):
        # The near-miss found this session -- statistically clean, just
        # short on cost economics.
        self.assertEqual(self.by_id["hyp-20260618-001-v03"]["triage"], "close")

    def test_bitbank_xrp_spot_reflects_latest_full_history_scan(self):
        # This hypothesis was re-scanned multiple times this session;
        # the latest (full ~year of data) result should be reflected,
        # not a stale earlier pass.
        row = self.by_id["hyp-20260619-002"]
        self.assertEqual(row["status"], "scan-failed")
        self.assertGreater(row["scanned_distinct_days"], 100)  # not the original 8-day sample

    def test_more_data_available_is_zero_when_fully_rescanned(self):
        # hyp-20260619-002 was just rescanned against essentially all
        # available bronze -- there shouldn't be a large "more data"
        # gap immediately after.
        row = self.by_id["hyp-20260619-002"]
        self.assertLess(row["more_data_available_days"], 10)


if __name__ == "__main__":
    unittest.main()
