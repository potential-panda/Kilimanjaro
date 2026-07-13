"""Framework-layer tests: RollingDrawdownGate semantics, run_grid with stub
signal/return-calc (per-combo schedules, gates, combo ids, cache reuse),
run_grid_split rank-stability merge, and the block-bootstrap p-value.

No parquet data is touched — stubs generate deterministic trades in memory.
"""

import sys
import unittest
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework import (  # noqa: E402
    MarketConfig,
    RollingDrawdownGate,
    ScheduleFilter,
    block_bootstrap_pvalue,
    run_grid,
    run_grid_split,
    split_windows,
)
from kilimanjaro.framework.evaluate import default_split  # noqa: E402

TARGET = MarketConfig("GMO", "ETH_JPY", "0.3")


def _trades(ts, returns, exit_offset=60.0):
    ts = np.asarray(ts, dtype=float)
    return pd.DataFrame({
        "ts": ts,
        "exit_ts": ts + exit_offset,
        "return_bps": np.asarray(returns, dtype=float),
    })


class TestRollingDrawdownGate(unittest.TestCase):
    def test_disabled_gate_keeps_everything(self):
        gate = RollingDrawdownGate(0, 0, 0)
        df = _trades([0, 100], [5.0, -5.0])
        kept, info = gate.apply(df)
        self.assertEqual(len(kept), 2)
        self.assertEqual(info["n_dd_blocked"], 0)

    def test_drawdown_closes_gate_and_cooldown_reopens(self):
        # window 1000s, threshold 100bps, cooldown 500s
        gate = RollingDrawdownGate(1000, 100, 500)
        # three big losses push window sum to -150 < -100 → 4th trade blocked
        ts = [0, 100, 200, 300, 400, 900]
        rets = [-50, -50, -50, 10, 10, 10]
        kept, info = gate.apply(_trades(ts, rets, exit_offset=10))
        # trade at ts=300: window sum = -150 → trigger, blocked
        # trade at ts=400: still in cooldown (reopen at 300+500=800) → blocked
        # trade at ts=900: past reopen → kept, window reset
        self.assertEqual(list(kept["ts"]), [0.0, 100.0, 200.0, 900.0])
        self.assertEqual(info["n_dd_blocked"], 2)
        self.assertEqual(info["n_dd_triggers"], 1)
        trig = info["dd_triggers"][0]
        self.assertEqual(trig["close_ts"], 300.0)
        self.assertEqual(trig["reopen_ts"], 800.0)
        self.assertEqual(trig["n_blocked"], 2)

    def test_old_returns_fall_out_of_window(self):
        gate = RollingDrawdownGate(100, 100, 500)
        # losses at t=0..20 sum to -150 but by t=1000 they're out of the window
        kept, info = gate.apply(
            _trades([0, 10, 20, 1000], [-50, -50, -50, 5], exit_offset=1)
        )
        # at ts=20 window holds -100 (ts=0,10) → not < -100 → kept; at 1000 window empty
        self.assertEqual(info["n_dd_blocked"], 0)
        self.assertEqual(len(kept), 4)

    def test_nan_returns_do_not_enter_window(self):
        gate = RollingDrawdownGate(1000, 100, 500)
        kept, info = gate.apply(
            _trades([0, 10, 20, 30], [-80, np.nan, -80, 1], exit_offset=1)
        )
        # window at ts=30: -160 → blocked (NaN contributed nothing)
        self.assertEqual(info["n_dd_blocked"], 1)
        self.assertEqual(list(kept["ts"]), [0.0, 10.0, 20.0])

    def test_missing_exit_ts_column_falls_back_to_ts(self):
        gate = RollingDrawdownGate(1000, 100, 500)
        df = pd.DataFrame({"ts": [0.0, 10.0], "return_bps": [1.0, 2.0]})
        kept, info = gate.apply(df)
        self.assertEqual(len(kept), 2)


class StubSignal:
    """SignalDef stub: emits one signal per hour over a fixed grid."""

    load_calls = 0

    def __init__(self, family: str, gain: float):
        self.family = family
        self.gain = gain

    @property
    def params(self) -> dict:
        return {"family": self.family, "gain": self.gain}

    @property
    def cache_key(self) -> tuple:
        return (self.family,)

    def load_data(self, start, end, data_root):
        StubSignal.load_calls += 1
        return {"start": start, "end": end}

    def generate(self, data):
        n = 200
        ts = np.arange(n) * 3600.0 + 1.7e9
        return pd.DataFrame({
            "ts": ts, "direction": 1.0, "strength": 1.0, "exit_ts": ts + 1800.0,
        })


class StubReturn:
    """ReturnCalc stub: deterministic returns; profitable iff gain > 0,
    and (for split tests) only in windows the `windows` set allows."""

    def __init__(self, horizon: int = 60):
        self.horizon = horizon

    @property
    def params(self) -> dict:
        return {"horizon": self.horizon}

    def compute(self, signals, target, start_date, end_date, data_root):
        # zlib.crc32, not hash(): str hash is randomized per process
        seed = zlib.crc32(f"{start_date}:{self.horizon}".encode())
        rng = np.random.default_rng(seed)
        gain = signals.attrs.get("gain", 1.0)
        df = signals.copy()
        df["return_bps"] = rng.normal(gain, 2.0, len(df))
        df["holding_seconds"] = 1800.0
        df["jst_hour"] = 12
        df["jst_weekday"] = 2
        df["jst_date"] = "2026-01-01"
        return df


