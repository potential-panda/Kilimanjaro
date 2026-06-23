"""Proves implementation-tasks.md task 7's acceptance criteria for the
signal scanner.

Unit tests use small synthetic arrays for the individual metric functions
(fast, deterministic). The integration test runs the real scanner against
the canonical BINANCE-imbalance-predicts-GMO-return spec over a 2-day
window of real Makalu bronze data -- skipped automatically if that data
isn't present, since this is the same case worked through by hand earlier
and the whole point is confirming the automated version agrees with it.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from scanner import (  # noqa: E402
    _bucketed_returns,
    _bucketed_returns_by_day,
    _bucketed_returns_by_month,
    _bucketed_returns_by_week,
    _effective_sample_count,
    _hit_rate,
    _lag1_autocorr,
    _measured_lead_lag,
    _newey_west_hac_ci,
    _peak_lag_corr,
    _spearman_ic,
    run_scan,
    write_report,
)

_ROOT = Path(__file__).parent.parent
_MAKALU_DATA_ROOT = Path("/panda/makalu-data")
_SCHEMA_PATH = _ROOT / "schemas" / "scan_report.schema.json"


def _synthetic_predictive_df(n: int = 2000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    signal = rng.normal(size=n)
    target = 0.3 * signal + rng.normal(scale=1.0, size=n)
    dates = ["2026-01-01"] * (n // 2) + ["2026-01-02"] * (n - n // 2)
    return pd.DataFrame({"signal": signal, "target_return": target, "jst_date": dates})


class TestMetricFunctions(unittest.TestCase):
    def test_spearman_ic_detects_known_correlation(self):
        df = _synthetic_predictive_df()
        ic = _spearman_ic(df["signal"].to_numpy(), df["target_return"].to_numpy())
        self.assertGreater(ic, 0.1)

    def test_spearman_ic_none_for_constant_input(self):
        self.assertIsNone(_spearman_ic(np.zeros(10), np.arange(10)))

    def test_hit_rate_above_half_for_predictive_signal(self):
        df = _synthetic_predictive_df()
        hr = _hit_rate(df["signal"].to_numpy(), df["target_return"].to_numpy())
        self.assertGreater(hr, 0.5)

    def test_hit_rate_none_when_all_signs_zero(self):
        self.assertIsNone(_hit_rate(np.zeros(10), np.zeros(10)))

    def test_lag1_autocorr_detects_persistence(self):
        x = np.cumsum(np.random.RandomState(0).normal(size=1000))  # random walk: highly autocorrelated
        rho = _lag1_autocorr(x)
        self.assertGreater(rho, 0.9)

    def test_effective_sample_count_smaller_than_raw_for_autocorrelated_signal(self):
        n = 1000
        x = np.cumsum(np.random.RandomState(0).normal(size=n))
        df = pd.DataFrame({"signal": x, "target_return": np.random.RandomState(1).normal(size=n), "jst_date": ["2026-01-01"] * n})
        raw_n, eff_n = _effective_sample_count(df)
        self.assertEqual(raw_n, n)
        self.assertLess(eff_n, raw_n)

    def test_bucketed_returns_monotonic_for_strong_signal(self):
        df = _synthetic_predictive_df(n=5000)
        buckets = _bucketed_returns(df)
        self.assertEqual(len(buckets), 5)
        means = [b["mean_target_return"] for b in sorted(buckets, key=lambda b: b["bucket"])]
        self.assertEqual(means, sorted(means))  # bucket 0 (low signal) -> bucket 4 (high signal) increasing

    def test_bucketed_returns_by_day_splits_on_calendar_day(self):
        df = _synthetic_predictive_df(n=2000, seed=0)  # already 2 distinct days
        by_day = _bucketed_returns_by_day(df)

        self.assertEqual([d["day"] for d in by_day], ["2026-01-01", "2026-01-02"])
        for d in by_day:
            self.assertEqual(d["distinct_days"], 1)
            self.assertIsNotNone(d["ic_pooled"])
            self.assertEqual(len(d["bucketed_returns"]), 5)

    def test_bucketed_returns_by_week_splits_on_calendar_week(self):
        wk1 = _synthetic_predictive_df(n=2000, seed=0)
        wk1["jst_date"] = "2026-01-05"  # a Monday
        wk2 = _synthetic_predictive_df(n=2000, seed=1)
        wk2["jst_date"] = "2026-01-19"  # two weeks later
        df = pd.concat([wk1, wk2], ignore_index=True)

        by_week = _bucketed_returns_by_week(df)

        self.assertEqual(len(by_week), 2)
        self.assertNotEqual(by_week[0]["week"], by_week[1]["week"])
        for w in by_week:
            self.assertEqual(w["distinct_days"], 1)
            self.assertEqual(len(w["bucketed_returns"]), 5)

    def test_bucketed_returns_by_month_splits_on_calendar_month(self):
        jan = _synthetic_predictive_df(n=2000, seed=0)
        jan["jst_date"] = "2026-01-15"
        feb = _synthetic_predictive_df(n=2000, seed=1)
        feb["jst_date"] = "2026-02-15"
        df = pd.concat([jan, feb], ignore_index=True)

        by_month = _bucketed_returns_by_month(df)

        self.assertEqual([m["month"] for m in by_month], ["2026-01", "2026-02"])
        for m in by_month:
            self.assertEqual(m["distinct_days"], 1)
            self.assertEqual(len(m["bucketed_returns"]), 5)

    def test_newey_west_ci_excludes_zero_for_strong_signal(self):
        df = _synthetic_predictive_df(n=5000)
        ci_low, ci_high, bandwidth = _newey_west_hac_ci(df)
        self.assertGreater(ci_low, 0)  # whole CI above zero -- real, positive effect
        self.assertIsNotNone(bandwidth)

    def test_newey_west_ci_none_for_too_little_data(self):
        df = pd.DataFrame({"signal": [1.0, 2.0], "target_return": [1.0, 2.0], "jst_date": ["2026-01-01"] * 2})
        ci_low, ci_high, bandwidth = _newey_west_hac_ci(df)
        self.assertIsNone(ci_low)
        self.assertIsNone(bandwidth)

    def test_measured_lead_lag_none_for_same_market(self):
        # Same feature/target glob -- lead-lag isn't meaningful against
        # yourself; no query should even be attempted.
        self.assertIsNone(_measured_lead_lag(None, "same/glob", "same/glob", "2026-05-01", "2026-05-02"))

    def test_peak_lag_corr_detects_known_lead(self):
        rng = np.random.RandomState(0)
        n = 2000
        feature_ret = rng.normal(size=n)
        target_ret = np.empty(n)
        target_ret[:3] = rng.normal(size=3)
        target_ret[3:] = 0.8 * feature_ret[:-3] + 0.2 * rng.normal(size=n - 3)  # target lags feature by 3
        result = _peak_lag_corr(feature_ret, target_ret, max_lag_seconds=10)
        self.assertIsNotNone(result)
        best_lag, best_corr, corr_at_zero = result
        self.assertEqual(best_lag, 3)
        self.assertGreater(best_corr, corr_at_zero)

    def test_peak_lag_corr_none_for_too_little_data(self):
        self.assertIsNone(_peak_lag_corr(np.array([1.0, 2.0]), np.array([1.0, 2.0]), max_lag_seconds=10))


class TestWriteReportConcurrencySafety(unittest.TestCase):
    """Confirmed for real (2026-06-22): two scans started around the same
    time both computed report['id'] = 'scan-20260622-003' from the same
    stale directory listing, and the second write silently clobbered the
    first's report -- losing it and leaving the first scan's ledger event
    pointing at the wrong report. write_report's O_CREAT|O_EXCL claim must
    never allow that."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.scan_reports_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_does_not_overwrite_an_existing_report_with_the_same_guessed_id(self):
        (self.scan_reports_dir / "scan-20260622-003.json").write_text(json.dumps({"id": "scan-20260622-003", "hypothesis_id": "hyp-A"}))

        report = {"id": "scan-20260622-003", "hypothesis_id": "hyp-B"}
        path = write_report(report, self.scan_reports_dir)

        self.assertEqual(report["id"], "scan-20260622-004")
        self.assertEqual(path.name, "scan-20260622-004.json")
        self.assertEqual(json.loads((self.scan_reports_dir / "scan-20260622-003.json").read_text())["hypothesis_id"], "hyp-A")
        self.assertEqual(json.loads(path.read_text())["hypothesis_id"], "hyp-B")

    def test_skips_past_multiple_colliding_ids(self):
        for seq in (3, 4, 5):
            (self.scan_reports_dir / f"scan-20260622-{seq:03d}.json").write_text("{}")

        report = {"id": "scan-20260622-003", "hypothesis_id": "hyp-C"}
        path = write_report(report, self.scan_reports_dir)

        self.assertEqual(report["id"], "scan-20260622-006")
        self.assertEqual(path.name, "scan-20260622-006.json")

    def test_normal_case_uses_the_guessed_id_unchanged(self):
        report = {"id": "scan-20260622-001", "hypothesis_id": "hyp-D"}
        path = write_report(report, self.scan_reports_dir)
        self.assertEqual(report["id"], "scan-20260622-001")
        self.assertEqual(path.name, "scan-20260622-001.json")


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestRunScanIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = yaml.safe_load((_ROOT / "tests" / "fixtures" / "valid_spec.yaml").read_text())
        cls.report = run_scan(spec, "2026-05-01", "2026-05-02")

    def test_report_matches_schema(self):
        schema = json.loads(_SCHEMA_PATH.read_text())
        jsonschema.Draft202012Validator(schema).validate(self.report)

    def test_market_context_has_real_measured_spread(self):
        ctx = self.report["market_context"]
        # GMO/BTC_JPY's real half-spread for this exact window is ~0.92bps
        # (computed directly, not the spec's old stale full-history guess).
        self.assertGreater(ctx["avg_half_spread_bps"], 0.5)
        self.assertLess(ctx["avg_half_spread_bps"], 2.0)
        self.assertGreater(ctx["realized_volatility_bps"], 0)

    def test_market_context_lead_lag_confirms_binance_leads(self):
        # Measured directly from bronze for exactly this window (not
        # Makalu's gold LeadLag dataset, whose coverage doesn't match
        # every scanned period) -- should confirm BINANCE leads GMO, the
        # premise every cross-market hypothesis in this project relies on.
        lead_lag = self.report["market_context"]["lead_lag"]
        self.assertIsNotNone(lead_lag)
        self.assertGreater(lead_lag["feature_leads_fraction"], 0.5)
        self.assertGreater(lead_lag["mean_best_lag_seconds"], 0)
        self.assertGreaterEqual(lead_lag["mean_best_corr"], lead_lag["mean_corr_at_zero"])

    def test_regime_restricted_available_for_a_market_with_a_precomputed_artifact(self):
        # markets/regimes/BINANCE_BTC_USDT__GMO_BTC_JPY.json exists --
        # should be picked up and used, not silently skipped.
        rr = self.report["regime_restricted"]
        self.assertTrue(rr["available"])
        self.assertEqual(rr["regime_key"], "BINANCE_BTC_USDT__GMO_BTC_JPY")
        self.assertGreater(rr["fraction_of_rows"], 0)
        self.assertLess(rr["fraction_of_rows"], 1)
        self.assertIsNotNone(rr["metrics"]["ic_pooled"])

    def test_regime_restricted_unavailable_for_a_market_with_no_artifact(self):
        import yaml

        from scanner import run_scan

        spec = yaml.safe_load((_ROOT / "tests" / "fixtures" / "valid_spec.yaml").read_text())
        spec["hypothesis"]["target"]["source_dataset"]["symbol"] = "ETH_JPY"  # no regime artifact exists for this
        spec["hypothesis"]["feature"]["source_datasets"][0]["symbol"] = "ETH_USDT"
        report = run_scan(spec, "2026-05-01", "2026-05-02")
        rr = report["regime_restricted"]
        self.assertFalse(rr["available"])
        self.assertIn("reason", rr)

    def test_ic_and_hit_rate_are_positive_and_modest(self):
        # Matches the manually-computed result for this exact case: IC ~0.05,
        # hit rate ~0.52 -- a real but small effect, not a fluke and not huge.
        m = self.report["metrics"]
        self.assertGreater(m["ic_pooled"], 0.02)
        self.assertLess(m["ic_pooled"], 0.2)
        self.assertGreater(m["hit_rate"], 0.5)
        self.assertLess(m["hit_rate"], 0.6)

    def test_effective_sample_count_much_smaller_than_raw(self):
        m = self.report["metrics"]
        self.assertLess(m["effective_sample_count"], m["raw_sample_count"] * 0.1)

    def test_decay_by_horizon_covers_all_tested_horizons(self):
        m = self.report["metrics"]
        horizons = {d["horizon_seconds"] for d in m["decay_by_horizon"]}
        self.assertEqual(horizons, {30, 120, 300})

    def test_significance_method_is_newey_west_not_block_bootstrap(self):
        # The v1 default switched after confirming block bootstrap is too
        # slow at real tick-level row counts -- see requirements.md.
        self.assertEqual(self.report["metrics"]["significance_method"], "newey_west_hac")

    def test_threshold_verdict_not_yet_evaluated(self):
        # Task 8's job, not task 7's.
        self.assertEqual(self.report["threshold_verdict"]["status"], "not_evaluated")

    def test_bucketed_returns_are_roughly_monotonic(self):
        buckets = sorted(self.report["metrics"]["bucketed_returns"], key=lambda b: b["bucket"])
        means = [b["mean_target_return"] for b in buckets]
        self.assertEqual(means, sorted(means))


if __name__ == "__main__":
    unittest.main()
