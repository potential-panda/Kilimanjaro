"""The generator: proposes valid, deduped, data-viable formula specs.
available_features is mocked so tests stay data-free."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import generate as gen
from kilimanjaro.driver.validate import validate_scan_spec

# a market that has everything (leader) and a target that has only mid
_FULL = {"mid, spread_bps (execution_size=0.1)": {}, "obi (OBIFeature)": {},
         "ta_* (TradeAggression)": {}, "bs_* (BookState)": {}, "bc_* (BookChange)": {}}
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
            # one viable target x 14 families = 14 proposable ideas
            paths = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            self.assertEqual(len(paths), 14)
            # exhausted: a second call dedups against what it wrote -> nothing new
            paths2 = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            self.assertEqual(paths2, [])

    def test_generate_is_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "queue"
            first = gen.generate(q, limit=3, date_str="20260716", scan_dirs=[q])
            self.assertEqual(len(first), 3)          # first 3 families
            rest = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            self.assertEqual(len(rest), 11)          # remaining 11, deduped
            self.assertEqual(gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q]), [])

    def test_same_idea_on_many_markets_shares_one_hypothesis(self):
        """One idea, N market specs — not N near-identical hypotheses."""
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "12_specs"
            gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            by_family = {}
            for p in q.glob("*.yaml"):
                s = yaml.safe_load(p.read_text())
                by_family.setdefault(s["family"], set()).add(s["hypothesis_id"])
            for family, ids in by_family.items():
                self.assertEqual(len(ids), 1, f"{family} fragmented across {ids}")

    def test_new_spec_joins_the_hypothesis_already_covering_its_idea(self):
        """An existing spec's hypothesis is reused, even for a different market."""
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "12_specs"
            q.mkdir(parents=True)
            prior = {
                "hypothesis_id": "hyp-20260101-042",
                "family": "basis",
                "markets": {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT"}},
                "target": {"exchange": "KRAKEN", "symbol": "ETH_EUR"},
                "expressions": [{"expr": "zscore(a_mid / b_mid, 900)"}],
            }
            (q / "prior.yaml").write_text(yaml.safe_dump(prior))
            ids = gen.existing_idea_ids(q)
            self.assertIn(gen.idea_key(prior), ids)
            specs = gen.propose_specs(20, "20260716", idea_ids=ids)
            basis = [s for s in specs if s["family"] == "basis"]
            self.assertTrue(basis)
            for s in basis:
                self.assertEqual(s["hypothesis_id"], "hyp-20260101-042")

    def test_generate_writes_registry_beside_the_specs_not_the_repo(self):
        """A run against a throwaway specs dir must not touch the real registry.

        Regression: write_registry_entries once defaulted to the repo's
        cycle/11_hypotheses, so this very test suite wrote live hypothesis files.
        """
        repo_registry = Path(__file__).resolve().parents[1] / "cycle" / "11_hypotheses"
        before = {p.name for p in repo_registry.glob("hyp-*.yaml")}
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "12_specs"
            paths = gen.generate(q, limit=20, date_str="20260716", scan_dirs=[q])
            entries = sorted((Path(tmp) / "11_hypotheses").glob("hyp-*.yaml"))
            self.assertEqual(len(entries), len(paths))
        self.assertEqual({p.name for p in repo_registry.glob("hyp-*.yaml")}, before)


class TestIdeaConsistency(unittest.TestCase):
    """One idea -> one hypothesis (many specs). Enforced, not just conventional."""

    def _load_repo_specs(self):
        root = Path(__file__).resolve().parents[1] / "cycle" / "12_specs"
        return [yaml.safe_load(p.read_text()) for p in root.glob("*.yaml")]

    def test_repo_specs_are_idea_consistent(self):
        """The live spec tree must never split one idea across hypotheses, nor
        pin two ideas to one id. Fails loudly if a spec re-fragments them."""
        from kilimanjaro.driver.validate import idea_consistency_errors
        errors = idea_consistency_errors(self._load_repo_specs())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_detects_a_split_idea(self):
        from kilimanjaro.driver.validate import idea_consistency_errors
        base = {"family": "momentum", "target": {"exchange": "GMO", "symbol": "ETH_JPY"},
                "markets": {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT"}},
                "expressions": [{"expr": "zscore(ret(a_mid, 30), 1800)"}]}
        specs = [
            {**base, "hypothesis_id": "hyp-20260101-001"},
            {**base, "hypothesis_id": "hyp-20260101-002",  # same idea, different id
             "target": {"exchange": "GMO", "symbol": "BTC_JPY"},
             "markets": {"a": {"exchange": "BINANCE", "symbol": "BTC_USDT"}}},
        ]
        errors = idea_consistency_errors(specs)
        self.assertTrue(any("split across" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
