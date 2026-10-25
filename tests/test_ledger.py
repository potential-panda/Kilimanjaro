"""Proves implementation-tasks.md task 3's acceptance criteria for the
ledger schema/search helper:

- Agent can find prior rejected ideas.
- Variants link to parent ids.
- Duplicate entry can be marked without running a scan.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.ledger.core import append_event, load_hypotheses, search

_FIXTURES = Path(__file__).parent / "fixtures"


def _spec(hyp_id, parent_id, family="order_book_imbalance", title="t", status="draft"):
    base = yaml.safe_load((_FIXTURES / "valid_spec.yaml").read_text())
    base["hypothesis"]["id"] = hyp_id
    base["hypothesis"]["parent_id"] = parent_id
    base["hypothesis"]["family"] = family
    base["hypothesis"]["title"] = title
    base["hypothesis"]["status"] = status
    return base


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.hyp_dir = Path(self.tmpdir.name) / "hypotheses"
        self.ledger_path = Path(self.tmpdir.name) / "ledger" / "events.jsonl"
        self.hyp_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_spec(self, spec: dict) -> None:
        path = self.hyp_dir / f"{spec['hypothesis']['id']}.yaml"
        path.write_text(yaml.dump(spec, sort_keys=False))

    def test_find_prior_rejected_ideas(self):
        self._write_spec(_spec("hyp-20260618-001", None, title="imbalance idea"))
        append_event(
            hypothesis_id="hyp-20260618-001",
            event_type="status_transition",
            actor_type="agent",
            actor_id="agent-1",
            from_status="scan-ready",
            to_status="scan-failed",
            decision_reason="failed-ic-threshold",
            artifact_ids={"spec_id": "hyp-20260618-001", "scan_report_id": "scan-20260618-001"},
            ledger_path=self.ledger_path,
        )

        results = search(
            family="order_book_imbalance",
            rejected_only=True,
            hypotheses_dir=self.hyp_dir,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "hyp-20260618-001")
        self.assertEqual(results[0]["current_status"], "scan-failed")
        self.assertIn("failed-ic-threshold", results[0]["decision_reasons_seen"])
        self.assertTrue(results[0]["rejected"])

        # An unrelated, non-rejected hypothesis must not show up under rejected_only.
        self._write_spec(_spec("hyp-20260618-002", None, title="unrelated idea"))
        results = search(rejected_only=True, hypotheses_dir=self.hyp_dir, ledger_path=self.ledger_path)
        self.assertEqual([r["id"] for r in results], ["hyp-20260618-001"])

    def test_variants_link_to_parent_ids(self):
        self._write_spec(_spec("hyp-20260618-010", None, title="parent idea"))
        self._write_spec(_spec("hyp-20260618-010-v01", "hyp-20260618-010", title="parent idea, tighter horizon"))

        specs = load_hypotheses(self.hyp_dir)
        variant = specs["hyp-20260618-010-v01"]["hypothesis"]
        self.assertEqual(variant["parent_id"], "hyp-20260618-010")

        results = search(hypotheses_dir=self.hyp_dir, ledger_path=self.ledger_path)
        by_id = {r["id"]: r for r in results}
        self.assertEqual(by_id["hyp-20260618-010-v01"]["parent_id"], "hyp-20260618-010")
        self.assertIsNone(by_id["hyp-20260618-010"]["parent_id"])

    def test_duplicate_entry_marked_without_scan(self):
        self._write_spec(_spec("hyp-20260618-020", None, title="rediscovered idea", status="duplicate"))

        event = append_event(
            hypothesis_id="hyp-20260618-020",
            event_type="status_transition",
            actor_type="agent",
            actor_id="agent-1",
            from_status="draft",
            to_status="duplicate",
            decision_reason="duplicate",
            artifact_ids={"spec_id": "hyp-20260618-020"},  # no scan_report_id
            details={"duplicate_of": "hyp-20260618-001"},
            ledger_path=self.ledger_path,
        )
        self.assertEqual(event["to_status"], "duplicate")
        self.assertNotIn("scan_report_id", event["artifact_ids"])

        results = search(decision_reason="duplicate", hypotheses_dir=self.hyp_dir, ledger_path=self.ledger_path)
        self.assertEqual([r["id"] for r in results], ["hyp-20260618-020"])
        self.assertTrue(results[0]["rejected"])


if __name__ == "__main__":
    unittest.main()
