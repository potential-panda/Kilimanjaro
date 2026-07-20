"""Stage C cadence: bounded, accumulates, never promotes. Mocks gen + run_once."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import stage_c  # noqa: E402

_SUMMARY = {
    "n_specs": 3,
    "counts": {"scan-passed": 1, "scan-failed": 2},
    "passed": [{"hypothesis_id": "h1", "headline": "ok", "review_packet": "p.md"}],
}


class TestStageC(unittest.TestCase):
    def test_refuses_unbounded_run(self):
        with self.assertRaises(ValueError):
            stage_c.run_cadence("/tmp/q", "2026-01-01", "2026-06-30")  # no bound

    def test_runs_bounded_cycles_and_accumulates(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "kilimanjaro.driver.stage_c.gen.generate", return_value=["a.yaml", "b.yaml"]
        ) as g, patch(
            "kilimanjaro.driver.stage_c.run_once", return_value=_SUMMARY
        ) as r:
            seen = []
            digest = stage_c.run_cadence(
                Path(d) / "q", "2026-01-01", "2026-06-30",
                cycles=3, gen_per_cycle=2, on_cycle=seen.append,
            )
        self.assertEqual(g.call_count, 3)
        self.assertEqual(r.call_count, 3)
        self.assertEqual(digest.cycles, 3)
        self.assertEqual(digest.generated, 6)  # 2/cycle × 3
        self.assertEqual(digest.scanned, 9)  # 3/cycle × 3
        self.assertEqual(digest.counts, {"scan-passed": 3, "scan-failed": 6})
        self.assertEqual(len(digest.survivors), 3)  # parked, not promoted
        self.assertEqual(len(seen), 3)

    def test_dry_run_skips_generation(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "kilimanjaro.driver.stage_c.gen.generate"
        ) as g, patch(
            "kilimanjaro.driver.stage_c.run_once",
            return_value={"n_specs": 0, "counts": {}, "passed": []},
        ) as r:
            stage_c.run_cadence(Path(d) / "q", "2026-01-01", "2026-06-30", cycles=1, dry_run=True)
        g.assert_not_called()  # dry-run generates nothing
        r.assert_called_once()

    def test_gen_per_cycle_is_passed_through(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "kilimanjaro.driver.stage_c.gen.generate", return_value=[]
        ) as g, patch(
            "kilimanjaro.driver.stage_c.run_once",
            return_value={"n_specs": 0, "counts": {}, "passed": []},
        ):
            stage_c.run_cadence(Path(d) / "q", "2026-01-01", "2026-06-30", cycles=1, gen_per_cycle=7)
        self.assertEqual(g.call_args.args[1], 7)  # limit == gen_per_cycle


if __name__ == "__main__":
    unittest.main()
