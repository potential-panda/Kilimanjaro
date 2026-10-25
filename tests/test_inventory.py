"""Proves implementation-tasks.md task 4's acceptance criteria for the
Makalu inventory snapshot.

These are integration tests against the real Makalu instance at
/home/lulurun/workspace/Makalu (the same fixed dependency every
requirements/ doc assumes) -- mocking the CLI/filesystem would defeat the
point of a tool whose entire job is to verify what's actually true, not
what's assumed. Skipped automatically if that Makalu checkout isn't present.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import inventory

_MAKALU_ROOT = Path("/home/lulurun/workspace/Makalu")
_MARKETS = [{"exchange": "GMO", "symbol": "BTC_JPY"}, {"exchange": "BINANCE", "symbol": "BTC_USDT"}]


@unittest.skipUnless(_MAKALU_ROOT.is_dir(), "real Makalu checkout not present")
class TestInventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = inventory.build_inventory(_MARKETS, start_date="2026-01-01", end_date="2026-01-10")
        cls.by_name_exchange = {(d["dataset_name"], d["availability"]["exchange"]): d for d in cls.inv["datasets"]}

    def test_includes_all_five_layers(self):
        layers = {d["catalog_declared_layer"] for d in self.inv["datasets"]}
        self.assertEqual(layers, {"bronze", "silver", "gold", "feature", "research"})

    def test_bronze_is_guaranteed_others_are_candidate(self):
        for (name, _exchange), entry in self.by_name_exchange.items():
            if name in ("Trade", "OrderBook"):
                self.assertEqual(entry["reliability"], "guaranteed", name)
            else:
                self.assertEqual(entry["reliability"], "candidate", name)

    def test_reliability_is_not_inferred_from_catalog_declared_layer(self):
        # makalu-integration-contract.md's "Known Gap" originally documented
        # a real catalog/storage mismatch (catalog said "bronze" for
        # BookState, which is actually stored under silver, and Makalu's own
        # query CLI couldn't register it). Confirmed fixed upstream in
        # Makalu (2026-06-21): catalog_declared_layer now correctly says
        # "silver" and query_verified is True. The actual invariant this
        # test exists to prove still holds regardless: reliability must
        # never be inferred from catalog_declared_layer being present/
        # correct -- BookState is still only "candidate", never "guaranteed",
        # because reliability is about whether the dataset IS Trade/OrderBook,
        # not whether the catalog describes it accurately.
        book_state = self.by_name_exchange[("BookState", "GMO")]
        self.assertEqual(book_state["catalog_declared_layer"], "silver")
        self.assertEqual(book_state["reliability"], "candidate")
        self.assertTrue(book_state["query_verified"])
        self.assertEqual(book_state["resolved_layer"], "silver")

    def test_canonical_vs_research_ephemeral_distinguished(self):
        bronze_entry = self.by_name_exchange[("Trade", "GMO")]
        research_entry = self.by_name_exchange[("TripleBarrierLabel", "GMO")]
        self.assertEqual(bronze_entry["canonicality"], "canonical")
        self.assertEqual(research_entry["canonicality"], "research-ephemeral")

    def test_missing_dates_represented(self):
        # Invariant: every dataset reported "missing" carries a well-formed
        # missing_dates list (spans of {start, end}). We do NOT hardcode which
        # dataset is missing over which range — that tracks live
        # /panda/makalu-data state and drifts (Feature_NearHFT was backfilled
        # since the task-4 spike). The structure is what must hold.
        for d in self.inv["datasets"]:
            avail = d["availability"]
            self.assertIn("missing_dates", avail)
            self.assertIsInstance(avail["missing_dates"], list)
            for span in avail["missing_dates"]:
                self.assertIn("start", span)
                self.assertIn("end", span)

    def test_non_bronze_unavailability_routes_to_population_action_not_a_block(self):
        for (name, _exchange), entry in self.by_name_exchange.items():
            if name in ("Trade", "OrderBook"):
                continue
            if entry["availability"]["status"] != "available":
                self.assertIn(
                    entry["population_action"],
                    ("populate", "derive_from_bronze", "new_dataset_needed"),
                    name,
                )

    def test_extra_partition_datasets_skip_date_check_with_warning(self):
        vol_spread = self.by_name_exchange[("VolSpread", "GMO")]
        self.assertEqual(vol_spread["availability"]["status"], "unknown")
        self.assertTrue(any("extra partitions" in w for w in vol_spread["metadata_warnings"]))

    def test_no_makalu_writes(self):
        # Structural guarantee: inventory.py never shells out to `populate`
        # as an actual CLI argument (the word appears in comments/docstrings
        # describing the routing decision, which is fine -- only a literal
        # ["populate", ...]/["populate"] argument list would be a write).
        source = (Path(__file__).parent.parent / "tools" / "inventory.py").read_text()
        self.assertNotIn('["populate"', source)
        self.assertNotIn("'populate',", source)


if __name__ == "__main__":
    unittest.main()
