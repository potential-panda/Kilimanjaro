"""tools/grid_ledger.py: a completed grid run becomes one schema-valid
scan_attached ledger event, written to the given ledger path (a temp file
here — the real ledger is never touched by tests)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.ledger.grid import record_grid_scan


def _fake_res(stable_rows):
    return {
        "rows": [],
        "stable": stable_rows,
        "windows": {"train": ["2025-07-01", "2026-02-28"],
                    "holdout": ["2026-03-01", "2026-06-30"]},
        "params": {
            "rank_metric": "t_stat",
            "top_quantile": 0.10,
            "min_n": 30,
            "n_combos": 245,
            "costs_bps": 1.0,
            "gates": [{"rolling_dd_window_seconds": 10800,
                       "rolling_dd_threshold_bps": 500,
                       "rolling_dd_cooldown_seconds": 21600}],
        },
    }


class TestRecordGridScan(unittest.TestCase):
    def _record(self, res):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "events.jsonl"
            event = record_grid_scan(
                hypothesis_id="hyp-20260703-002",
                actor_id="grid-scan-flip",
                scan_id="ETH_2025-07-01_2026-06-30_x",
                out_dir=Path("cycle/15_return_eval/grid_scan/flip/ETH_x"),
                market="eth",
                res=res,
                ledger_path=path,
            )
            written = [json.loads(line) for line in path.read_text().splitlines()]
        return event, written

    def test_no_stable_run_recorded(self):
        event, written = self._record(_fake_res([]))
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0], event)
        self.assertEqual(event["event_type"], "scan_attached")
        self.assertEqual(event["hypothesis_id"], "hyp-20260703-002")
        self.assertEqual(event["artifact_ids"]["scan_report_ids"],
                         ["ETH_2025-07-01_2026-06-30_x"])
        d = event["details"]
        self.assertEqual(d["n_combos_tested"], 245)
        self.assertEqual(d["n_stable"], 0)
        self.assertIn("0/245", d["headline"])
        self.assertEqual(d["windows"]["holdout"], ["2026-03-01", "2026-06-30"])
        self.assertEqual(d["gates"][0]["rolling_dd_threshold_bps"], 500)

    def test_stable_run_headline_names_best_combo(self):
        stable = [{
            "box": 0.0015, "step": 0.05, "schedule": "active",
            "train_avg_net": 0.946, "train_t_stat": 1.949,
            "holdout_avg_net": 1.533, "holdout_t_stat": 2.382,
            "train_rank_pct": 0.95, "holdout_rank_pct": 1.0, "stable": True,
            "n_combos_tested": 245,
        }]
        event, _ = self._record(_fake_res(stable))
        h = event["details"]["headline"]
        self.assertIn("1/245", h)
        self.assertIn("box=0.0015", h)
        self.assertIn("schedule=active", h)
        self.assertIn("holdout +1.53 bps", h)


if __name__ == "__main__":
    unittest.main()
