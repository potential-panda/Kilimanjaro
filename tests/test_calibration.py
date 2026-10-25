"""Calibration registry: mechanism status + spec derivation (no data)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework import calibration as C


class TestCalibrationRegistry(unittest.TestCase):
    def test_flip_is_calibrated(self):
        self.assertTrue(C.is_calibrated("flip+between_signal"))
        self.assertEqual(C.lookup("flip+between_signal").status, C.CALIBRATED)

    def test_expression_mechanisms_are_trend_only(self):
        self.assertFalse(C.is_calibrated("expression+fixed_horizon"))
        self.assertFalse(C.is_calibrated("expression+episode"))

    def test_unknown_defaults_to_trend_only(self):
        cal = C.lookup("something+new")
        self.assertEqual(cal.status, C.TREND_ONLY)
        self.assertFalse(cal.is_calibrated)

    def test_spec_mechanism_derivation(self):
        flip = {"signal_family": "flip", "markets": {}, "target": {}}
        self.assertEqual(C.spec_mechanism(flip), "flip+between_signal")
        # a spec without `expressions` is the flip grid
        self.assertEqual(C.spec_mechanism({"target": {}}), "flip+between_signal")
        fh = {
            "markets": {"a": {}},
            "expressions": [{"expr": "x"}],
        }  # default fixed_horizon
        self.assertEqual(C.spec_mechanism(fh), "expression+fixed_horizon")
        ep = {
            "markets": {"a": {}},
            "expressions": [{"expr": "x"}],
            "return_calc": "episode",
        }
        self.assertEqual(C.spec_mechanism(ep), "expression+episode")

    def test_mechanism_name(self):
        self.assertEqual(
            C.mechanism_name("expression", "episode"), "expression+episode"
        )


if __name__ == "__main__":
    unittest.main()