class GainAwareSignal(StubSignal):
    """Passes its gain to the return stub via DataFrame.attrs."""

    def generate(self, data):
        df = super().generate(data)
        df.attrs["gain"] = self.gain
        return df


class TestRunGrid(unittest.TestCase):
    def test_rows_carry_params_ids_and_counts(self):
        combos = [
            (GainAwareSignal("a", 2.0), StubReturn(60)),
            (GainAwareSignal("a", -2.0), StubReturn(120)),
            (GainAwareSignal("b", 2.0), StubReturn(60)),
        ]
        StubSignal.load_calls = 0
        rows = run_grid(combos, TARGET, "2026-01-01", "2026-02-01")

        self.assertEqual(len(rows), 3)
        # rows come back in input order with combo ids
        self.assertEqual([r["_combo_id"] for r in rows], [0, 1, 2])
        self.assertTrue(all(r["n_combos_tested"] == 3 for r in rows))
        self.assertTrue(all(r["status"] == "ok" for r in rows))
        self.assertIn("p_value_bootstrap", rows[0])
        self.assertIn("n_trades", rows[0])
        # cache reuse: two distinct cache keys → two loads
        self.assertEqual(StubSignal.load_calls, 2)

    def test_per_combo_schedule_name_in_row(self):
        sched = ScheduleFilter(block_weekday_hours=[], name="myschedule")
        combos = [(GainAwareSignal("a", 2.0), StubReturn(60), sched)]
        rows = run_grid(combos, TARGET, "2026-01-01", "2026-02-01")
        self.assertEqual(rows[0]["schedule"], "myschedule")

    def test_gate_counts_merged_into_row(self):
        gate = RollingDrawdownGate(1e9, 1.0, 1e9)  # huge window, tiny threshold
        combos = [(GainAwareSignal("a", -50.0), StubReturn(60))]
        rows = run_grid(combos, TARGET, "2026-01-01", "2026-02-01", gates=[gate])
        self.assertEqual(rows[0]["status"], "ok")
        self.assertGreater(rows[0]["n_dd_blocked"], 0)
        self.assertNotIn("dd_triggers", rows[0])  # non-scalars stay out of rows


class TestRunGridSplit(unittest.TestCase):
    def test_split_windows(self):
        self.assertEqual(
            split_windows("2025-07-01", "2026-03-01", "2026-06-30"),
            ("2025-07-01", "2026-02-28", "2026-03-01", "2026-06-30"),
        )
        with self.assertRaises(ValueError):
            split_windows("2025-07-01", "2025-07-01", "2026-06-30")
        with self.assertRaises(ValueError):
            split_windows("2025-07-01", "2026-07-01", "2026-06-30")

    def test_default_split_two_thirds(self):
        self.assertEqual(default_split("2026-01-01", "2026-01-31"), "2026-01-21")

    def test_stable_requires_top_rank_in_both_windows(self):
        # 10 combos: gains 1..10 → gain 10 ranks top in both windows (returns
        # are gain + noise, same construction in each window)
        combos = [
            (GainAwareSignal("a", float(g)), StubReturn(60 + g)) for g in range(1, 11)
        ]
        res = run_grid_split(
            combos, TARGET, "2026-01-01", "2026-03-01", "2026-04-01",
            top_quantile=0.10, min_n=10,
        )
        rows = res["rows"]
        self.assertEqual(len(rows), 10)
        stable = res["stable"]
        self.assertEqual(len(stable), 1)
        self.assertEqual(stable[0]["gain"], 10.0)
        self.assertEqual(stable[0]["train_rank_pct"], 1.0)
        self.assertEqual(stable[0]["holdout_rank_pct"], 1.0)
        # merged rows have both windows' stats and no unprefixed stat fields
        self.assertIn("train_avg_net", rows[0])
        self.assertIn("holdout_avg_net", rows[0])
        self.assertNotIn("avg_net", rows[0])
        self.assertEqual(res["windows"]["train"], ["2026-01-01", "2026-02-28"])

    def test_min_n_excludes_from_ranking(self):
        combos = [(GainAwareSignal("a", 10.0), StubReturn(60))]
        res = run_grid_split(
            combos, TARGET, "2026-01-01", "2026-03-01", "2026-04-01",
            min_n=10_000,  # stub only produces 200 trades
        )
        r = res["rows"][0]
        self.assertNotEqual(r["train_rank_pct"], r["train_rank_pct"])  # NaN
        self.assertFalse(r["stable"])


class TestBlockBootstrap(unittest.TestCase):
    def test_strong_positive_mean_has_small_p(self):
        rng = np.random.default_rng(7)
        x = rng.normal(2.0, 1.0, 500)
        self.assertLess(block_bootstrap_pvalue(x), 0.01)

    def test_zero_mean_noise_has_large_p(self):
        rng = np.random.default_rng(7)
        x = rng.normal(0.0, 1.0, 500)
        self.assertGreater(block_bootstrap_pvalue(x), 0.1)

    def test_small_n_returns_nan(self):
        x = np.ones(5)
        self.assertNotEqual(block_bootstrap_pvalue(x), block_bootstrap_pvalue(x))

    def test_deterministic(self):
        rng = np.random.default_rng(7)
        x = rng.normal(0.5, 1.0, 300)
        self.assertEqual(block_bootstrap_pvalue(x), block_bootstrap_pvalue(x))


if __name__ == "__main__":
    unittest.main()
