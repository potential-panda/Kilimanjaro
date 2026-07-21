"""The generator: proposes valid, deduped, data-viable formula specs.
available_features is mocked so tests stay data-free."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import generate as gen  # noqa: E402
from kilimanjaro.driver.validate import validate_scan_spec  # noqa: E402

# a market that has everything (leader) and a target that has only mid
_FULL = {"mid, spread_bps (execution_size=0.1)": {}, "obi (OBIFeature)": {},
         "ta_* (TradeAggression)": {}}
_MID_ONLY = {"mid, spread_bps (execution_size=0.3)": {},
             "mid, spread_bps (execution_size=0.1)": {}}


def _fake_available(exchange, symbol, data_root=None):
    if exchange == "BINANCE":
        return _FULL
    if (exchange, symbol) == ("GMO", "ETH_JPY"):
        return _MID_ONLY
    return {}  # other targets have no data


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.p = patch("kilimanjaro.driver.generate.available_features",
                       side_effect=_fake_available)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_proposals_are_valid_specs(self):
        specs = gen.propose_specs(limit=20, date_str="20260716")
        self.assertTrue(specs)
        for s in specs:
            self.assertEqual(validate_scan_spec(s), [], s.get("hypothesis_id"))

    def test_only_viable_pairs(self):
        # only GMO/ETH_JPY has data among targets -> every spec targets it
        specs = gen.propose_specs(limit=20, date_str="20260716")
        targets = {(s["target"]["exchange"], s["target"]["symbol"]) for s in specs}
        self.assertEqual(targets, {("GMO", "ETH_JPY")})

    def test_target_exec_size_is_largest_available(self):
        specs = gen.propose_specs(limit=20, date_str="20260716")
        self.assertTrue(all(s["target"]["execution_size"] == "0.3" for s in specs))

    def test_ids_sequential_from_seq_start(self):
        specs = gen.propose_specs(limit=3, date_str="20260716", seq_start=7)
        self.assertEqual([s["hypothesis_id"] for s in specs],
                         ["hyp-20260716-007", "hyp-20260716-008", "hyp-20260716-009"])

    def test_limit_respected(self):
        self.assertEqual(len(gen.propose_specs(limit=2, date_str="20260716")), 2)

    def test_diversified_across_families(self):
        # first few proposals should span multiple families, not all momentum
        specs = gen.propose_specs(limit=4, date_str="20260716")
        self.assertGreater(len({s["family"] for s in specs}), 1)

    def test_dedup_against_seen(self):
        first = gen.propose_specs(limit=20, date_str="20260716")
        seen = {gen.signature(s) for s in first}
        again = gen.propose_specs(limit=20, date_str="20260716", seen=seen)
        self.assertEqual(again, [])

    def test_write_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp)
            specs = gen.propose_specs(limit=3, date_str="20260716")
            paths = gen.write_specs(specs, q)
            self.assertEqual(len(paths), 3)
            # written files dedup against themselves
            seen = gen.existing_signatures(q)
            self.assertEqual(len(seen), 3)

    def test_generate_end_to_end_writes_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "queue"
            # one viable target x 5 families = 5 proposable ideas
            paths = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            self.assertEqual(len(paths), 5)
            # exhausted: a second call dedups against what it wrote -> nothing new
            paths2 = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            self.assertEqual(paths2, [])

    def test_generate_is_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "queue"
            first = gen.generate(q, limit=3, date_str="20260716", scan_dirs=[q])
            self.assertEqual(len(first), 3)          # first 3 families
            rest = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            self.assertEqual(len(rest), 2)           # remaining 2, deduped
            self.assertEqual(gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q]), [])


if __name__ == "__main__":
    unittest.main()
