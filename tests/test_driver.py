"""Driver (stage-A scanner loop): validation, policy, dedup, and the per-spec
pipeline that writes ledger events + a review packet. The scan itself is mocked
(run_spec) so these stay fast and data-free; run_spec is covered by the scan
tests and real runs."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import ledger_io  # noqa: E402
from kilimanjaro.driver.loop import process_spec  # noqa: E402
from kilimanjaro.driver.policy import evaluate  # noqa: E402
from kilimanjaro.driver.validate import validate_scan_spec  # noqa: E402

_GOOD_SPEC = {
    "hypothesis_id": "hyp-20260716-999",
    "markets": {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT", "execution_size": "0.1"}},
    "target": {"exchange": "GMO", "symbol": "ETH_JPY", "execution_size": "0.3"},
    "expressions": [{"label": "imb", "expr": "zscore(a_ta_imbalance_qty_5s, 1800)",
                     "invert": [False]}],
    "thresholds": [1.5],
    "horizons": [60],
}


def _row(stable, **over):
    r = {"expr": "imb", "threshold": 1.5, "invert": False, "min_age": 0, "max_age": 60,
         "horizon": 60, "train_avg_net": 1.2, "train_t_stat": 3.0, "holdout_avg_net": 1.1,
         "holdout_t_stat": 2.5, "holdout_rank_pct": 0.95, "holdout_n": 500,
         "train_pos_month_share": 0.8, "holdout_pos_month_share": 0.75, "verdict": "stable",
         "stable": stable}
    r.update(over)
    return r


def _fake_res(rows):
    return {
        "rows": rows, "stable": [r for r in rows if r["stable"]], "monthly_rows": [],
        "verdict_counts": {"stable": sum(r["stable"] for r in rows),
                           "noise": sum(not r["stable"] for r in rows)},
        "windows": {"train": ["2025-07-01", "2026-02-28"], "holdout": ["2026-03-01", "2026-06-30"]},
        "params": {"rank_metric": "t_stat", "top_quantile": 0.1, "min_n": 30,
                   "min_month_n": 10, "n_combos": len(rows), "costs_bps": 1.0, "gates": []},
    }


class TestValidate(unittest.TestCase):
    def test_good_spec_valid(self):
        self.assertEqual(validate_scan_spec(_GOOD_SPEC), [])

    def test_missing_hypothesis_id(self):
        s = {**_GOOD_SPEC}; del s["hypothesis_id"]
        self.assertTrue(any("hypothesis_id" in e for e in validate_scan_spec(s)))

    def test_bad_expression_rejected(self):
        s = {**_GOOD_SPEC, "expressions": [{"expr": "a_ta_x.rolling(3)"}]}
        self.assertTrue(validate_scan_spec(s))

    def test_unknown_alias_rejected(self):
        s = {**_GOOD_SPEC, "expressions": [{"expr": "zscore(b_mid, 60)"}]}  # 'b' not declared
        self.assertTrue(any("alias" in e for e in validate_scan_spec(s)))

    def test_fixed_horizon_needs_horizons(self):
        s = {**_GOOD_SPEC}; del s["horizons"]
        self.assertTrue(any("horizons" in e for e in validate_scan_spec(s)))

    def test_episode_ok_without_horizons(self):
        s = {**_GOOD_SPEC, "return_calc": "episode"}; del s["horizons"]
        self.assertEqual(validate_scan_spec(s), [])


class TestPolicy(unittest.TestCase):
    def test_passes_with_a_stable_combo(self):
        out = evaluate(_fake_res([_row(True), _row(False)]))
        self.assertTrue(out.passed)
        self.assertEqual(out.status, "scan-passed")
        self.assertEqual(out.n_stable, 1)

    def test_fails_with_no_stable(self):
        out = evaluate(_fake_res([_row(False), _row(False)]))
        self.assertFalse(out.passed)
        self.assertEqual(out.status, "scan-failed")


class TestProcessSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "events.jsonl"
        self.results = Path(self.tmp.name) / "results"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, spec, rows, **kw):
        out_dir = self.results / "scan_x"
        out_dir.mkdir(parents=True, exist_ok=True)

        def fake_run_spec(spec, s, e, results_root, split=None, scan_name="scan", **_):
            return _fake_res(rows), out_dir, "scan_x"

        with patch("kilimanjaro.driver.loop.run_spec", side_effect=fake_run_spec):
            return process_spec(spec, "2025-07-01", "2026-06-30", scan_name="scan_x",
                                results_root=self.results, ledger_path=self.ledger, **kw)

    def _statuses(self):
        import json
        return [json.loads(l).get("to_status") for l in self.ledger.read_text().splitlines()]

    def test_passed_writes_packet_and_ledger(self):
        r = self._run(_GOOD_SPEC, [_row(True), _row(False)])
        self.assertEqual(r["status"], "scan-passed")
        self.assertTrue(Path(r["review_packet"]).exists())
        # scan-ready then scan-passed recorded (+ scan_attached has no to_status)
        self.assertIn("scan-ready", self._statuses())
        self.assertIn("scan-passed", self._statuses())

    def test_failed_no_packet(self):
        r = self._run(_GOOD_SPEC, [_row(False)])
        self.assertEqual(r["status"], "scan-failed")
        self.assertIsNone(r["review_packet"])
        self.assertIn("scan-failed", self._statuses())

    def test_invalid_spec_recorded_not_scanned(self):
        bad = {**_GOOD_SPEC}; del bad["thresholds"]
        r = self._run(bad, [_row(True)])
        self.assertEqual(r["status"], "invalid-spec")
        self.assertIn("invalid-spec", self._statuses())

    def test_dedup_skips_second_run(self):
        self._run(_GOOD_SPEC, [_row(True)])
        second = self._run(_GOOD_SPEC, [_row(True)])
        self.assertEqual(second["status"], "skipped-duplicate")

    def test_dry_run_no_ledger(self):
        r = self._run(_GOOD_SPEC, [_row(True)], dry_run=True)
        self.assertEqual(r["status"], "dry-run")
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main()
