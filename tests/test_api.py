"""Smoke tests for src/api.py -- a read-only viewer over specs/, ledger/,
and scan-reports/. Runs against the real, committed data in this repo
(not a scratch fixture) since the whole point is visualizing what's
actually there; these tests just confirm the routes don't crash and
return the expected shape.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api import app  # noqa: E402

_ROOT = Path(__file__).parent.parent


class TestApi(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_hypotheses_list_matches_real_specs_count(self):
        resp = self.client.get("/api/hypotheses")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        expected = len(list((_ROOT / "specs").glob("hyp-*.yaml")))
        self.assertEqual(len(data), expected)
        self.assertGreater(expected, 0)

    def test_hypothesis_detail_has_spec_and_events(self):
        hyp_id = self.client.get("/api/hypotheses").get_json()[0]["id"]
        resp = self.client.get(f"/api/hypotheses/{hyp_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("spec", data)
        self.assertIn("events", data)
        self.assertEqual(data["spec"]["hypothesis"]["id"], hyp_id)

    def test_hypothesis_detail_404_for_unknown_id(self):
        resp = self.client.get("/api/hypotheses/hyp-99999999-999")
        self.assertEqual(resp.status_code, 404)

    def test_scan_report_returns_real_content(self):
        hyp = self.client.get("/api/hypotheses").get_json()[0]
        report_id = hyp["latest_scan_report_id"]
        if report_id is None:
            self.skipTest("no scan report for the first hypothesis")
        resp = self.client.get(f"/api/scan-reports/{report_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("threshold_verdict", resp.get_json())

    def test_scan_report_404_for_unknown_id(self):
        resp = self.client.get("/api/scan-reports/scan-00000000-999")
        self.assertEqual(resp.status_code, 404)

    def test_ledger_returns_a_list(self):
        resp = self.client.get("/api/ledger")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_index_serves_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Kilimanjaro", resp.data)


if __name__ == "__main__":
    unittest.main()
