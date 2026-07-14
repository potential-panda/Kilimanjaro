"""Smoke tests for src/api.py -- the read-only viewer over
results/grid_scan/, specs/, and ledger/. Runs against the real, committed
data in this repo (not a scratch fixture) since the whole point is
visualizing what's actually there; these tests confirm the routes return
the expected shapes and that the file endpoint can't escape a scan folder.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api import app  # noqa: E402


class TestApi(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_scans_list(self):
        r = self.client.get("/api/scans")
        self.assertEqual(r.status_code, 200)
        scans = r.get_json()
        self.assertGreater(len(scans), 0)
        s = scans[0]
        for key in ("kind", "scan_id", "n_combos", "n_stable", "verdicts", "mtime"):
            self.assertIn(key, s)
        # newest first
        self.assertEqual([x["mtime"] for x in scans],
                         sorted((x["mtime"] for x in scans), reverse=True))

    def test_scan_detail_and_csv(self):
        scans = self.client.get("/api/scans").get_json()
        s = next(x for x in scans if x["verdicts"])  # a verdict-era run
        d = self.client.get(f"/api/scans/{s['kind']}/{s['scan_id']}").get_json()
        self.assertIn("output.csv", d["files"])
        self.assertEqual(d["params"].get("scan_id"), s["scan_id"])

        f = self.client.get(
            f"/api/scans/{s['kind']}/{s['scan_id']}/file/output.csv").get_json()
        self.assertIn("verdict", f["columns"])
        self.assertEqual(len(f["rows"]), s["n_combos"])
        self.assertTrue(all(len(row) == len(f["columns"]) for row in f["rows"][:5]))

    def test_scan_file_traversal_guarded(self):
        scans = self.client.get("/api/scans").get_json()
        s = scans[0]
        for bad in ("../params.json", "..%2f..%2fledger%2fevents.jsonl",
                    "output.csv/../params.json", "params.json"):
            r = self.client.get(f"/api/scans/{s['kind']}/{s['scan_id']}/file/{bad}")
            self.assertEqual(r.status_code, 404, bad)
        r = self.client.get("/api/scans/nope/x/file/output.csv")
        self.assertEqual(r.status_code, 404)

    def test_hypotheses(self):
        hyps = self.client.get("/api/hypotheses").get_json()
        ids = {h["id"] for h in hyps}
        self.assertIn("hyp-20260703-002", ids)
        retired = next(h for h in hyps if h["id"] == "hyp-20260703-002")
        self.assertEqual(retired["status"], "retired")

        d = self.client.get("/api/hypotheses/hyp-20260713-002").get_json()
        self.assertEqual(d["summary"]["id"], "hyp-20260713-002")
        self.assertGreater(len(d["events"]), 0)
        self.assertIn("hypothesis", d["spec"])

    def test_hypothesis_404(self):
        r = self.client.get("/api/hypotheses/hyp-00000000-000")
        self.assertEqual(r.status_code, 404)

    def test_spa_fallback(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"KILIMANJARO", r.data)
        r = self.client.get("/api/nonexistent")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
