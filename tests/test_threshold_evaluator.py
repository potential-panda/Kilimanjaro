"""Proves implementation-tasks.md task 8's acceptance criteria for the
threshold evaluator: distinct-day minimum, sample-count minimums, cost
buffer with 25% margin, and single-cell-overfit detection.

The integration test re-runs the real scanner against the canonical
BINANCE-imbalance-predicts-GMO-return spec and confirms the evaluator
reaches the exact conclusion worked through by hand earlier: every
statistical threshold clears, but the cost buffer doesn't -- skipped
automatically if real Makalu data isn't present.
"""

import copy
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from threshold_evaluator import evaluate  # noqa: E402

_ROOT = Path(__file__).parent.parent
_MAKALU_DATA_ROOT = Path("/panda/makalu-data")


def _base_report() -> dict:
    return {
        "data_window": {"distinct_days": 10},
        "metrics": {
            "primary_horizon_seconds": 120,
            "ic_pooled": 0.05,
            "ic_by_day": {f"d{i}": 0.05 for i in range(10)},
            "hit_rate": 0.55,
            "raw_sample_count": 50_000,
            "effective_sample_count": 5_000,
            "bootstrap_ci_low": 0.03,
            "bootstrap_ci_high": 0.07,
            "bucketed_returns": [
                {"bucket": 0, "n": 100, "mean_target_return": -0.001},
                {"bucket": 4, "n": 100, "mean_target_return": 0.001},
            ],
            "decay_by_horizon": [
                {"horizon_seconds": 30, "ic_pooled": 0.06, "hit_rate": 0.55, "n": 50_000},
                {"horizon_seconds": 120, "ic_pooled": 0.05, "hit_rate": 0.55, "n": 50_000},
                {"horizon_seconds": 300, "ic_pooled": 0.04, "hit_rate": 0.53, "n": 50_000},
            ],
        },
    }


def _base_spec() -> dict:
    return {
        "hypothesis": {
            "target": {"type": "next_return"},
            "execution_assumptions": {"fees_bps": 0.1, "half_spread_bps": 0.1, "slippage_bps": 0.1},
        }
    }


class TestThresholdEvaluator(unittest.TestCase):
    def test_all_thresholds_pass(self):
        verdict = evaluate(_base_report(), _base_spec())
        self.assertEqual(verdict["status"], "pass")
        self.assertEqual(verdict["failed_rules"], [])

    def test_too_few_distinct_days_yields_needs_more_data(self):
        report = _base_report()
        report["data_window"]["distinct_days"] = 3
        verdict = evaluate(report, _base_spec())
        self.assertEqual(verdict["status"], "needs-more-data")
        self.assertIn("insufficient-data", verdict["failed_rules"])

    def test_low_raw_sample_count_fails(self):
        report = _base_report()
        report["metrics"]["raw_sample_count"] = 100
        verdict = evaluate(report, _base_spec())
        self.assertIn("insufficient-data", verdict["failed_rules"])

    def test_low_effective_sample_count_fails(self):
        report = _base_report()
        report["metrics"]["effective_sample_count"] = 10
        verdict = evaluate(report, _base_spec())
        self.assertIn("insufficient-data", verdict["failed_rules"])

    def test_weak_ic_fails(self):
        report = _base_report()
        report["metrics"]["ic_pooled"] = 0.005
        verdict = evaluate(report, _base_spec())
        self.assertIn("failed-ic-threshold", verdict["failed_rules"])

    def test_weak_hit_rate_fails(self):
        report = _base_report()
        report["metrics"]["hit_rate"] = 0.50
        verdict = evaluate(report, _base_spec())
        self.assertIn("failed-hit-rate-threshold", verdict["failed_rules"])

    def test_significance_interval_straddling_zero_fails(self):
        report = _base_report()
        report["metrics"]["bootstrap_ci_low"] = -0.01
        verdict = evaluate(report, _base_spec())
        self.assertIn("autocorrelation-adjustment-failed", verdict["failed_rules"])

    def test_day_instability_fails(self):
        report = _base_report()
        # Only 2 of 10 days share the pooled IC's sign -- below both the
        # absolute (3) and fractional (60%) day-stability minimums.
        ic_by_day = {f"d{i}": (0.05 if i < 2 else -0.05) for i in range(10)}
        report["metrics"]["ic_by_day"] = ic_by_day
        verdict = evaluate(report, _base_spec())
        self.assertIn("parameter-overfit", verdict["failed_rules"])

    def test_single_cell_overfit_fails(self):
        report = _base_report()
        report["metrics"]["decay_by_horizon"] = [
            {"horizon_seconds": 30, "ic_pooled": -0.06, "hit_rate": 0.45, "n": 50_000},
            {"horizon_seconds": 120, "ic_pooled": 0.05, "hit_rate": 0.55, "n": 50_000},  # primary
            {"horizon_seconds": 300, "ic_pooled": -0.04, "hit_rate": 0.47, "n": 50_000},
        ]
        verdict = evaluate(report, _base_spec())
        self.assertIn("parameter-overfit", verdict["failed_rules"])

    def test_single_horizon_grid_yields_yellow_flag_not_failure(self):
        report = _base_report()
        report["metrics"]["decay_by_horizon"] = [
            {"horizon_seconds": 120, "ic_pooled": 0.05, "hit_rate": 0.55, "n": 50_000},
        ]
        verdict = evaluate(report, _base_spec())
        self.assertNotIn("parameter-overfit", verdict["failed_rules"])
        self.assertIn("insufficient-grid-for-overfit-check", verdict["yellow_flags"])

    def test_cost_buffer_failure(self):
        report = _base_report()
        report["metrics"]["bucketed_returns"] = [
            {"bucket": 0, "n": 100, "mean_target_return": -0.00001},
            {"bucket": 4, "n": 100, "mean_target_return": 0.00001},
        ]
        verdict = evaluate(report, _base_spec())
        self.assertIn("failed-cost-buffer", verdict["failed_rules"])

    def test_missing_half_spread_info_yields_needs_tooling(self):
        report = _base_report()
        spec = _base_spec()
        spec["hypothesis"]["execution_assumptions"] = {"fees_bps": 0.1}  # no half_spread_bps, not flagged included
        verdict = evaluate(report, spec)
        self.assertEqual(verdict["status"], "needs-tooling")

    def test_spread_adjusted_target_does_not_double_count_half_spread(self):
        report = copy.deepcopy(_base_report())
        spec = _base_spec()
        spec["hypothesis"]["target"]["type"] = "spread_adjusted_return"
        spec["hypothesis"]["execution_assumptions"]["half_spread_bps"] = 100  # would fail if double-counted
        verdict = evaluate(report, spec)
        self.assertNotIn("failed-cost-buffer", verdict["failed_rules"])

    def test_regime_stability_always_flagged_in_v1(self):
        verdict = evaluate(_base_report(), _base_spec())
        self.assertIn("regime-stability-unproven", verdict["yellow_flags"])


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestThresholdEvaluatorIntegration(unittest.TestCase):
    def test_canonical_example_fails_cost_buffer_only(self):
        from scanner import run_scan

        spec = yaml.safe_load((_ROOT / "tests" / "fixtures" / "valid_spec.yaml").read_text())
        # >=5 distinct days needed so the distinct-days check doesn't mask
        # the cost-buffer-specific finding this test is actually checking.
        report = run_scan(spec, "2026-05-01", "2026-05-05")
        verdict = evaluate(report, spec)

        self.assertEqual(verdict["status"], "fail")
        self.assertEqual(verdict["failed_rules"], ["failed-cost-buffer"])


if __name__ == "__main__":
    unittest.main()
