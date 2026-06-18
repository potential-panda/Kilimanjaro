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


if __name__ == "__main__":
    unittest.main()
