"""Tests for the Atlas research Universe Manager."""

from __future__ import annotations

import tempfile
import unittest
import io
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
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
    load_population,
)
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.scanner import cleanup_failed_tickers, run_population_scan, universe_health_rows
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
from atlas_os.web_app import dispatch_request


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

    def test_market_pulse_reads_latest_scan_and_shows_ranked_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("LC01", "GME", "GRRR", "DEAD"))
                run_population_scan(output_dir, "all", provider=MixedArchetypeProvider())
                response = dispatch_request("GET", "/greenrock/market-pulse")

        self.assertEqual(response.status, 200)
        self.assertIn("scan-all-", response.body)
        self.assertIn("Configured Tickers", response.body)
        self.assertIn("Duplicates Removed", response.body)
        self.assertIn("GME", response.body)
        self.assertIn("GRRR", response.body)
        self.assertIn("No scored names in this archetype", response.body)

    def test_universe_page_filters_and_labels_page_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                response = dispatch_request("GET", "/greenrock/universe?q=TSLA&archetype=Large")

        self.assertEqual(response.status, 200)
        self.assertIn("Master Universe Size", response.body)
        self.assertIn("Showing", response.body)
        self.assertIn("filtered rows", response.body)
        self.assertIn("Ticker Search", response.body)
        self.assertIn("Provider Failure Health", response.body)

    def test_market_pulse_cli_and_archetype_audit_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("LC01", "GME", "GRRR"))
                run_population_scan(output_dir, "all", provider=MixedArchetypeProvider())
                pulse = _run_cli(["greenrock", "market-pulse"])
                audit = _run_cli(["greenrock", "archetypes", "audit"])

        self.assertIn("GreenRock Market Pulse", pulse)
        self.assertIn("GME", pulse)
        self.assertIn("GreenRock Archetype Audit", audit)
        self.assertIn("Meme: count=", audit)
        self.assertIn("Special Situation: count=", audit)

    def test_universe_health_reports_failed_tickers_and_cleanup_dry_run_removes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, MICRO_MOONSHOT_POPULATION, ("GRRR", "DEAD"))
                run_population_scan(output_dir, "micro_moonshot", provider=MixedArchetypeProvider())
                health_rows = universe_health_rows(output_dir)
                cleanup_candidates = cleanup_failed_tickers(output_dir, confirm=False)
                cli_health = _run_cli(["greenrock", "universe", "health"])
                cli_cleanup = _run_cli(["greenrock", "universe", "cleanup-failures", "--dry-run"])
                micro = load_population(output_dir, MICRO_MOONSHOT_POPULATION)

        self.assertTrue(any(row["ticker"] == "DEAD" for row in health_rows))
        self.assertTrue(cleanup_candidates)
        self.assertIn("DEAD", cli_health)
        self.assertIn("dry-run", cli_cleanup)
        self.assertIn("DEAD", micro.tickers)


class MockScanProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "mock_scan_provider"

    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.symbols = symbols

    def fetch_stocks(self):
        stocks = {stock.symbol: stock for stock in load_mock_stocks()}
        return tuple(stocks[symbol] for symbol in self.symbols)


class MixedArchetypeProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "mixed_archetype_provider"
    provider_failures = ("DEAD: no price history",)

    def fetch_stocks(self):
        base = next(stock for stock in load_mock_stocks() if stock.symbol == "LC01")
        return (
            replace(base, symbol="LC01", company_name="Large Fixture", market_cap=8_000_000_000),
            replace(base, symbol="GME", company_name="Meme Fixture", market_cap=10_000_000_000),
            replace(base, symbol="GRRR", company_name="Special Situation Fixture", market_cap=100_000_000),
        )


def _run_cli(args: list[str]) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    if exit_code != 0:
        raise AssertionError(f"CLI exited with {exit_code}: {args}")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
