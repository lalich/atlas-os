"""Tests for the Atlas research Universe Manager."""

from __future__ import annotations

import tempfile
import unittest
import io
import csv
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.greenrock.analyst import (
    analyst_candidate_from_staged_row,
    analyst_candidates,
    archetype_leaders,
    remaining_candidates,
)
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
from atlas_os.greenrock.market_pulse import stage_analyst_slate_candidates, stage_top_market_pulse_candidates
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
from atlas_os.greenrock.staging import load_staged_candidates
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

    def test_stage_from_market_pulse_stages_expected_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
            run_population_scan(output_dir, "all", provider=FullMarketPulseProvider())

            result = stage_top_market_pulse_candidates(output_dir, overwrite=True)
            rows = load_staged_candidates(output_dir)

        self.assertEqual(len(result.staged_rows), 23)
        self.assertEqual(sum(1 for row in rows if row["staged_bucket"] == "mega"), 1)
        self.assertEqual(sum(1 for row in rows if row["staged_bucket"] == "large"), 11)
        self.assertEqual(sum(1 for row in rows if row["staged_bucket"] == "small_mid"), 11)
        mega = next(row for row in rows if row["ticker"] == "MEGA1")
        self.assertTrue(mega["greenrock_score"])
        self.assertTrue(mega["confidence"])
        self.assertTrue(mega["evidence_agreement"])
        self.assertTrue(mega["guardrail"])
        self.assertTrue(mega["research_priority"])
        self.assertTrue(mega["top_bullish_signal"])
        self.assertTrue(mega["source_scan_id"].startswith("scan-all-"))
        self.assertEqual(mega["notes"], "Market Pulse staged candidate")

    def test_report_from_market_pulse_creates_pending_approval_without_pdf_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
                run_population_scan(output_dir, "all", provider=FullMarketPulseProvider())
                output = _run_cli(["greenrock", "report-from-market-pulse", "--overwrite-staging"])

        self.assertIn("GreenRock Market Pulse candidates staged", output)
        self.assertIn("GreenRock staging report draft created", output)
        self.assertIn("approval_id: 1", output)
        self.assertIn("Draft is blocked until approved by a human", output)
        self.assertFalse(list(output_dir.rglob("*.pdf")))

    def test_market_pulse_ui_stages_then_shows_draft_button(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
                run_population_scan(output_dir, "all", provider=FullMarketPulseProvider())
                confirm = dispatch_request("GET", "/greenrock/market-pulse/stage/confirm")
                staged = dispatch_request("POST", "/greenrock/market-pulse/stage", "overwrite_staging=yes")
                page = dispatch_request("GET", "/greenrock/market-pulse")

        self.assertEqual(confirm.status, 200)
        self.assertIn("Stage Top Market Pulse Candidates", confirm.body)
        self.assertEqual(staged.status, 303)
        self.assertIn("Generate Draft From Staged Market Pulse", page.body)

    def test_atlas_analyst_summary_and_prior_unavailable_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
                scan = run_population_scan(output_dir, "all", provider=SevenArchetypeProvider())
                result = stage_analyst_slate_candidates(output_dir, overwrite=True)

                candidate = analyst_candidate_from_staged_row(output_dir, result.staged_rows[0])
                report = _run_cli(["greenrock", "report-from-staging"])
                draft_path = next(output_dir.rglob("greenrock_report_draft.md"))
                draft = draft_path.read_text(encoding="utf-8")

        self.assertEqual(candidate.source_scan_id, scan.scan_id)
        self.assertIn("Atlas flags it as", candidate.summary)
        self.assertIn("GreenRock staging report draft created", report)
        self.assertIn("Atlas Analyst Summary", draft)
        self.assertIn("No prior scan comparison available.", draft)
        self.assertIn("## Featured Archetype Leaders", draft)
        self.assertIn("## Remaining Ranked Candidates", draft)

    def test_prior_scan_comparison_works_with_fake_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
            scan = run_population_scan(output_dir, "all", provider=SevenArchetypeProvider())
            _write_prior_scan(output_dir, scan.scan_id, "MEGA1", previous_rank="5", previous_score="75.00")
            result = stage_analyst_slate_candidates(output_dir, overwrite=True)

            candidate = analyst_candidate_from_staged_row(output_dir, next(row for row in result.staged_rows if row["ticker"] == "MEGA1"))

        self.assertIsNotNone(candidate.prior)
        self.assertIn("Prior scan comparison", candidate.summary)
        self.assertIn("rank improved", candidate.summary)
        self.assertIn("GreenRock Score increased", candidate.summary)

    def test_archetype_leaders_are_unique_and_remaining_excludes_featured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
            run_population_scan(output_dir, "all", provider=SevenArchetypeProvider())
            result = stage_analyst_slate_candidates(output_dir, overwrite=True)
            candidates = analyst_candidates(output_dir, result.staged_rows)
            leaders = archetype_leaders(candidates)
            remaining = remaining_candidates(candidates, leaders)

        self.assertEqual(len({leader.ticker for leader in leaders}), len(leaders))
        self.assertEqual({leader.archetype for leader in leaders}, {"Mega", "Large", "Mid", "Small", "Micro", "Meme", "Special Situation"})
        self.assertTrue({leader.ticker for leader in leaders}.isdisjoint({candidate.ticker for candidate in remaining}))

    def test_analyst_slate_cli_creates_expected_staging_and_gated_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
                run_population_scan(output_dir, "all", provider=SevenArchetypeProvider())
                staged = _run_cli(["greenrock", "stage-analyst-slate", "--overwrite-staging"])
                rows = load_staged_candidates(output_dir)
                report = _run_cli(["greenrock", "report-analyst-slate", "--overwrite-staging"])

        self.assertIn("GreenRock Atlas Analyst slate staged", staged)
        self.assertEqual(len(rows), 23)
        self.assertTrue(any(row["notes"] == "Atlas Analyst slate candidate" for row in rows))
        self.assertIn("GreenRock staging report draft created", report)
        self.assertIn("approval_id: 1", report)
        self.assertIn("Draft is blocked until approved by a human", report)
        self.assertFalse(list(output_dir.rglob("*.pdf")))

    def test_market_pulse_ui_exposes_analyst_slate_button(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {"ATLAS_OUTPUT_DIR": str(output_dir), "ATLAS_DB_PATH": str(Path(directory) / "atlas.db")}
            with patch.dict("os.environ", env, clear=False):
                save_population(output_dir, QQQ_POPULATION, ("MEGA1", "LG01", "MD01"))
                run_population_scan(output_dir, "all", provider=SevenArchetypeProvider())
                page = dispatch_request("GET", "/greenrock/market-pulse")
                confirm = dispatch_request("GET", "/greenrock/market-pulse/stage/confirm?slate=analyst")

        self.assertIn("Generate Atlas Analyst Report Slate", page.body)
        self.assertIn("one leader from each available archetype", confirm.body)


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


class FullMarketPulseProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "full_market_pulse_provider"

    def fetch_stocks(self):
        base = next(stock for stock in load_mock_stocks() if stock.symbol == "LC01")
        mega = (replace(base, symbol="MEGA1", company_name="Mega Fixture", market_cap=1_500_000_000_000),)
        large = tuple(
            replace(
                base,
                symbol=f"LG{index:02d}",
                company_name=f"Large Fixture {index:02d}",
                market_cap=(20 + index) * 1_000_000_000,
            )
            for index in range(1, 12)
        )
        small_mid = tuple(
            replace(
                base,
                symbol=f"MD{index:02d}",
                company_name=f"Mid Fixture {index:02d}",
                market_cap=(2.5 + index * 0.1) * 1_000_000_000,
            )
            for index in range(1, 12)
        )
        return mega + large + small_mid


class SevenArchetypeProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "seven_archetype_provider"

    def fetch_stocks(self):
        base = next(stock for stock in load_mock_stocks() if stock.symbol == "LC01")
        mega = (replace(base, symbol="MEGA1", company_name="Mega Fixture", market_cap=1_500_000_000_000),)
        large = tuple(
            replace(base, symbol=f"LG{index:02d}", company_name=f"Large Fixture {index:02d}", market_cap=(20 + index) * 1_000_000_000)
            for index in range(1, 11)
        )
        meme = (replace(base, symbol="GME", company_name="Meme Fixture", market_cap=12_000_000_000),)
        mid = tuple(
            replace(base, symbol=f"MD{index:02d}", company_name=f"Mid Fixture {index:02d}", market_cap=(2.5 + index * 0.1) * 1_000_000_000)
            for index in range(1, 5)
        )
        small = tuple(
            replace(base, symbol=f"SM{index:02d}", company_name=f"Small Fixture {index:02d}", market_cap=(500 + index * 20) * 1_000_000)
            for index in range(1, 5)
        )
        micro = tuple(
            replace(base, symbol=f"MI{index:02d}", company_name=f"Micro Fixture {index:02d}", market_cap=(100 + index * 20) * 1_000_000)
            for index in range(1, 3)
        )
        special = (replace(base, symbol="GRRR", company_name="Special Situation Fixture", market_cap=120_000_000),)
        return mega + large + meme + mid + small + micro + special


def _write_prior_scan(output_dir: Path, current_scan_id: str, ticker: str, previous_rank: str, previous_score: str) -> None:
    current_dir = output_dir / "greenrock" / "scans" / current_scan_id
    prior_dir = output_dir / "greenrock" / "scans" / "scan-all-20000101000000"
    prior_dir.mkdir(parents=True, exist_ok=True)
    with (current_dir / "scan_results.csv").open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    for row in rows:
        if row.get("symbol") == ticker:
            row["rank"] = previous_rank
            row["greenrock_score"] = previous_score
            row["greenrock_confidence"] = "60.00"
            row["evidence_agreement"] = "55.00"
    with (prior_dir / "scan_results.csv").open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    (prior_dir / "scan_summary.md").write_text("# Summary\n\n- Data Source: fake\n", encoding="utf-8")


def _run_cli(args: list[str]) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    if exit_code != 0:
        raise AssertionError(f"CLI exited with {exit_code}: {args}")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
