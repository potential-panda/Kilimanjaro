"""Expression signal stack: op causality (the property that makes arbitrary
formulas lookahead-free), AST allowlist enforcement, and ExpressionSignal
event generation on a synthetic frame. No parquet data is touched."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.op.series import OPS, lag, zscore
from kilimanjaro.signal.expression import (
    ExpressionSignal,
    eval_expr,
    validate_expr,
)

# A second series operand, for ops that take two series (e.g. imbalance).
_B_SERIES = pd.Series(
    np.exp(np.random.default_rng(4).normal(0, 0.01, 400).cumsum()) + 1.0
)

# One sample invocation per registered op (op name → args after the series).
# A pd.Series arg is truncated alongside the primary series in the causality test.
_OP_SAMPLE_ARGS = {
    "ema": (30,),
    "roll_mean": (20,),
    "roll_std": (20,),
    "roll_sum": (20,),
    "roll_max": (20,),
    "roll_min": (20,),
    "zscore": (50,),
    "lag": (5,),
    "diff": (3,),
    "ret": (10,),
    "sign": (),
    "abs": (),
    "clip": (-1.0, 1.0),
    "imbalance": (_B_SERIES,),
}


class TestOpCausality(unittest.TestCase):
    """Every registered op must be causal: output[:k] computed on the full
    series must equal output[:k] computed on only the first k points."""

    def test_every_op_is_registered_in_sample_table(self):
        self.assertEqual(set(_OP_SAMPLE_ARGS), set(OPS))

    def test_all_ops_causal(self):
        rng = np.random.default_rng(3)
        x = pd.Series(np.exp(rng.normal(0, 0.01, 400).cumsum()) + 1.0)
        k = 250
        for name, args in _OP_SAMPLE_ARGS.items():
            with self.subTest(op=name):
                full = OPS[name](x, *args)
                # truncate any series operand alongside x (two-series ops)
                pre_args = tuple(
                    a.iloc[:k] if isinstance(a, pd.Series) else a for a in args
                )
                prefix = OPS[name](x.iloc[:k], *pre_args)
                pd.testing.assert_series_equal(
                    full.iloc[:k], prefix, check_names=False,
                    obj=f"op {name} is not causal",
                )

    def test_negative_lag_rejected(self):
        with self.assertRaises(ValueError):
            lag(pd.Series([1.0, 2.0]), -1)

    def test_zscore_zero_std_is_nan_not_inf(self):
        z = zscore(pd.Series(np.ones(100)), 20)
        self.assertTrue(np.isnan(z.iloc[-1]))


class TestValidateExpr(unittest.TestCase):
    def test_valid_expr_returns_columns(self):
        cols = validate_expr("zscore(a_mid / b_mid, 3600) - ema(a_obi_k5, 60)")
        self.assertEqual(cols, ["a_mid", "a_obi_k5", "b_mid"])

    def test_disallowed_syntax_rejected(self):
        bad = [
            "__import__('os').system('x')",       # attribute + unknown call
            "a_mid.shift(-1)",                     # attribute access (lookahead!)
            "a_mid[0]",                            # subscript
            "(lambda: 1)()",                       # lambda
            "[x for x in a_mid]",                  # comprehension
            "a_mid if b_mid else 0",               # conditional
            "unknown_fn(a_mid)",                   # unregistered call
            "zscore(a_mid, window=10)",            # keyword args
            "a_mid and b_mid",                     # boolean op
            "1 + 1",                               # no columns at all
        ]
        for expr in bad:
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                validate_expr(expr)

    def test_eval_expr_matches_direct_computation(self):
        frame = pd.DataFrame({
            "ts": np.arange(100, dtype=float),
            "a_mid": np.linspace(100, 110, 100),
            "b_mid": np.linspace(50, 54, 100),
        })
        out = eval_expr("zscore(a_mid / b_mid, 50)", frame)
        expected = zscore(frame["a_mid"] / frame["b_mid"], 50)
        pd.testing.assert_series_equal(out, expected, check_names=False)


def _make_signal(**kw):
    defaults = {
        "markets": {"a": {"exchange": "X", "symbol": "Y", "execution_size": "1"}},
        "expr": "ema(a_mid, 1)",
        "threshold": 0.5,
    }
    defaults.update(kw)
    return ExpressionSignal.create(**defaults)


class TestExpressionSignal(unittest.TestCase):
    def _frame(self, values):
        return pd.DataFrame({
            "ts": np.arange(len(values), dtype=float) + 1e9,
            "a_mid": np.asarray(values, dtype=float),
        })

    def test_threshold_and_direction(self):
        sig = _make_signal(expr="a_mid", threshold=0.5)
        frame = self._frame([0.2, 0.9, -0.8, 0.1, 0.7])
        out = sig.generate(frame)
        self.assertEqual(list(out["direction"]), [1.0, -1.0, 1.0])
        self.assertEqual(list(out["strength"]), [0.9, -0.8, 0.7])

    def test_invert_flips_direction_and_strength(self):
        sig = _make_signal(expr="a_mid", threshold=0.5, invert=True)
        out = sig.generate(self._frame([0.9, -0.8]))
        self.assertEqual(list(out["direction"]), [-1.0, 1.0])
        self.assertEqual(list(out["strength"]), [-0.9, 0.8])

    def test_age_window_gating(self):
        # |x|>0.5 for 4 consecutive steps; min_age=2 keeps only steps 2,3 of the episode
        sig = _make_signal(expr="a_mid", threshold=0.5, min_age=2)
        out = sig.generate(self._frame([0.1, 0.9, 0.9, 0.9, 0.9, 0.1]))
        self.assertEqual(len(out), 2)  # ages 2 and 3

    def test_episode_id_labels_maximal_runs(self):
        # Two separate runs above threshold → two distinct episode ids,
        # constant within each run; gap seconds are filtered out entirely.
        sig = _make_signal(expr="a_mid", threshold=0.5)
        out = sig.generate(self._frame([0.9, 0.9, 0.1, 0.8, 0.8, 0.8]))
        self.assertEqual(list(out["episode"]), [1, 1, 2, 2, 2])
        self.assertEqual(out["episode"].nunique(), 2)

    def test_episode_id_survives_age_gating(self):
        # min_age drops the crossing second but the surviving rows still carry
        # the true episode id (so EpisodeReturn groups them correctly).
        sig = _make_signal(expr="a_mid", threshold=0.5, min_age=1)
        out = sig.generate(self._frame([0.1, 0.9, 0.9, 0.1, 0.9, 0.9]))
        # episode 1 = ts1,2 (age 0 dropped → ts2); episode 2 = ts4,5 (age0 dropped → ts5)
        self.assertEqual(list(out["episode"]), [1, 2])

    def test_nan_expression_values_never_fire(self):
        sig = _make_signal(expr="zscore(a_mid, 10)", threshold=0.1)
        out = sig.generate(self._frame([1.0, 1.0, 1.0]))  # too short: all NaN
        self.assertEqual(len(out), 0)

    def test_cache_key_shared_across_thresholds_and_exprs_on_same_columns(self):
        a = _make_signal(expr="zscore(a_mid, 60)", threshold=1.0)
        b = _make_signal(expr="zscore(a_mid, 600)", threshold=2.0, invert=True)
        self.assertEqual(a.cache_key, b.cache_key)  # both need only a_mid

    def test_invalid_expr_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            _make_signal(expr="a_mid.rolling(3)")

    def test_params_use_label_when_given(self):
        sig = _make_signal(expr="a_mid", label="raw_mid")
        self.assertEqual(sig.params["expr"], "raw_mid")


class TestBuildCombos(unittest.TestCase):
    def test_spec_expansion(self):
        from kilimanjaro.driver.scan_runner import build_combos
        spec = {
            "markets": {"a": {"exchange": "X", "symbol": "Y", "execution_size": "1"}},
            "expressions": [
                {"label": "e1", "expr": "zscore(a_mid, 60)", "invert": [False, True]},
                {"label": "e2", "expr": "ema(a_mid, 30)"},
            ],
            "thresholds": [1.0, 2.0],
            "age_windows": [[None, None], [10, 60]],
            "horizons": [30, 120],
        }
        combos = build_combos(spec)
        # e1: 2 inverts × 2 thr × 2 ages × 2 H = 16; e2: 1 × 2 × 2 × 2 = 8
        self.assertEqual(len(combos), 24)
        sig, rc = combos[0]
        self.assertEqual(sig.params["expr"], "e1")
        self.assertEqual(rc.params["horizon"], 30)

    def test_episode_return_calc_mode(self):
        from kilimanjaro.driver.scan_runner import build_combos
        spec = {
            "return_calc": "episode",
            "markets": {"a": {"exchange": "X", "symbol": "Y", "execution_size": "1"}},
            "expressions": [{"label": "e1", "expr": "zscore(a_mid, 60)"}],
            "thresholds": [1.0, 2.0],
            "max_holds": [60, None],
        }
        combos = build_combos(spec)
        self.assertEqual(len(combos), 4)  # 1 expr × 2 thr × 1 age × 2 holds
        _, rc = combos[0]
        self.assertEqual(rc.params["max_hold_s"], 60)
        self.assertEqual(combos[1][1].params["max_hold_s"], -1)  # None → -1


if __name__ == "__main__":
    unittest.main()
