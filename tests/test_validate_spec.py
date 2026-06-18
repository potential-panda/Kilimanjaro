"""Proves implementation-tasks.md task 2's acceptance criteria for the
hypothesis schema/validator:

- Valid sample spec passes.
- Missing mechanism fails.
- Missing duplicate search fails.
- Research-layer target without version metadata fails.
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


if __name__ == "__main__":
    unittest.main()
