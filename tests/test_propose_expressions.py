import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from propose_expressions import propose_specs, write_specs  # noqa: E402
from validate_spec import validate  # noqa: E402


class TestProposeExpressions(unittest.TestCase):
    def test_generated_gmo_jpy_spec_uses_binance_leader_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs = propose_specs(
                specs_dir=Path(tmp),
                date_str="20260620",
                created_at="2026-06-20T00:00:00Z",
                limit=1,
                target_exchange="GMO",
                target_symbol="BTC_JPY",
            )

        self.assertEqual(len(specs), 1)
        spec = specs[0]
        h = spec["hypothesis"]
        source = h["feature"]["source_datasets"][0]
        target = h["target"]["source_dataset"]

        self.assertEqual(h["id"], "hyp-20260620-001")
        self.assertEqual((source["exchange"], source["symbol"]), ("BINANCE", "BTC_USDT"))
        self.assertEqual((target["exchange"], target["symbol"]), ("GMO", "BTC_JPY"))
        self.assertIn("leader market", h["cross_market_rationale"])
        self.assertEqual(validate(spec), [])

    def test_generated_bitbank_spot_spec_stays_same_market_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs = propose_specs(
                specs_dir=Path(tmp),
                date_str="20260620",
                created_at="2026-06-20T00:00:00Z",
                limit=1,
                target_exchange="BITBANK",
                target_symbol="XRP_SPOT",
            )

        h = specs[0]["hypothesis"]
        source = h["feature"]["source_datasets"][0]
        target = h["target"]["source_dataset"]

        self.assertEqual((source["exchange"], source["symbol"]), ("BITBANK", "XRP_SPOT"))
        self.assertEqual((target["exchange"], target["symbol"]), ("BITBANK", "XRP_SPOT"))
        self.assertIsNone(h["cross_market_rationale"])
        self.assertEqual(h["execution_assumptions"]["order_type"], "limit")
        self.assertEqual(h["execution_assumptions"]["fees_bps"], -0.02)
        self.assertEqual(validate(specs[0]), [])

    def test_write_specs_advances_after_existing_sequence_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            (specs_dir / "hyp-20260620-001.yaml").write_text("placeholder\n")

            specs = propose_specs(
                specs_dir=specs_dir,
                date_str="20260620",
                created_at="2026-06-20T00:00:00Z",
                limit=2,
                target_exchange="COINCHECK",
                target_symbol="BTC_SPOT",
            )
            paths = write_specs(specs, specs_dir)

            self.assertEqual([p.name for p in paths], ["hyp-20260620-002.yaml", "hyp-20260620-003.yaml"])
            loaded = yaml.safe_load(paths[0].read_text())
            self.assertEqual(validate(loaded), [])


if __name__ == "__main__":
    unittest.main()
