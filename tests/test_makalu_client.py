"""Tests for tools/makalu_client.py -- the bridge that checks Makalu's
populate coverage, calls Makalu's CLI if missing, then reads the result.
Real integration tests against the actual Makalu repo/data -- skipped
automatically if Makalu isn't present, since mocking a populate CLI call
would prove nothing about whether the real pipeline actually wrote what
this module expects to read back.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from makalu_client import (  # noqa: E402
    asset_of,
    ensure_leadlag,
    ensure_market_price,
    ensure_return,
    execution_size_for,
    makalu_available,
    market_price_glob,
    read_leadlag_summary,
    read_return,
    return_param_id,
)


class TestPureHelpers(unittest.TestCase):
    def test_asset_of(self):
        self.assertEqual(asset_of("BTC_USDT"), "BTC")
        self.assertEqual(asset_of("ETH_JPY"), "ETH")
        self.assertEqual(asset_of("XRP_SPOT"), "XRP")

    def test_execution_size_for_known_asset(self):
        self.assertEqual(execution_size_for("BTC_JPY"), "0.002")
        self.assertEqual(execution_size_for("ETH_USDT"), "0.1")

    def test_execution_size_for_unknown_asset_is_none(self):
        self.assertIsNone(execution_size_for("DOGE_JPY"))

    def test_return_param_id_matches_makalu_convention(self):
        self.assertEqual(return_param_id("0.002", (30, 120, 300)), "ret_0.002_30_120_300_1s")


@unittest.skipUnless(makalu_available(), "Makalu repo not present")
class TestReturnBridge(unittest.TestCase):
    def test_ensure_then_read_real_data(self):
        ok = ensure_return("GMO", "BTC_JPY", "2026-05-01", "2026-05-15")
        self.assertTrue(ok)
        df = read_return("GMO", "BTC_JPY", "2026-05-01", "2026-05-15")
        self.assertIsNotNone(df)
        self.assertGreater(len(df), 0)
        for col in ("return_30s", "return_120s", "return_300s", "mid", "ts"):
            self.assertIn(col, df.columns)
        # Found for real while building ReturnStep: a raw .join() on
        # mismatched tick-vs-resampled indices left ~99.9% of rows null --
        # confirm that regression doesn't come back.
        self.assertLess(df["return_120s"].isna().mean(), 0.01)

    def test_ensure_market_price_does_not_repopulate_when_only_gap_is_unfixable(self):
        # Found for real: re-scanning hyp-20260619-002 a second time
        # triggered another ~1hr MarketPrice repopulate for 338 days it
        # already had, just to reconfirm the same 8 unfillable days
        # (2026-04-02..09, a real upstream raw-data gap) are still
        # unfillable. ensure_market_price must not call populate at all
        # once every "missing" day is also missing from bronze itself.
        from unittest import mock

        import makalu_client

        # GMO/BTC_JPY already has MarketPrice for a window spanning the
        # known 2026-04-02..09 bronze gap (populated by earlier sessions).
        with mock.patch.object(makalu_client, "_run_populate") as mock_populate:
            ok = ensure_market_price("GMO", "BTC_JPY", "2026-03-30", "2026-04-10")
        self.assertTrue(ok)
        mock_populate.assert_not_called()

    def test_ensure_return_actually_covers_a_range_marketprice_never_had(self):
        # Found for real: re-scanning hyp-20260619-002 over a ~year of
        # bronze history still only produced the original 8-day sample,
        # because `populate Return` exits 0 even when its MarketPrice
        # prerequisite is missing -- it just silently skips every chunk.
        # ensure_return must check/populate MarketPrice first, not just
        # trust the Return populate command's exit code. Uses a narrow,
        # cheap 3-day window distinct from other tests' windows so this
        # genuinely exercises "MarketPrice didn't exist yet here."
        exchange, symbol, start, end = "GMO", "XRP_JPY", "2026-02-01", "2026-02-03"
        mp_glob = market_price_glob(exchange, symbol, execution_size_for(symbol))
        ok = ensure_market_price(exchange, symbol, start, end)
        self.assertTrue(ok)
        import duckdb
        import pandas as pd

        con = duckdb.connect()
        dates = set(pd.to_datetime(con.sql(
            f"SELECT DISTINCT date FROM read_parquet('{mp_glob}', hive_partitioning=true) "
            f"WHERE date >= DATE '{start}' AND date <= DATE '{end}'"
        ).df()["date"]).dt.date)
        self.assertEqual(len(dates), 3)

        ok = ensure_return(exchange, symbol, start, end)
        self.assertTrue(ok)
        df = read_return(exchange, symbol, start, end)
        self.assertIsNotNone(df)
        self.assertGreater(len(df), 0)

    def test_unknown_asset_returns_false_and_none(self):
        self.assertFalse(ensure_return("GMO", "DOGE_JPY", "2026-05-01", "2026-05-02"))
        self.assertIsNone(read_return("GMO", "DOGE_JPY", "2026-05-01", "2026-05-02"))


@unittest.skipUnless(makalu_available(), "Makalu repo not present")
class TestLeadLagBridge(unittest.TestCase):
    def test_ensure_then_read_real_data_matches_bronze_direct_finding(self):
        ok = ensure_leadlag("BINANCE", "BTC_USDT", "GMO", "BTC_SPOT", "2026-05-25", "2026-05-31")
        self.assertTrue(ok)
        summary = read_leadlag_summary("BINANCE", "BTC_USDT", "GMO", "BTC_SPOT", "2026-05-25", "2026-05-31")
        self.assertIsNotNone(summary)
        # Confirmed earlier (manually) against scanner.py's own bronze-direct
        # _measured_lead_lag for this exact pair/window: 0.875-1.0. Makalu's
        # own (more sophisticated) cross-correlation method should land in
        # the same ballpark, not just "some number".
        self.assertGreater(summary["feature_leads_fraction"], 0.7)
        self.assertGreater(summary["mean_best_lag_seconds"], 0)  # feature (BINANCE) leads
        self.assertGreaterEqual(summary["mean_best_corr"], summary["mean_corr_at_zero"])

    def test_same_market_pair_with_no_data_returns_none(self):
        summary = read_leadlag_summary("BINANCE", "DOGE_USDT", "GMO", "DOGE_JPY", "2026-05-01", "2026-05-02")
        self.assertIsNone(summary)


if __name__ == "__main__":
    unittest.main()
