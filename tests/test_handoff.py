"""Proves implementation-tasks.md task 10's acceptance criteria for the
handoff packet generator:

- handoff cannot be generated from any status other than backtest-approved;
- handoff requires a prior human_approval ledger event;
- handoff includes entry/exit sketch, parameter grid, split windows,
  execution assumptions, expected outputs, and links to hypothesis/scan;
- handoff generation does not itself perform the backtest-approved
  transition;
- handoff does not create strategy code (only ever descriptive text/data).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from handoff import HandoffError, create_handoff, write_handoff  # noqa: E402
from ledger import append_event, load_events  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures"
_HYP_ID = "hyp-20260618-001"


def _spec() -> dict:
    base = yaml.safe_load((_FIXTURES / "valid_spec.yaml").read_text())
    base["hypothesis"]["id"] = _HYP_ID
    return base


_REPORT = {
    "schema_version": 1,
    "id": "scan-20260618-001",
    "hypothesis_id": _HYP_ID,
    "data_window": {"exchange": "GMO", "symbol": "BTC_JPY", "start_date": "2026-05-01", "end_date": "2026-05-15", "distinct_days": 15},
    "metrics": {"ic_pooled": 0.17},
}


class TestHandoff(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.specs_dir = root / "specs"
        self.ledger_path = root / "ledger" / "events.jsonl"
        self.scan_reports_dir = root / "scan-reports"
        self.handoffs_dir = root / "handoffs"
        self.specs_dir.mkdir(parents=True)
        self.scan_reports_dir.mkdir(parents=True)
        (self.specs_dir / f"{_HYP_ID}.yaml").write_text(yaml.dump(_spec(), sort_keys=False))
        (self.scan_reports_dir / "scan-20260618-001.json").write_text(json.dumps(_REPORT))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _scan_passed(self):
        append_event(
            hypothesis_id=_HYP_ID, event_type="status_transition", actor_type="agent", actor_id="scanner-v1",
            from_status="scan-ready", to_status="scan-passed",
            artifact_ids={"spec_id": _HYP_ID, "scan_report_id": "scan-20260618-001"},
            ledger_path=self.ledger_path,
        )

    def _approve(self):
        append_event(
            hypothesis_id=_HYP_ID, event_type="human_approval", actor_type="human", actor_id="reviewer-1",
            from_status="scan-passed", to_status="backtest-approved",
            ledger_path=self.ledger_path,
        )

    def test_refuses_when_not_backtest_approved(self):
        self._scan_passed()  # never approved
        with self.assertRaises(HandoffError):
            create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)

    def test_refuses_without_human_approval_event_even_if_status_says_otherwise(self):
        # backtest-approved reached via a status_transition event, not the
        # required human_approval event -- must still be refused, since the
        # whole point is a record of an actual human decision, not just the
        # right string in to_status.
        self._scan_passed()
        append_event(
            hypothesis_id=_HYP_ID, event_type="status_transition", actor_type="agent", actor_id="some-agent",
            from_status="scan-passed", to_status="backtest-approved",
            ledger_path=self.ledger_path,
        )
        with self.assertRaises(HandoffError):
            create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)

    def test_generates_handoff_after_approval(self):
        self._scan_passed()
        self._approve()
        handoff = create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)
        d = handoff["handoff"]

        self.assertEqual(d["hypothesis_id"], _HYP_ID)
        self.assertEqual(d["scan_report_id"], "scan-20260618-001")
        self.assertTrue(d["ledger_approval_event_id"].startswith("evt-"))
        self.assertIn("Exit at the primary horizon", d["entry_exit_sketch"])
        self.assertEqual(d["himalaya_run_config"]["execution_model"]["fees_bps"], 0)
        self.assertTrue(d["himalaya_run_config"]["train_window"])
        self.assertTrue(d["himalaya_run_config"]["test_window"])
        self.assertEqual(d["expected_outputs"][0], "net_pnl_after_costs")

    def test_entry_exit_direction_follows_measured_ic_sign(self):
        self._scan_passed()
        self._approve()
        handoff = create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)
        self.assertIn("long when the signal is positive", handoff["handoff"]["entry_exit_sketch"])

    def test_does_not_perform_the_approval_transition_itself(self):
        self._scan_passed()
        self._approve()
        events_before = load_events(self.ledger_path)
        create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)
        events_after = load_events(self.ledger_path)
        self.assertEqual(events_before, events_after)

    def test_does_not_generate_strategy_code(self):
        self._scan_passed()
        self._approve()
        handoff = create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)
        serialized = json.dumps(handoff)
        for keyword in ("def ", "import ", "class ", "qate.trading.strategy."):
            self.assertNotIn(keyword, serialized)

    def test_write_handoff_creates_yaml_and_markdown(self):
        self._scan_passed()
        self._approve()
        handoff = create_handoff(_HYP_ID, self.specs_dir, self.ledger_path, self.scan_reports_dir)
        yaml_path, md_path = write_handoff(handoff, self.handoffs_dir)

        self.assertTrue(yaml_path.is_file())
        self.assertTrue(md_path.is_file())
        round_tripped = yaml.safe_load(yaml_path.read_text())
        self.assertEqual(round_tripped, handoff)
        self.assertIn("do not hand-edit this file", md_path.read_text())


if __name__ == "__main__":
    unittest.main()
