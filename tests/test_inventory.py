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

import inventory  # noqa: E402

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
        # BookState/BookChange's catalog_declared_layer says "bronze" (a known
        # mismatch -- see makalu-integration-contract.md's "Known Gap") but
        # reliability must reflect that they are NOT actually Trade/OrderBook,
        # not the catalog's claim.
        book_state = self.by_name_exchange[("BookState", "GMO")]
        self.assertEqual(book_state["catalog_declared_layer"], "bronze")
        self.assertEqual(book_state["reliability"], "candidate")
        self.assertFalse(book_state["query_verified"])
        self.assertEqual(book_state["resolved_layer"], "silver")

    def test_canonical_vs_research_ephemeral_distinguished(self):
        bronze_entry = self.by_name_exchange[("Trade", "GMO")]
        research_entry = self.by_name_exchange[("TripleBarrierLabel", "GMO")]
        self.assertEqual(bronze_entry["canonicality"], "canonical")
        self.assertEqual(research_entry["canonicality"], "research-ephemeral")

    def test_missing_dates_represented(self):
        # Feature_NearHFT has no populated data for either test market (only
        # BINANCE/ETH_USDT and BINANCE/XRP_USDT are populated) -- confirmed
        # directly against /panda/makalu-data during the task-4 spike.
        entry = self.by_name_exchange[("Feature_NearHFT", "GMO")]
        self.assertEqual(entry["availability"]["status"], "missing")
        self.assertEqual(
            entry["availability"]["missing_dates"],
            [{"start": "2026-01-01", "end": "2026-01-10"}],
        )

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
