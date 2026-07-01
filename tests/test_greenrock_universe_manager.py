"""Tests for the Atlas research Universe Manager."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_os.greenrock.market_data import MarketDataProvider
from atlas_os.greenrock.market_engine import (
    ARCHETYPE_LARGE,
    ARCHETYPE_MEME,
    ARCHETYPE_MICRO,
    ARCHETYPE_MID,
    ARCHETYPE_SMALL,
    ARCHETYPE_SPECIAL_SITUATION,
    classify_market_archetype,
)
from atlas_os.greenrock.population import (
    MICRO_MOONSHOT_POPULATION,
    QQQ_POPULATION,
    RUSSELL2000_POPULATION,
    SP500_POPULATION,
    save_population,
)
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.scanner import run_population_scan
from atlas_os.greenrock.universe import add_ticker_to_greenrock_list
from atlas_os.greenrock.universe_manager import (
    BUCKET_LARGE,
    BUCKET_MEGA,
    BUCKET_MICRO,
    BUCKET_SMALL_MID,
    PERSONAL_WATCHLISTS_PROVIDER,
    classify_market_cap_bucket,
    default_universe_manager,
    load_master_universe,
)


class UniverseManagerTests(unittest.TestCase):
    def test_provider_registration_and_master_merge_remove_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            save_population(output_dir, QQQ_POPULATION, ("AAPL", "MSFT", "DUP"))
            save_population(output_dir, SP500_POPULATION, ("AAPL", "JPM", "DUP"))
            save_population(output_dir, RUSSELL2000_POPULATION, ("SOFI", "DUP"))
            save_population(output_dir, MICRO_MOONSHOT_POPULATION, ("GRRR", "SOFI"))
            add_ticker_to_greenrock_list(output_dir, "LC01", "personal_watchlist")

            manager = default_universe_manager(output_dir)
            master = manager.build_master_universe()

            self.assertEqual(set(manager.providers), {QQQ_POPULATION, SP500_POPULATION, RUSSELL2000_POPULATION, MICRO_MOONSHOT_POPULATION, PERSONAL_WATCHLISTS_PROVIDER})
            self.assertGreaterEqual(master.size, 300)
            self.assertGreaterEqual(master.duplicates_removed, 4)
            self.assertTrue(master.path.exists())
            dup = next(row for row in master.rows if row.ticker == "DUP")
            self.assertEqual(dup.provider_membership, (QQQ_POPULATION, SP500_POPULATION, RUSSELL2000_POPULATION))
            personal = next(row for row in master.rows if row.ticker == "LC01")
            self.assertEqual(personal.provider_membership, (PERSONAL_WATCHLISTS_PROVIDER,))

    def test_market_cap_classification(self) -> None:
        self.assertEqual(classify_market_cap_bucket(1_200_000_000_000), BUCKET_MEGA)
        self.assertEqual(classify_market_cap_bucket(25_000_000_000), BUCKET_LARGE)
        self.assertEqual(classify_market_cap_bucket(500_000_000), BUCKET_SMALL_MID)
        self.assertEqual(classify_market_cap_bucket(100_000_000), BUCKET_MICRO)
        self.assertEqual(classify_market_cap_bucket(None, (RUSSELL2000_POPULATION,)), BUCKET_SMALL_MID)

    def test_market_archetype_classification(self) -> None:
        self.assertEqual(classify_market_archetype("AAPL", 2_000_000_000_000), "Mega")
        self.assertEqual(classify_market_archetype("MSFT", 100_000_000_000), ARCHETYPE_LARGE)
        self.assertEqual(classify_market_archetype("MID", 3_000_000_000), ARCHETYPE_MID)
        self.assertEqual(classify_market_archetype("SMALL", 800_000_000), ARCHETYPE_SMALL)
        self.assertEqual(classify_market_archetype("MIC", 100_000_000), ARCHETYPE_MICRO)
        self.assertEqual(classify_market_archetype("GME", 10_000_000_000), ARCHETYPE_MEME)
        self.assertEqual(classify_market_archetype("GRRR", 100_000_000), ARCHETYPE_SPECIAL_SITUATION)

    def test_master_universe_persists_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            save_population(output_dir, QQQ_POPULATION, ("AAPL", "MSFT"))
            master = default_universe_manager(output_dir).build_master_universe()
            loaded = load_master_universe(output_dir)

        self.assertEqual(loaded.size, master.size)
        self.assertEqual(loaded.rows[0].ticker, master.rows[0].ticker)

    def test_scanner_all_uses_master_universe_and_ranking_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            save_population(output_dir, QQQ_POPULATION, ("LC01", "LC02"))
            save_population(output_dir, SP500_POPULATION, ("LC02",))
            save_population(output_dir, RUSSELL2000_POPULATION, ("SC01",))
            save_population(output_dir, MICRO_MOONSHOT_POPULATION, ("SC01", "NOISE"))

            result = run_population_scan(output_dir, "all", provider=MockScanProvider(("LC01", "LC02", "SC01")))

            self.assertEqual(result.population, "all")
            self.assertEqual(len(result.rows), 3)
            self.assertGreater(result.configured_ticker_count, len(result.rows))
            self.assertEqual(result.fetched_ticker_count, 3)
            self.assertGreater(result.skipped_ticker_count, 0)
            self.assertEqual(result.provider_failure_count, 0)
            self.assertGreaterEqual(result.duplicates_removed, 1)
            self.assertEqual([row["rank"] for row in result.rows], ["1", "2", "3"])
            self.assertTrue(all(row["percentile"] for row in result.rows))
            self.assertTrue(all(row["market_archetype"] for row in result.rows))
            lc02 = next(row for row in result.rows if row["symbol"] == "LC02")
            self.assertEqual(lc02["universe_membership"], "qqq|sp500")
            self.assertIn("percentile", result.results_path.read_text(encoding="utf-8"))
            summary = result.summary_path.read_text(encoding="utf-8")
            self.assertIn("Total Configured Tickers", summary)
            self.assertIn("Ranked Count", summary)


class MockScanProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "mock_scan_provider"

    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.symbols = symbols

    def fetch_stocks(self):
        stocks = {stock.symbol: stock for stock in load_mock_stocks()}
        return tuple(stocks[symbol] for symbol in self.symbols)


if __name__ == "__main__":
    unittest.main()
