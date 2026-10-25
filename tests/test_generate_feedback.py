"""Generator feedback: family ordering by past success (no market data)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver.generate import _priority_order, family_scores


class TestPriorityOrder(unittest.TestCase):
    _FAMS = [{"family": "a"}, {"family": "b"}, {"family": "c"}]

    def test_none_is_identity(self):
        self.assertEqual(_priority_order(self._FAMS, None), [0, 1, 2])

    def test_orders_by_priority_desc(self):
        # c highest, then a; b has no score -> last
        order = _priority_order(self._FAMS, {"c": 5.0, "a": 1.0})
        self.assertEqual(order, [2, 0, 1])

    def test_stable_for_ties(self):
        self.assertEqual(_priority_order(self._FAMS, {"a": 1, "b": 1, "c": 1}), [0, 1, 2])


class TestFamilyScores(unittest.TestCase):
    def _write_spec(self, d: Path, hyp: str, family: str):
        (d / f"{hyp}.yaml").write_text(
            f"hypothesis_id: {hyp}\nfamily: {family}\nexpressions:\n  - {{expr: x}}\n"
        )

    def _write_ledger(self, path: Path, events: list[dict]):
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    def test_counts_survivors_per_family_from_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            specs = Path(td) / "specs"
            specs.mkdir()
            self._write_spec(specs, "hyp-1", "momentum")
            self._write_spec(specs, "hyp-2", "basis")
            self._write_spec(specs, "hyp-3", "momentum")
            ledger = Path(td) / "events.jsonl"
            self._write_ledger(ledger, [
                {"hypothesis_id": "hyp-1", "to_status": "scan-passed"},
                {"hypothesis_id": "hyp-2", "to_status": "scan-failed"},   # not counted
                {"hypothesis_id": "hyp-3", "to_status": "backtest-approved"},
                {"hypothesis_id": "hyp-x", "to_status": "scan-passed"},   # no spec -> skip
            ])
            scores = family_scores(specs, ledger_path=ledger)
        self.assertEqual(scores, {"momentum": 2.0})  # basis failed; hyp-x unknown

    def test_empty_history_gives_empty_scores(self):
        with tempfile.TemporaryDirectory() as td:
            specs = Path(td) / "specs"
            specs.mkdir()
            self._write_spec(specs, "hyp-1", "momentum")
            ledger = Path(td) / "events.jsonl"
            ledger.write_text("")
            self.assertEqual(family_scores(specs, ledger_path=ledger), {})


if __name__ == "__main__":
    unittest.main()
