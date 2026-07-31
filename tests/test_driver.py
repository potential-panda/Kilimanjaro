"""Driver (stage-A scanner loop): validation, policy, dedup, and the per-spec
pipeline that writes ledger events + a review packet. The scan itself is mocked
(run_spec) so these stay fast and data-free; run_spec is covered by the scan
tests and real runs."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.driver import ledger_io  # noqa: E402
from kilimanjaro.driver.loop import process_spec  # noqa: E402
from kilimanjaro.driver.policy import evaluate  # noqa: E402
from kilimanjaro.driver.validate import validate_scan_spec  # noqa: E402
from kilimanjaro.driver import blend as blend_mod  # noqa: E402

_GOOD_SPEC = {
    "hypothesis_id": "hyp-20260716-999",
    "markets": {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT", "execution_size": "0.1"}},
    "target": {"exchange": "GMO", "symbol": "ETH_JPY", "execution_size": "0.3"},
    "expressions": [{"label": "imb", "expr": "zscore(a_ta_imbalance_qty_5s, 1800)",
                     "invert": [False]}],
    "thresholds": [1.5],
    "horizons": [60],
}


def _row(stable, mt=True, **over):
    r = {"expr": "imb", "threshold": 1.5, "invert": False, "min_age": 0, "max_age": 60,
         "horizon": 60, "train_avg_net": 1.2, "train_t_stat": 3.0, "holdout_avg_net": 1.1,
         "holdout_t_stat": 2.5, "holdout_p_value_bootstrap": 0.01, "holdout_rank_pct": 0.95,
         "holdout_n": 500, "train_pos_month_share": 0.8, "holdout_pos_month_share": 0.75,
         "verdict": "stable", "stable": stable, "mt_significant": mt}
    r.update(over)
    return r


def _fake_res(rows):
    stable = [r for r in rows if r["stable"]]
    return {
        "rows": rows, "stable": stable, "monthly_rows": [],
        "verdict_counts": {"stable": len(stable), "noise": len(rows) - len(stable)},
        "windows": {"train": ["2025-07-01", "2026-02-28"], "holdout": ["2026-03-01", "2026-06-30"]},
        "params": {"rank_metric": "t_stat", "top_quantile": 0.1, "min_n": 30,
                   "min_month_n": 10, "mt_alpha": 0.1,
                   "n_mt_significant": sum(r["mt_significant"] for r in rows),
                   "n_combos": len(rows), "costs_bps": 6.5, "gates": []},
    }


class TestValidate(unittest.TestCase):
    def test_good_spec_valid(self):
        self.assertEqual(validate_scan_spec(_GOOD_SPEC), [])

    def test_missing_hypothesis_id(self):
        s = {**_GOOD_SPEC}; del s["hypothesis_id"]
        self.assertTrue(any("hypothesis_id" in e for e in validate_scan_spec(s)))

    def test_bad_expression_rejected(self):
        s = {**_GOOD_SPEC, "expressions": [{"expr": "a_ta_x.rolling(3)"}]}
        self.assertTrue(validate_scan_spec(s))

    def test_unknown_alias_rejected(self):
        s = {**_GOOD_SPEC, "expressions": [{"expr": "zscore(b_mid, 60)"}]}  # 'b' not declared
        self.assertTrue(any("alias" in e for e in validate_scan_spec(s)))

    def test_fixed_horizon_needs_horizons(self):
        s = {**_GOOD_SPEC}; del s["horizons"]
        self.assertTrue(any("horizons" in e for e in validate_scan_spec(s)))

    def test_episode_ok_without_horizons(self):
        s = {**_GOOD_SPEC, "return_calc": "episode"}; del s["horizons"]
        self.assertEqual(validate_scan_spec(s), [])


class TestGatesForSpec(unittest.TestCase):
    def test_fixed_horizon_is_ungated_by_nature(self):
        # The gate sums returns; on the overlapping per-second fixed_horizon grid
        # that over-blocks (~94%), so it is skipped there regardless of setting.
        from kilimanjaro.driver.scan_runner import gates_for_spec
        self.assertEqual(gates_for_spec(_GOOD_SPEC), [])  # _GOOD_SPEC is fixed_horizon

    def test_episode_gets_the_canonical_gate_by_default(self):
        from kilimanjaro.driver.scan_runner import gates_for_spec
        from kilimanjaro.framework.gate import (
            DEFAULT_DD_THRESHOLD_BPS,
            RollingDrawdownGate,
        )
        gates = gates_for_spec(_GOOD_SPEC, return_calc="episode")
        self.assertEqual(len(gates), 1)
        self.assertIsInstance(gates[0], RollingDrawdownGate)
        self.assertEqual(gates[0].threshold_bps, DEFAULT_DD_THRESHOLD_BPS)

    def test_false_disables_even_for_episode(self):
        from kilimanjaro.driver.scan_runner import gates_for_spec
        s = {**_GOOD_SPEC, "drawdown_gate": False}
        self.assertEqual(gates_for_spec(s, return_calc="episode"), [])

    def test_dict_overrides_params(self):
        from kilimanjaro.driver.scan_runner import gates_for_spec
        g = gates_for_spec({**_GOOD_SPEC, "drawdown_gate": {"threshold_bps": 250}},
                           return_calc="episode")[0]
        self.assertEqual(g.threshold_bps, 250)
        self.assertEqual(g.window_seconds, 10800.0)  # unspecified -> default


class TestPolicy(unittest.TestCase):
    def test_passes_with_a_confirmed_survivor(self):
        out = evaluate(_fake_res([_row(True, mt=True), _row(False)]))
        self.assertTrue(out.passed)
        self.assertEqual(out.status, "scan-passed")
        self.assertEqual(out.n_survivors, 1)

    def test_fails_with_no_stable(self):
        out = evaluate(_fake_res([_row(False), _row(False)]))
        self.assertFalse(out.passed)
        self.assertEqual(out.status, "scan-failed")

    def test_stable_but_fdr_dropped_fails(self):
        # rank-stable but did not clear multiple-testing -> not a survivor
        out = evaluate(_fake_res([_row(True, mt=False)]))
        self.assertFalse(out.passed)
        self.assertEqual(out.status, "scan-failed")
        self.assertEqual(out.n_stable, 1)
        self.assertEqual(out.n_survivors, 0)


def _spec_with(hyp, markets, exprs):
    return {
        "hypothesis_id": hyp,
        "markets": markets,
        "target": {"exchange": "GMO", "symbol": "ETH_JPY", "execution_size": "0.3"},
        "expressions": [{"label": lbl, "expr": e} for lbl, e in exprs],
    }


class TestFactorsFromSpecs(unittest.TestCase):
    _M = {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT", "execution_size": "0.1"}}

    def test_collects_distinct_factors(self):
        specs = [
            _spec_with("h1", self._M, [("f1", "a_mid"), ("f2", "ret(a_mid, 30)")]),
            _spec_with("h2", self._M, [("g1", "a_obi_ema_k1_30s")]),
        ]
        fs = blend_mod.factors_from_specs(specs)
        self.assertEqual(len(fs), 3)
        self.assertEqual({f.expr for f in fs}, {"a_mid", "ret(a_mid, 30)", "a_obi_ema_k1_30s"})

    def test_dedups_same_markets_and_expr(self):
        # same (markets, expr) across two specs collapses to one factor;
        # invert/threshold are not part of factor identity
        specs = [
            _spec_with("h1", self._M, [("f1", "a_mid")]),
            _spec_with("h2", self._M, [("dup", "a_mid")]),
        ]
        self.assertEqual(len(blend_mod.factors_from_specs(specs)), 1)


class TestCompositeSpec(unittest.TestCase):
    _M = {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT", "execution_size": "0.1"}}

    def _specs(self):
        return [
            _spec_with("h1", self._M, [("f1", "zscore(ret(a_mid, 30), 1800)")]),
            _spec_with("h2", self._M, [("f2", "zscore(a_obi_k1, 1800)")]),
        ]

    def test_composite_is_weighted_sum_dropping_dud_factors(self):
        specs = self._specs()
        factors = blend_mod.factors_from_specs(specs)
        # f1 strong, f2 below floor -> only f1 survives into the composite
        weights = {factors[0].name: 0.05, factors[1].name: 0.001}
        comp = blend_mod.composite_spec(
            specs, factors, weights, thresholds=[1.5], horizon=30, min_abs_weight=0.01
        )
        expr = comp["expressions"][0]["expr"]
        self.assertIn("0.050000*(zscore(ret(a_mid, 30), 1800))", expr)
        self.assertNotIn("a_obi_k1", expr)  # dud dropped
        self.assertTrue(expr.startswith("zscore(") and expr.endswith(", 1800)"))
        self.assertEqual(comp["return_calc"], "episode")
        # the composite is a valid, AST-clean expression signal
        from kilimanjaro.signal.expression import validate_expr
        self.assertEqual(validate_expr(expr), ["a_mid"])

    def test_inconsistent_alias_across_specs_raises(self):
        a1 = _spec_with("h1", {"a": {"exchange": "BINANCE", "symbol": "ETH_USDT",
                                      "execution_size": "0.1"}}, [("f1", "a_mid")])
        a2 = _spec_with("h2", {"a": {"exchange": "GMO", "symbol": "ETH_JPY",
                                     "execution_size": "0.3"}}, [("f2", "a_mid")])
        factors = blend_mod.factors_from_specs([a1, a2])
        weights = {f.name: 0.05 for f in factors}
        with self.assertRaises(ValueError):
            blend_mod.composite_spec([a1, a2], factors, weights,
                                     thresholds=[1.5], horizon=30)


class TestBlendMath(unittest.TestCase):
    """The IC-weighting / z-scoring / combination math, with build_panel mocked
    so no data is loaded — two independent weak factors must blend to a HIGHER
    IC than either alone (the sqrt(N) diversification the whole idea rests on)."""

    def _panels(self, n, seed):
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(seed)
        f1 = rng.normal(size=n)
        f2 = rng.normal(size=n)  # independent of f1
        noise = rng.normal(size=n)
        # return driven equally by both factors + large noise (weak IC each)
        ret = 0.2 * f1 + 0.2 * f2 + noise
        return pd.DataFrame({"ts": np.arange(n, dtype=float),
                             "F1": f1, "F2": f2, "fwd_ret": ret})

    def test_independent_factors_blend_higher(self):
        train = self._panels(6000, 1)
        holdout = self._panels(4000, 2)
        factors = [
            blend_mod.Factor("F1", (), "a_mid"),
            blend_mod.Factor("F2", (), "b_mid"),
        ]
        panels = iter([train, holdout])
        with patch.object(blend_mod, "build_panel", lambda *a, **k: next(panels)):
            res = blend_mod.blend(factors, None, "2025-07-01", "2026-03-01",
                                  "2026-06-30", 120)
        # each single factor is weak; the blend of two independent ones is stronger
        self.assertGreater(res["blend_holdout_ic"], res["best_single_holdout_ic"])
        self.assertGreater(res["diversification_ratio"], 1.0)
        # weights are the train ICs, both positive here
        self.assertTrue(all(p["weight"] > 0 for p in res["per_factor"]))

    def test_partial_coverage_factor_scored_and_neutral_when_silent(self):
        # A factor that is NaN half the time must still be scored on its present
        # rows and contribute 0 (not NaN, not a dropped row) where it is silent.
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(3)
        n = 6000
        f1 = rng.normal(size=n)
        f2 = rng.normal(size=n)
        ret = 0.2 * f1 + 0.2 * f2 + rng.normal(size=n)
        f2_silent = f2.copy()
        f2_silent[1::2] = np.nan  # silent on every other row
        train = pd.DataFrame({"ts": np.arange(n, dtype=float),
                              "F1": f1, "F2": f2_silent, "fwd_ret": ret})
        holdout = train.copy()
        factors = [blend_mod.Factor("F1", (), "a_mid"),
                   blend_mod.Factor("F2", (), "b_mid")]
        panels = iter([train, holdout])
        with patch.object(blend_mod, "build_panel", lambda *a, **k: next(panels)):
            res = blend_mod.blend(factors, None, "2025-07-01", "2026-03-01",
                                  "2026-06-30", 120)
        # nothing NaN-ed out, and the sparse factor keeps a real (positive) IC
        self.assertTrue(all(p["train_ic"] == p["train_ic"] for p in res["per_factor"]))
        f2row = next(p for p in res["per_factor"] if p["factor"] == "F2")
        self.assertGreater(f2row["train_ic"], 0)
        self.assertEqual(res["train_n"], n)  # panel not shrunk by the silent rows


class TestProcessSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "events.jsonl"
        self.results = Path(self.tmp.name) / "results"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, spec, rows, episode_rows=None, **kw):
        """Mock both tiers. `rows` is the screening result; `episode_rows` is the
        episode re-test result (defaults to the rank-stable screening rows, i.e.
        the candidates re-confirming). The verdict is decided on the episode tier."""
        out_dir = self.results / "scan_x"
        out_dir.mkdir(parents=True, exist_ok=True)

        def fake_run_spec(spec, s, e, results_root, split=None, scan_name="scan", **_):
            return _fake_res(rows), out_dir, "scan_x"

        def fake_retest(spec, candidates, s, e, *, results_root,
                        screening_scan_id, split=None):
            ep_dir = self.results / f"{screening_scan_id}_episode"
            ep_dir.mkdir(parents=True, exist_ok=True)
            ep = episode_rows if episode_rows is not None else candidates
            return _fake_res(ep), ep_dir, f"{screening_scan_id}_episode"

        with patch("kilimanjaro.driver.loop.run_spec", side_effect=fake_run_spec), \
             patch("kilimanjaro.driver.loop.run_episode_retest", side_effect=fake_retest):
            return process_spec(spec, "2025-07-01", "2026-06-30", scan_name="scan_x",
                                results_root=self.results, ledger_path=self.ledger, **kw)

    def _statuses(self):
        import json
        return [json.loads(l).get("to_status") for l in self.ledger.read_text().splitlines()]

    def test_passed_writes_packet_and_ledger(self):
        # rank-stable candidate that also confirms on the episode tier
        r = self._run(_GOOD_SPEC, [_row(True), _row(False)])
        self.assertEqual(r["status"], "scan-passed")
        self.assertTrue(Path(r["review_packet"]).exists())
        self.assertIsNotNone(r["episode_folder"])  # episode tier ran
        # scan-ready then scan-passed recorded (+ scan_attached has no to_status)
        self.assertIn("scan-ready", self._statuses())
        self.assertIn("scan-passed", self._statuses())

    def test_two_scan_attached_events_when_episode_runs(self):
        import json
        self._run(_GOOD_SPEC, [_row(True), _row(False)])
        events = [json.loads(l) for l in self.ledger.read_text().splitlines()]
        attached = [e for e in events if e.get("event_type") == "scan_attached"]
        self.assertEqual(len(attached), 2)  # screening + episode

    def test_failed_no_packet(self):
        r = self._run(_GOOD_SPEC, [_row(False)])
        self.assertEqual(r["status"], "scan-failed")
        self.assertIsNone(r["review_packet"])
        self.assertIsNone(r["episode_folder"])  # no candidate -> no episode tier
        self.assertIn("scan-failed", self._statuses())

    def test_rank_stable_but_episode_rejects_fails(self):
        # A candidate passes screening (rank-stable) but its non-overlapping
        # episode return is negative in holdout -> not confirmed. This is the
        # whole point of the episode tier.
        killed = _row(True, holdout_avg_net=-0.5)
        r = self._run(_GOOD_SPEC, [_row(True)], episode_rows=[killed])
        self.assertEqual(r["status"], "scan-failed")
        self.assertIsNotNone(r["episode_folder"])  # tier ran, then rejected
        self.assertIn("scan-failed", self._statuses())

    def test_invalid_spec_recorded_not_scanned(self):
        bad = {**_GOOD_SPEC}; del bad["thresholds"]
        r = self._run(bad, [_row(True)])
        self.assertEqual(r["status"], "invalid-spec")
        self.assertIn("invalid-spec", self._statuses())

    def test_dedup_skips_second_run(self):
        self._run(_GOOD_SPEC, [_row(True)])
        second = self._run(_GOOD_SPEC, [_row(True)])
        self.assertEqual(second["status"], "skipped-duplicate")

    def test_dry_run_no_ledger(self):
        r = self._run(_GOOD_SPEC, [_row(True)], dry_run=True)
        self.assertEqual(r["status"], "dry-run")
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main()
