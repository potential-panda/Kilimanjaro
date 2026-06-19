"""Proves implementation-tasks.md task 2's acceptance criteria for the
hypothesis schema/validator:

- Valid sample spec passes.
- Missing mechanism fails.
- Missing duplicate search fails.
- Research-layer target without version metadata fails.

Plus two rules added after the lead-lag-aware signal-sourcing guidance in
direction/hypothesis-taxonomy.md and requirements/hypothesis-contract.md:
- a feature sourced from a different market than the target must justify
  why (e.g. BINANCE leading GMO);
- BINANCE is the *default* feature source for any target with a confirmed
  lead-lag leader (per direct user instruction) -- sourcing from the
  target's own market instead also requires justification, except for the
  local-execution-mechanics families where BINANCE has no analogue.

Plus task 6's SQL safety check (tools/sql_safety.py) wired into
feature.sql_expression validation -- see tests/test_sql_safety.py for the
full battery of allowed/rejected SQL cases; this file only proves it's
actually invoked from validate_spec.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from validate_spec import validate_file  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures"


class TestValidateSpec(unittest.TestCase):
    def test_valid_sample_spec_passes(self):
        errors = validate_file(str(_FIXTURES / "valid_spec.yaml"))
        self.assertEqual(errors, [])

    def test_missing_mechanism_fails(self):
        errors = validate_file(str(_FIXTURES / "invalid_missing_mechanism.yaml"))
        self.assertTrue(any("missing-mechanism" in e for e in errors), errors)

    def test_missing_duplicate_search_fails(self):
        errors = validate_file(str(_FIXTURES / "invalid_missing_duplicate_search.yaml"))
        self.assertTrue(any("duplicate search was not performed" in e for e in errors), errors)

    def test_research_label_without_version_metadata_fails(self):
        errors = validate_file(str(_FIXTURES / "invalid_research_label_no_version.yaml"))
        self.assertTrue(any("research_metadata.param_id is missing" in e for e in errors), errors)

    def test_cross_market_feature_without_rationale_fails(self):
        errors = validate_file(str(_FIXTURES / "invalid_cross_market_no_rationale.yaml"))
        self.assertTrue(any("cross_market_rationale is empty" in e for e in errors), errors)

    def test_same_market_feature_without_binance_default_rationale_fails(self):
        errors = validate_file(str(_FIXTURES / "invalid_same_market_no_binance_default.yaml"))
        self.assertTrue(any("BINANCE is the default feature source" in e for e in errors), errors)

    def test_local_mechanics_family_exempt_from_binance_default(self):
        import copy

        import yaml

        spec = yaml.safe_load((_FIXTURES / "invalid_same_market_no_binance_default.yaml").read_text())
        spec = copy.deepcopy(spec)
        spec["hypothesis"]["family"] = "queue_depletion"
        spec["hypothesis"]["family_label"] = "Queue depletion before price movement"
        from validate_spec import validate

        errors = validate(spec)
        self.assertFalse(any("BINANCE is the default feature source" in e for e in errors), errors)

    def test_unsafe_sql_expression_fails(self):
        errors = validate_file(str(_FIXTURES / "invalid_unsafe_sql_expression.yaml"))
        self.assertTrue(any("feature.sql_expression:" in e for e in errors), errors)

    def test_fees_bps_mismatched_with_registry_fails(self):
        # Found for real while running task 11's bootstrap hypotheses: every
        # one of them declared fees_bps: 1.0 against GMO BTC_JPY/ETH_JPY,
        # whose markets/registry.yaml fee_bps is actually 0 -- an arbitrary
        # guess silently overriding the project's own authoritative source.
        import copy

        import yaml

        from validate_spec import validate

        spec = copy.deepcopy(yaml.safe_load((_FIXTURES / "valid_spec.yaml").read_text()))
        spec["hypothesis"]["execution_assumptions"]["fees_bps"] = 1.0  # registry says GMO BTC_JPY is 0
        errors = validate(spec)
        self.assertTrue(any("does not match markets/registry.yaml's fee_bps" in e for e in errors), errors)

    def test_fees_bps_matching_registry_passes(self):
        import yaml

        from validate_spec import validate

        spec = yaml.safe_load((_FIXTURES / "valid_spec.yaml").read_text())
        errors = validate(spec)
        self.assertFalse(any("fee_bps" in e for e in errors), errors)

    def test_slippage_bps_mismatched_with_registry_fails(self):
        # markets/registry.yaml's default_slippage_bps is a real, user-
        # provided number (~1 year of actual trading), not a placeholder --
        # a spec silently using a different value should be caught the same
        # way a mismatched fees_bps is.
        import copy

        import yaml

        from validate_spec import validate

        spec = copy.deepcopy(yaml.safe_load((_FIXTURES / "valid_spec.yaml").read_text()))
        spec["hypothesis"]["execution_assumptions"]["slippage_bps"] = 5.0
        errors = validate(spec)
        self.assertTrue(any("does not match markets/registry.yaml's default_slippage_bps" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
