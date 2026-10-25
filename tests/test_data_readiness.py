"""Proves implementation-tasks.md task 5's acceptance criteria for spec
validation against a live Makalu inventory.

Unit tests cover the parts that don't need a real Makalu checkout: the
purely spec-derived unsupported-feature-transform checks, and the small
arithmetic helpers (_distinct_days_from_probe, _filesystem_distinct_days).
The integration test runs assess_data_readiness against the real,
already-proven-scannable canonical spec and confirms it actually reaches
scan-ready -- skipped automatically if the real Makalu checkout isn't
present, since mocking it would defeat the point.
"""

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from data_readiness import (
    _distinct_days_from_probe,
    _filesystem_distinct_days,
    _unsupported_transform_reasons,
    assess_data_readiness,
)

_ROOT = Path(__file__).parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures"
_MAKALU_ROOT = Path("/home/lulurun/workspace/Makalu")


def _load(name: str) -> dict:
    return yaml.safe_load((_FIXTURES / name).read_text())


class TestUnsupportedTransformReasons(unittest.TestCase):
    def test_valid_spec_has_no_reasons(self):
        spec = _load("valid_spec.yaml")
        self.assertEqual(_unsupported_transform_reasons(spec["hypothesis"]), [])

    def test_research_label_target_type_unsupported(self):
        spec = _load("invalid_research_label_no_version.yaml")
        reasons = _unsupported_transform_reasons(spec["hypothesis"])
        self.assertTrue(any("target.type" in r for r in reasons))

    def test_non_orderbook_source_dataset_unsupported(self):
        spec = _load("invalid_research_label_no_version.yaml")
        reasons = _unsupported_transform_reasons(spec["hypothesis"])
        self.assertTrue(any("TripleBarrierLabel" in r and "OrderBook" in r for r in reasons))

    def test_incomplete_research_metadata_unsupported(self):
        spec = _load("invalid_research_label_no_version.yaml")
        reasons = _unsupported_transform_reasons(spec["hypothesis"])
        self.assertTrue(any("metadata_status" in r for r in reasons))


class TestDistinctDaysHelpers(unittest.TestCase):
    def test_distinct_days_from_probe_subtracts_missing(self):
        probe = {"min_date": "2026-01-01", "max_date": "2026-01-10", "missing_count": 2}
        self.assertEqual(_distinct_days_from_probe(probe), 8)

    def test_distinct_days_from_probe_none_without_dates(self):
        self.assertIsNone(_distinct_days_from_probe({"status": "unknown"}))

    def test_filesystem_distinct_days_counts_date_dirs(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            base = data_root / "bronze" / "dataset=OrderBook" / "exchange=GMO" / "symbol=BTC_JPY"
            for d in ("date=2026-01-01", "date=2026-01-02", "date=2026-01-03"):
                (base / d).mkdir(parents=True)
            count = _filesystem_distinct_days("OrderBook", "bronze", "GMO", "BTC_JPY", data_root)
            self.assertEqual(count, 3)

    def test_filesystem_distinct_days_zero_when_no_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            count = _filesystem_distinct_days("OrderBook", "bronze", "GMO", "BTC_JPY", Path(tmp))
            self.assertEqual(count, 0)


@unittest.skipUnless(_MAKALU_ROOT.is_dir(), "real Makalu checkout not present")
class TestDataReadinessIntegration(unittest.TestCase):
    def test_canonical_spec_is_scan_ready(self):
        spec = _load("valid_spec.yaml")
        result = assess_data_readiness(spec, makalu_root=_MAKALU_ROOT)
        self.assertEqual(result["status"], "scan-ready")
        self.assertEqual(result["needs_tooling_reasons"], [])
        self.assertEqual(result["needs_more_data_reasons"], [])

    def test_research_label_spec_is_needs_tooling(self):
        spec = _load("invalid_research_label_no_version.yaml")
        result = assess_data_readiness(spec, makalu_root=_MAKALU_ROOT)
        self.assertEqual(result["status"], "needs-tooling")
        self.assertEqual(result["decision_reason"], "unsupported-feature-transform")


if __name__ == "__main__":
    unittest.main()
