"""Return calcs on the in-memory price cache: the searchsorted joins must
reproduce the DuckDB ASOF semantics they replaced — first tick at or after
ts, INNER on entry, LEFT on exit. Synthetic prices only; the real-data
equivalence is asserted against recorded grid numbers (see commit message)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.data.price_cache import TargetPrices  # noqa: E402
from kilimanjaro.framework.protocol import MarketConfig  # noqa: E402
from kilimanjaro.return_calc.between_signal import BetweenSignalReturn  # noqa: E402
from kilimanjaro.return_calc.episode import EpisodeReturn  # noqa: E402
from kilimanjaro.return_calc.fixed_horizon import FixedHorizonReturn  # noqa: E402

TARGET = MarketConfig("GMO", "ETH_JPY", "0.3")

# ticks at t=10, 20, 30, 40; spread 2 around mids 100, 110, 120, 130
MP = TargetPrices(
    ts=np.array([10.0, 20.0, 30.0, 40.0]),
    bid=np.array([99.0, 109.0, 119.0, 129.0]),
    ask=np.array([101.0, 111.0, 121.0, 131.0]),
)


def _signals(rows):
    return pd.DataFrame(rows)


class TestTargetPrices(unittest.TestCase):
    def test_first_at_or_after(self):
        idx = MP.first_at_or_after(np.array([5.0, 10.0, 15.0, 40.0, 41.0, np.nan]))
        self.assertEqual(list(idx), [0, 0, 1, 3, 4, 4])
        # exact tick matches itself (ASOF >= semantics), 41 and NaN → no match


class TestBetweenSignalReturn(unittest.TestCase):
    def _compute(self, rows):
        with patch("kilimanjaro.return_calc.between_signal.load_target_prices",
                   return_value=MP):
            return BetweenSignalReturn().compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_buy_enters_ask_exits_bid_at_next_ticks(self):
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 1.0, "exit_ts": 25.0},
        ])
        r = out.iloc[0]
        self.assertEqual(r["entry_exec_ts"], 20.0)   # first tick >= 12
        self.assertEqual(r["entry_price"], 111.0)    # BUY → ask
        self.assertEqual(r["exit_exec_ts"], 30.0)    # first tick >= 25
        self.assertEqual(r["exit_price"], 119.0)     # BUY → bid
        self.assertAlmostEqual(
            r["return_bps"], (119.0 - 111.0) / 111.0 * 1e4)
        self.assertEqual(r["holding_seconds"], 10.0)

    def test_sell_enters_bid_exits_ask(self):
        out = self._compute([
            {"ts": 10.0, "direction": -1.0, "strength": -1.0, "exit_ts": 30.0},
        ])
        r = out.iloc[0]
        self.assertEqual(r["entry_price"], 99.0)     # SELL → bid, tick ts=10 itself
        self.assertEqual(r["exit_price"], 121.0)     # SELL → ask at ts=30
        self.assertAlmostEqual(
            r["return_bps"], -1 * (121.0 - 99.0) / 99.0 * 1e4)

    def test_last_flip_without_exit_kept_with_nan_return(self):
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 1.0, "exit_ts": np.nan},
        ])
        self.assertEqual(len(out), 1)                # LEFT join on exit
        self.assertTrue(np.isnan(out.iloc[0]["return_bps"]))
        self.assertTrue(np.isnan(out.iloc[0]["exit_price"]))

    def test_signal_after_last_tick_dropped(self):
        out = self._compute([
            {"ts": 50.0, "direction": 1.0, "strength": 1.0, "exit_ts": 60.0},
        ])
        self.assertEqual(len(out), 0)                # INNER join on entry

    def test_exit_beyond_data_is_nan_but_row_kept(self):
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 1.0, "exit_ts": 45.0},
        ])
        self.assertEqual(len(out), 1)
        self.assertTrue(np.isnan(out.iloc[0]["return_bps"]))


class TestFixedHorizonReturn(unittest.TestCase):
    def _compute(self, rows, horizon):
        with patch("kilimanjaro.return_calc.fixed_horizon.load_target_prices",
                   return_value=MP):
            return FixedHorizonReturn(horizon_seconds=horizon).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_mid_to_mid_at_horizon(self):
        out = self._compute(
            [{"ts": 12.0, "direction": 1.0, "strength": 0.5}], horizon=15)
        r = out.iloc[0]
        self.assertEqual(r["entry_mid"], 110.0)      # first tick >= 12 → ts=20
        self.assertEqual(r["exit_mid"], 120.0)       # first tick >= 27 → ts=30
        self.assertAlmostEqual(r["return_bps"], (120 - 110) / 110 * 1e4)

    def test_direction_signs_return(self):
        up = self._compute([{"ts": 12.0, "direction": 1.0, "strength": 1.0}], 15)
        dn = self._compute([{"ts": 12.0, "direction": -1.0, "strength": -1.0}], 15)
        self.assertAlmostEqual(
            up.iloc[0]["return_bps"], -dn.iloc[0]["return_bps"])

    def test_horizon_beyond_data_nan(self):
        out = self._compute([{"ts": 35.0, "direction": 1.0, "strength": 1.0}], 100)
        self.assertEqual(len(out), 1)
        self.assertTrue(np.isnan(out.iloc[0]["return_bps"]))


class TestEpisodeReturn(unittest.TestCase):
    def _compute(self, rows, max_hold=None):
        with patch("kilimanjaro.return_calc.episode.load_target_prices",
                   return_value=MP):
            return EpisodeReturn(max_hold_seconds=max_hold).compute(
                _signals(rows), TARGET, "2026-01-01", "2026-01-02")

    def test_one_trade_per_episode_natural_exit(self):
        # Episode 1 spans ts 12,13 → enter @12 (ask), exit @ last+1 = 14 (bid).
        # Episode 2 is a single second at ts 22 → enter @22, exit @23.
        out = self._compute([
            {"ts": 12.0, "direction": 1.0, "strength": 2.0, "episode": 1},
            {"ts": 13.0, "direction": 1.0, "strength": 2.5, "episode": 1},
            {"ts": 22.0, "direction": -1.0, "strength": -2.0, "episode": 2},
        ])
        self.assertEqual(len(out), 2)                       # one row per episode
        r1 = out.iloc[0]
        self.assertEqual(r1["ts"], 12.0)                    # entry = crossing second
        self.assertEqual(r1["strength"], 2.0)               # strength taken at entry
        self.assertEqual(r1["entry_exec_ts"], 20.0)         # first tick >= 12
        self.assertEqual(r1["entry_price"], 111.0)          # BUY → ask
        self.assertEqual(r1["exit_ts"], 14.0)               # last active (13) + 1
        self.assertEqual(r1["exit_exec_ts"], 20.0)          # first tick >= 14 → ts=20
        self.assertEqual(r1["exit_price"], 109.0)           # BUY → bid
        r2 = out.iloc[1]
        self.assertEqual(r2["exit_ts"], 23.0)               # single-second episode → +1
        self.assertEqual(r2["entry_price"], 119.0)          # SELL @ ts30 tick → bid
        self.assertEqual(r2["exit_price"], 121.0)           # SELL exit → ask

    def test_max_hold_caps_exit(self):
        # Long episode ts 10..40; hold cap 12 → exit at 10+12 = 22, not 41.
        rows = [{"ts": float(t), "direction": 1.0, "strength": 1.0, "episode": 1}
                for t in range(10, 41)]
        out = self._compute(rows, max_hold=12)
        self.assertEqual(out.iloc[0]["exit_ts"], 22.0)

    def test_requires_episode_column(self):
        with self.assertRaises(ValueError):
            self._compute([{"ts": 12.0, "direction": 1.0, "strength": 1.0}])

    def test_signal_after_last_tick_dropped(self):
        out = self._compute([
            {"ts": 50.0, "direction": 1.0, "strength": 1.0, "episode": 1},
        ])
        self.assertEqual(len(out), 0)                       # INNER on entry


if __name__ == "__main__":
    unittest.main()
