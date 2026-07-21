"""Stage C cadence: bounded (cycles / max_scans / stop signal), accumulates,
records a run-level cadence_digest, never promotes. Mocks gen + run_once."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import stage_c  # noqa: E402
from kilimanjaro.ledger.core import load_events  # noqa: E402

_SUMMARY = {
    "n_specs": 3,
    "counts": {"scan-passed": 1, "scan-failed": 2},
    "passed": [{"hypothesis_id": "h1", "headline": "ok", "review_packet": "p.md"}],
}


def _mock(generate_return=("a.yaml", "b.yaml"), summary=_SUMMARY):
    """(gen.generate, run_once) patched to fixed returns."""
    return (
        patch("kilimanjaro.driver.stage_c.gen.generate", return_value=list(generate_return)),
        patch("kilimanjaro.driver.stage_c.run_once", return_value=summary),
    )


class TestStageC(unittest.TestCase):
    def test_refuses_unbounded_run(self):
        with self.assertRaises(ValueError):
            stage_c.run_cadence("/tmp/q", "2026-01-01", "2026-06-30")  # no bound at all

    def test_runs_bounded_cycles_and_accumulates(self):
        gp, rp = _mock()
        with tempfile.TemporaryDirectory() as d, gp as g, rp as r:
            seen = []
            digest = stage_c.run_cadence(
                Path(d) / "q", "2026-01-01", "2026-06-30",
                cycles=3, gen_per_cycle=2, on_cycle=seen.append, record_digest=False,
            )
        self.assertEqual(g.call_count, 3)
        self.assertEqual(r.call_count, 3)
        self.assertEqual(digest.cycles, 3)
        self.assertEqual(digest.generated, 6)  # 2/cycle × 3
        self.assertEqual(digest.scanned, 9)  # 3/cycle × 3
        self.assertEqual(digest.scans_run, 9)  # (1 passed + 2 failed)/cycle × 3
        self.assertEqual(digest.counts, {"scan-passed": 3, "scan-failed": 6})
        self.assertEqual(len(digest.survivors), 3)  # parked, not promoted
        self.assertEqual(digest.stop_reason, "cycles")
        self.assertEqual(len(seen), 3)

    def test_dry_run_skips_generation_and_ledger(self):
        gp, rp = _mock(summary={"n_specs": 0, "counts": {}, "passed": []})
        with tempfile.TemporaryDirectory() as d, gp as g, rp as r:
            stage_c.run_cadence(Path(d) / "q", "2026-01-01", "2026-06-30", cycles=1, dry_run=True)
        g.assert_not_called()  # dry-run generates nothing
        r.assert_called_once()

    def test_gen_per_cycle_is_passed_through(self):
        gp, rp = _mock(generate_return=[], summary={"n_specs": 0, "counts": {}, "passed": []})
        with tempfile.TemporaryDirectory() as d, gp as g, rp:
            stage_c.run_cadence(
                Path(d) / "q", "2026-01-01", "2026-06-30",
                cycles=1, gen_per_cycle=7, record_digest=False,
            )
        self.assertEqual(g.call_args.args[1], 7)  # limit == gen_per_cycle

    def test_max_scans_cost_budget(self):
        # 3 grid scans / cycle; budget 5 → stops after 2 cycles (6 ≥ 5).
        gp, rp = _mock()
        with tempfile.TemporaryDirectory() as d, gp, rp as r:
            digest = stage_c.run_cadence(
                Path(d) / "q", "2026-01-01", "2026-06-30",
                max_scans=5, record_digest=False,
            )
        self.assertEqual(r.call_count, 2)
        self.assertEqual(digest.cycles, 2)
        self.assertEqual(digest.scans_run, 6)
        self.assertEqual(digest.stop_reason, "max_scans")

    def test_stop_requested_halts_after_current_cycle(self):
        # stop_requested alone satisfies the bound; flip it after cycle 1.
        flag = {"v": False}
        gp, rp = _mock()
        with tempfile.TemporaryDirectory() as d, gp, rp as r:
            digest = stage_c.run_cadence(
                Path(d) / "q", "2026-01-01", "2026-06-30",
                stop_requested=lambda: flag["v"],
                on_cycle=lambda cr: flag.__setitem__("v", True),
                record_digest=False,
            )
        self.assertEqual(r.call_count, 1)  # finished the in-flight cycle, then stopped
        self.assertEqual(digest.cycles, 1)
        self.assertEqual(digest.stop_reason, "stopped")

    def test_records_cadence_digest_to_ledger(self):
        gp, rp = _mock()
        with tempfile.TemporaryDirectory() as d, gp, rp:
            ledger = Path(d) / "events.jsonl"
            digest = stage_c.run_cadence(
                Path(d) / "q", "2026-01-01", "2026-06-30",
                cycles=2, ledger_path=ledger,
            )
            events = load_events(ledger)
        digests = [e for e in events if e["event_type"] == "cadence_digest"]
        self.assertEqual(len(digests), 1)
        ev = digests[0]
        self.assertEqual(ev["hypothesis_id"], digest.run_id)
        self.assertTrue(ev["hypothesis_id"].startswith("run-"))
        det = ev["details"]
        self.assertEqual(det["cycles"], 2)
        self.assertEqual(det["scans_run"], 6)
        self.assertEqual(det["verdicts"], {"scan-passed": 2, "scan-failed": 4})
        self.assertEqual(det["n_survivors"], 2)
        self.assertEqual(det["window"], ["2026-01-01", "2026-06-30"])
        self.assertEqual(det["stop_reason"], "cycles")


if __name__ == "__main__":
    unittest.main()
