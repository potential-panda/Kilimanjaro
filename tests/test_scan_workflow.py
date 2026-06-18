"""Proves implementation-tasks.md task 9's acceptance criteria for the
ledger update workflow:

- Every valid spec receives a ledger entry.
- Failed scans preserve failed threshold reasons.
- Scan-passed entries are marked for human review, not auto-approved.

Uses temporary scan-reports/ledger directories so this never touches the
real ones. Real integration test against live Makalu data -- skipped
automatically if it isn't present, since this is the same canonical
example worked through by hand and the whole point is confirming the
full pipeline (validate -> scan -> evaluate -> ledger) agrees with it.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from ledger import load_events  # noqa: E402
from scan_workflow import WorkflowError, run_scan_workflow  # noqa: E402

_ROOT = Path(__file__).parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures"
_MAKALU_DATA_ROOT = Path("/panda/makalu-data")


@unittest.skipUnless(_MAKALU_DATA_ROOT.is_dir(), "real Makalu data not present")
class TestScanWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.scan_reports_dir = Path(self.tmpdir.name) / "scan-reports"
        self.ledger_path = Path(self.tmpdir.name) / "ledger" / "events.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_valid_spec_receives_a_ledger_entry_with_preserved_reason_and_links(self):
        # One real scan, several independent assertions on it -- avoids
        # paying for a fresh ~15s real scan per assertion.
        result = run_scan_workflow(
            _FIXTURES / "valid_spec.yaml", "2026-05-01", "2026-05-05",
            scan_reports_dir=self.scan_reports_dir, ledger_path=self.ledger_path,
        )
        events = load_events(self.ledger_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["hypothesis_id"], "hyp-20260618-001")

        # This exact spec/window is known (from the hand-worked example and
        # tests/test_threshold_evaluator.py) to fail only the cost buffer.
        self.assertEqual(result["report"]["threshold_verdict"]["status"], "fail")
        event = result["ledger_event"]
        self.assertEqual(event["to_status"], "scan-failed")
        self.assertEqual(event["decision_reason"], "failed-cost-buffer")

        self.assertEqual(event["artifact_ids"]["spec_id"], "hyp-20260618-001")
        self.assertEqual(event["artifact_ids"]["scan_report_id"], result["report"]["id"])

    def test_repeated_runs_use_ledger_status_not_stale_spec_status(self):
        first = run_scan_workflow(
            _FIXTURES / "valid_spec.yaml", "2026-05-01", "2026-05-05",
            scan_reports_dir=self.scan_reports_dir, ledger_path=self.ledger_path,
        )
        self.assertEqual(first["ledger_event"]["from_status"], "draft")  # spec's initial status

        second = run_scan_workflow(
            _FIXTURES / "valid_spec.yaml", "2026-05-01", "2026-05-05",
            scan_reports_dir=self.scan_reports_dir, ledger_path=self.ledger_path,
        )
        self.assertEqual(second["ledger_event"]["from_status"], first["ledger_event"]["to_status"])
        self.assertEqual(len(load_events(self.ledger_path)), 2)

    def test_invalid_spec_is_refused_before_touching_scanner_or_ledger(self):
        with self.assertRaises(WorkflowError):
            run_scan_workflow(
                _FIXTURES / "invalid_missing_mechanism.yaml", "2026-05-01", "2026-05-05",
                scan_reports_dir=self.scan_reports_dir, ledger_path=self.ledger_path,
            )
        self.assertFalse(self.scan_reports_dir.exists())
        self.assertFalse(self.ledger_path.exists())

    def test_scan_passed_never_auto_escalates_to_backtest_approved(self):
        # No fixture in this repo actually passes every threshold (the
        # canonical example fails cost-buffer) -- this proves the mapping
        # itself can never produce backtest-approved, regardless of verdict,
        # by checking the workflow's status map directly.
        from scan_workflow import _STATUS_MAP

        self.assertNotIn("backtest-approved", _STATUS_MAP.values())
        self.assertEqual(_STATUS_MAP["pass"], "scan-passed")


if __name__ == "__main__":
    unittest.main()
