"""Tests for GreenRock market data provider modes."""

from __future__ import annotations

import io
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.core.approvals import list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.workflow_runs import list_workflow_runs
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.market_data import MarketDataProvider
from atlas_os.greenrock.market_data import get_market_data_provider
from atlas_os.greenrock.models import MockStock
from atlas_os.greenrock.population import (
    MICRO_MOONSHOT_POPULATION,
    load_population,
    reset_all_populations,
    validate_populations,
)
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.score import calculate_score_preview
from atlas_os.greenrock.scanner import load_promotion_metadata, run_population_scan
from atlas_os.greenrock.screener import run_screen
from atlas_os.greenrock.staging import (
    add_staged_candidate,
    load_staged_candidates,
    move_staged_candidate,
    remove_staged_candidate,
    staging_readiness,
)
from atlas_os.greenrock.staging_report import run_greenrock_staging_report_workflow
from atlas_os.greenrock.universe import LARGE_CAP_TICKERS, MEGA_ROCK_TICKERS, SMALL_MID_CAP_TICKERS, save_ticker_universe
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow
from atlas_os.web_app import dispatch_request


class FakeMarketDataProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "fake_provider"

    def fetch_stocks(self):
        return load_mock_stocks()


class FakeSectionedMarketDataProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "fake_sectioned_provider"

    def fetch_stocks(self):
        return load_mock_stocks()

    def fetch_grouped_stocks(self):
        stocks = load_mock_stocks()
        mega = tuple(replace(stock, market_cap=1_200_000_000_000) for stock in stocks[:5])
        large = tuple(replace(stock, market_cap=25_000_000_000 + index * 1_000_000_000) for index, stock in enumerate(stocks[:14]))
        small = tuple(replace(stock, market_cap=2_000_000_000 + index * 100_000_000) for index, stock in enumerate(stocks[14:28]))
        return {
            "mega_rock": mega,
            "large_cap": large,
            "small_mid_cap": small,
        }


class GreenRockMarketDataTests(unittest.TestCase):
    def test_mock_mode_screen_still_uses_mock_provider(self) -> None:
        result = run_screen()

        self.assertEqual(result.data_mode, "mock")
        self.assertEqual(result.data_source, "mock_sample_data")
        self.assertTrue(result.selected)

    def test_provider_interface_supports_fake_provider(self) -> None:
        result = run_screen(FakeMarketDataProvider())

        self.assertEqual(result.data_mode, "real")
        self.assertEqual(result.data_source, "fake_provider")
        self.assertTrue(result.selected)

    def test_real_mode_without_config_fails_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
                "ATLAS_MARKET_DATA_PROVIDER": "",
                "ATLAS_GREENROCK_REAL_TICKERS": "",
            }
            with patch.dict("os.environ", env, clear=False):
                output, exit_code = _run_cli_raw(["greenrock", "report-draft", "--data", "real"])

            self.assertEqual(exit_code, 1)
            self.assertIn("GreenRock report draft blocked", output)
            self.assertIn("data_mode: REAL", output)
            self.assertIn("not configured", output)
            self.assertEqual(list(root.glob("output/greenrock/*")), [])

    def test_cli_score_command_works_with_mock_data(self) -> None:
        output = _run_cli(["greenrock", "score", "LC01", "--data", "mock"])

        self.assertIn("GreenRock Score Preview", output)
        self.assertIn("ticker: LC01", output)
        self.assertIn("greenrock_score:", output)
        self.assertIn("greenrock_confidence:", output)
        self.assertIn("confidence_band:", output)
        self.assertIn("evidence_agreement:", output)
        self.assertIn("score_confidence_divergence:", output)
        self.assertIn("signal_label:", output)
        self.assertIn("research_priority:", output)
        self.assertIn("analyst_summary:", output)
        self.assertIn("rank_band:", output)
        self.assertIn("all_time_high:", output)
        self.assertIn("one_year_statistical_price_targets:", output)
        self.assertIn("historical_lookback:", output)
        self.assertIn("horizon: 1 year", output)
        self.assertIn("+2 SD:", output)
        self.assertIn("+3 SD:", output)
        self.assertIn("+5 SD:", output)
        self.assertIn("+7 SD:", output)
        self.assertIn("fundamental_guardrails:", output)
        self.assertIn("label:", output)
        self.assertIn("quick_ratio:", output)
        self.assertIn("share_count_change_percent:", output)
        self.assertIn("bullish_fundamental_evidence:", output)
        self.assertIn("bearish_fundamental_evidence:", output)
        self.assertIn("evidence_engine:", output)
        self.assertIn("top_bullish_evidence:", output)
        self.assertIn("top_bearish_evidence:", output)
        self.assertIn("bullish_evidence:", output)
        self.assertIn("bearish_evidence:", output)
        self.assertIn("positive_confidence_drivers:", output)
        self.assertIn("confidence_drags:", output)
        self.assertIn("what_to_watch_next:", output)
        self.assertIn("finviz: https://finviz.com/quote.ashx?t=LC01", output)

    def test_score_preview_works_with_fake_provider(self) -> None:
        preview = calculate_score_preview("LC01", data_mode="real", provider=FakeMarketDataProvider())

        self.assertEqual(preview.candidate.symbol, "LC01")
        self.assertEqual(preview.data_mode, "real")
        self.assertEqual(preview.data_source, "fake_provider")
        self.assertIn("rsi", preview.component_scores)

    def test_real_score_without_provider_fails_safely(self) -> None:
        with patch.dict("os.environ", {"ATLAS_MARKET_DATA_PROVIDER": ""}, clear=False):
            output, exit_code = _run_cli_raw(["greenrock", "score", "AAPL"])

        self.assertEqual(exit_code, 1)
        self.assertIn("GreenRock score preview blocked", output)
        self.assertIn("export ATLAS_MARKET_DATA_PROVIDER=yfinance", output)
        self.assertIn('python3 -m pip install -e ".[market-data]"', output)
        self.assertIn("No report, approval, artifact", output)

    def test_workflow_with_fake_real_provider_labels_report_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                workflow_run, _, _ = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                    data_mode="real",
                    provider=FakeMarketDataProvider(),
                )

            report_path = root / "output" / "greenrock" / workflow_run.run_id / "greenrock_report_draft.md"
            markdown = report_path.read_text(encoding="utf-8")

        self.assertEqual(workflow_run.data_mode, "real")
        self.assertFalse(workflow_run.mock_data_used)
        self.assertIn("**Data Mode:** REAL", markdown)
        self.assertIn("fake_provider", markdown)
        self.assertIn("approval-gated", markdown)

    def test_dashboard_displays_data_mode_if_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                db_path = initialize_database(root / "atlas.db")
                with connect(db_path) as connection:
                    workflow_run, _, _ = run_greenrock_screening_workflow(
                        connection,
                        root / "output",
                        include_report_draft=True,
                        data_mode="real",
                        provider=FakeMarketDataProvider(),
                    )
                response = dispatch_request("GET", "/greenrock")

        self.assertEqual(response.status, 200)
        self.assertIn("Data Mode", response.body)
        self.assertIn("REAL", response.body)
        self.assertIn(workflow_run.run_id, response.body)

    def test_real_provider_uses_greenrock_watchlists_when_env_tickers_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_yfinance = types.SimpleNamespace()
            with (
                patch.dict("sys.modules", {"yfinance": fake_yfinance}),
                patch.dict(
                    "os.environ",
                    {
                        "ATLAS_MARKET_DATA_PROVIDER": "yfinance",
                        "ATLAS_GREENROCK_REAL_TICKERS": "",
                    },
                    clear=False,
                ),
            ):
                provider = get_market_data_provider("real", output_dir=root / "output")

        self.assertEqual(provider.data_mode, "real")
        self.assertEqual(provider.source_name, "yfinance:greenrock_watchlists")
        self.assertEqual(tuple(provider.providers), ("mega_rock", "large_cap", "small_mid_cap"))
        self.assertIn("AAPL", provider.providers["mega_rock"].tickers)
        self.assertIn("NVDA", provider.providers["mega_rock"].tickers)
        self.assertIn("ADBE", provider.providers["large_cap"].tickers)
        self.assertIn("SOFI", provider.providers["small_mid_cap"].tickers)

    def test_fake_sectioned_real_provider_can_produce_23_picks(self) -> None:
        result = run_screen(FakeSectionedMarketDataProvider())

        self.assertEqual(result.data_mode, "real")
        self.assertEqual(result.selection_mode, "ranked")
        self.assertEqual(len(result.mega_rock), 1)
        self.assertEqual(len(result.large_cap), 11)
        self.assertEqual(len(result.small_cap), 11)
        self.assertEqual(len(result.selected), 23)
        self.assertEqual(result.data_quality_warnings, ())
        symbols = [candidate.symbol for candidate in result.selected]
        self.assertEqual(len(symbols), len(set(symbols)))

    def test_adbe_cannot_be_mega_rock_below_one_trillion_market_cap(self) -> None:
        class AdobeProvider(FakeSectionedMarketDataProvider):
            def fetch_grouped_stocks(self):
                stock = replace(load_mock_stocks()[0], symbol="ADBE", company_name="Adobe Inc.", market_cap=250_000_000_000)
                return {"mega_rock": (stock,), "large_cap": (), "small_mid_cap": ()}

        result = run_screen(AdobeProvider(), selection_mode="ranked")

        self.assertEqual(result.mega_rock, ())
        self.assertTrue(result.data_quality_warnings)

    def test_default_watchlist_taxonomy_places_adbe_in_large_cap_not_mega_rock(self) -> None:
        self.assertNotIn("ADBE", MEGA_ROCK_TICKERS)
        self.assertIn("ADBE", LARGE_CAP_TICKERS)

    def test_ranked_real_mode_fills_available_candidates_when_strict_fails(self) -> None:
        provider = FailingSectionedProvider()
        ranked = run_screen(provider, selection_mode="ranked")
        strict = run_screen(provider, selection_mode="strict")

        self.assertEqual(len(ranked.mega_rock), 1)
        self.assertEqual(len(ranked.large_cap), 11)
        self.assertEqual(len(ranked.small_cap), 11)
        self.assertEqual(len(ranked.selected), 23)
        self.assertTrue(all(candidate.selection_label in {"Ranked Candidate", "Watchlist"} for candidate in ranked.selected))
        self.assertLess(len(strict.selected), len(ranked.selected))

    def test_incomplete_sectioned_provider_shows_warnings(self) -> None:
        class IncompleteProvider(FakeSectionedMarketDataProvider):
            def fetch_grouped_stocks(self):
                stocks = load_mock_stocks()
                mega = tuple(replace(stock, market_cap=1_200_000_000_000) for stock in stocks[:1])
                large = tuple(replace(stock, market_cap=25_000_000_000) for stock in stocks[:3])
                small = tuple(replace(stock, market_cap=2_000_000_000) for stock in stocks[14:18])
                return {
                    "mega_rock": mega,
                    "large_cap": large,
                    "small_mid_cap": small,
                }

        result = run_screen(IncompleteProvider())

        self.assertTrue(result.data_quality_warnings)
        self.assertIn("Large-cap section has", result.data_quality_warnings[0])

    def test_universe_cli_add_remove_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                list_output = _run_cli(["greenrock", "universe", "list"])
                add_output = _run_cli(["greenrock", "universe", "add", "TSLA", "PLTR"])
                remove_output = _run_cli(["greenrock", "universe", "remove", "TSLA"])
                final_output = _run_cli(["greenrock", "universe", "list"])
                reset_output = _run_cli(["greenrock", "universe", "reset-mega-rock"])
                large_reset = _run_cli(["greenrock", "universe", "reset-large-cap"])
                small_reset = _run_cli(["greenrock", "universe", "reset-small-mid"])
                all_reset = _run_cli(["greenrock", "universe", "reset-all"])

        self.assertIn("name: mega_rock", list_output)
        self.assertIn("name: large_cap", list_output)
        self.assertIn("name: small_mid_cap", list_output)
        self.assertIn("ticker_count:", add_output)
        self.assertIn("ticker_count:", remove_output)
        self.assertIn("PLTR", final_output)
        mega_section = final_output.split("name: large_cap", maxsplit=1)[0]
        self.assertNotIn("  TSLA", mega_section)
        self.assertIn("GreenRock ticker watchlist reset", reset_output)
        self.assertIn(str(len(LARGE_CAP_TICKERS)), large_reset)
        self.assertIn(str(len(SMALL_MID_CAP_TICKERS)), small_reset)
        self.assertIn("GreenRock ticker watchlists reset", all_reset)

    def test_watchlist_validation_command_warns_for_duplicates_and_spce(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                save_ticker_universe(root / "output", ("AAPL", "SPCE"), name="mega_rock")
                save_ticker_universe(root / "output", ("AAPL", "ADBE"), name="large_cap")
                save_ticker_universe(root / "output", SMALL_MID_CAP_TICKERS, name="small_mid_cap")
                output = _run_cli(["greenrock", "universe", "validate"])

        self.assertIn("GreenRock watchlist validation", output)
        self.assertIn("SPCE is Virgin Galactic, not SpaceX.", output)
        self.assertIn("AAPL appears in multiple watchlists", output)

    def test_population_reset_creates_files_and_micro_moonshot_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            populations = reset_all_populations(root / "output")
            micro = load_population(root / "output", MICRO_MOONSHOT_POPULATION)
            paths_exist = all(population.path.exists() for population in populations.values())

        self.assertEqual(set(populations), {"qqq", "sp500", "russell2000", "micro_moonshot"})
        self.assertTrue(paths_exist)
        for ticker in ("GRRR", "PI", "ENPH", "SOFI", "ENVX"):
            self.assertIn(ticker, micro.tickers)

    def test_population_validate_catches_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reset_all_populations(root / "output")
            validation = validate_populations(root / "output")

        self.assertIn("AAPL", validation.duplicate_tickers)
        self.assertTrue(validation.warnings)

    def test_population_scan_with_fake_provider_ranks_tickers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reset_all_populations(root / "output")
            result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
            results_exists = result.results_path.exists()
            summary_exists = result.summary_path.exists()

        self.assertTrue(results_exists)
        self.assertTrue(summary_exists)
        self.assertTrue(result.rows)
        self.assertEqual(result.rows[0]["rank"], "1")
        self.assertIn("greenrock_score", result.rows[0])
        self.assertIn("greenrock_confidence", result.rows[0])
        self.assertIn("evidence_agreement", result.rows[0])
        self.assertIn("fundamental_guardrail", result.rows[0])

    def test_scanner_page_returns_200(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                response = dispatch_request("GET", "/greenrock/scanner")

        self.assertEqual(response.status, 200)
        self.assertIn("Market Scanner", response.body)
        self.assertIn("Run Population Scan", response.body)
        self.assertIn("Promote", response.body)
        self.assertIn("https://finviz.com/quote.ashx?t=", response.body)

    def test_discovery_page_returns_200_and_shows_workflow_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                response = dispatch_request("GET", "/greenrock/discovery")

        self.assertEqual(response.status, 200)
        self.assertIn("GreenRock Discovery Workflow", response.body)
        self.assertIn("/static/greenrock_logo.png", response.body)
        self.assertIn("GreenRock Discovery Flow", response.body)
        self.assertNotIn("->", response.body)
        self.assertIn("Select Population", response.body)
        self.assertIn("Run Scan", response.body)
        self.assertIn("Review Ranked Results", response.body)
        self.assertIn("Promote Selected Names", response.body)
        self.assertIn("View Updated Watchlists", response.body)
        self.assertIn("Generate Report Later", response.body)

    def test_scanner_page_shows_latest_scan_metadata_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                response = dispatch_request("GET", "/greenrock/scanner")

        self.assertEqual(response.status, 200)
        self.assertIn("Population Scanned", response.body)
        self.assertIn(result.population, response.body)
        self.assertIn("Scan Timestamp", response.body)
        self.assertIn("Tickers Scanned", response.body)
        self.assertIn("Minimum GreenRock Score", response.body)
        self.assertIn("Minimum Confidence", response.body)
        self.assertIn("Minimum Evidence Agreement", response.body)
        self.assertIn("Research Priority", response.body)
        self.assertIn("Guardrail label", response.body)
        self.assertIn("Promote Selected", response.body)

    def test_scanner_promotion_saves_duplicate_safe_and_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                db_path = initialize_database(root / "atlas.db")
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                ticker = result.rows[0]["symbol"]
                first = dispatch_request(
                    "POST",
                    "/greenrock/scanner/promote",
                    f"scan_id={result.scan_id}&ticker={ticker}&list_key=watchlist",
                )
                second = dispatch_request(
                    "POST",
                    "/greenrock/scanner/promote",
                    f"scan_id={result.scan_id}&ticker={ticker}&list_key=watchlist",
                )
                saved = (root / "output" / "greenrock" / "watchlists" / "watchlist.csv").read_text(encoding="utf-8")
                metadata = load_promotion_metadata(root / "output")
                with connect(db_path) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    runs = list_workflow_runs(connection)

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertIn("saved to Watchlist", first.body)
        self.assertIn("duplicate ignored", second.body)
        self.assertEqual(saved.count(ticker), 1)
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0]["ticker"], ticker)
        self.assertEqual(metadata[0]["destination_list"], "watchlist")
        self.assertEqual(metadata[0]["scan_id"], result.scan_id)
        self.assertIn("score", metadata[0])
        self.assertIn("evidence_agreement", metadata[0])
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_watchlists_page_renders_promoted_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                db_path = initialize_database(root / "atlas.db")
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                ticker = result.rows[0]["symbol"]
                dispatch_request(
                    "POST",
                    "/greenrock/scanner/promote-batch",
                    f"scan_id={result.scan_id}&tickers={ticker}&list_key=watchlist",
                )
                response = dispatch_request("GET", "/greenrock/watchlists")
                with connect(db_path) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    runs = list_workflow_runs(connection)

        self.assertEqual(response.status, 200)
        self.assertIn("GreenRock Watchlists", response.body)
        self.assertIn("Watchlist", response.body)
        self.assertIn(ticker, response.body)
        self.assertIn(f"scan:{result.scan_id}", response.body)
        self.assertIn("https://finviz.com/quote.ashx?t=", response.body)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_watchlist_remove_button_works_and_missing_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                ticker = result.rows[0]["symbol"]
                dispatch_request(
                    "POST",
                    "/greenrock/scanner/promote",
                    f"scan_id={result.scan_id}&ticker={ticker}&list_key=watchlist",
                )
                page = dispatch_request("GET", "/greenrock/watchlists")
                removed = dispatch_request("POST", "/greenrock/watchlists/remove", f"ticker={ticker}&list_key=watchlist")
                missing = dispatch_request("POST", "/greenrock/watchlists/remove", f"ticker={ticker}&list_key=watchlist")
                saved = (root / "output" / "greenrock" / "watchlists" / "watchlist.csv").read_text(encoding="utf-8")

        self.assertIn("Remove", page.body)
        self.assertIn(f"{ticker} removed from Watchlist", removed.body)
        self.assertIn("nothing changed", missing.body)
        self.assertNotIn(ticker, saved)

    def test_scanner_promotion_bucket_mismatch_warning_appears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                row = next(item for item in result.rows if item["market_cap_bucket"] == "small_cap")
                response = dispatch_request(
                    "POST",
                    "/greenrock/scanner/promote",
                    f"scan_id={result.scan_id}&ticker={row['symbol']}&list_key=large_cap",
                )

        self.assertEqual(response.status, 200)
        self.assertIn("Promotion blocked", response.body)
        self.assertIn("does not currently meet the requirements for Large Cap Watchlist", response.body)
        self.assertIn("Consider adding it to Small/Mid Watchlist or Personal Watchlist instead.", response.body)

    def test_cli_scan_promote_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                ticker = result.rows[0]["symbol"]
                output = _run_cli(["greenrock", "scan-promote", result.scan_id, ticker, "--list", "watchlist"])

        self.assertIn("GreenRock scan promotion complete", output)
        self.assertIn(f"ticker: {ticker}", output)
        self.assertIn("No report, approval, PDF, email, publication", output)

    def test_staging_page_returns_200(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                response = dispatch_request("GET", "/greenrock/staging")

        self.assertEqual(response.status, 200)
        self.assertIn("GreenRock Report Candidate Staging", response.body)
        self.assertIn("Mega Rock Candidate", response.body)
        self.assertIn("Large Cap Candidate", response.body)
        self.assertIn("Small/Mid Candidate", response.body)
        self.assertIn("Generate Draft From Staging", response.body)
        self.assertIn("/greenrock/staging/generate/confirm", response.body)

    def test_staging_stores_candidate_metadata_from_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                result = run_population_scan(root / "output", "micro_moonshot", provider=FakeMarketDataProvider())
                row = result.rows[0]
                staged = add_staged_candidate(root / "output", row["symbol"], "large", source_list="latest_scan", notes="review setup")
                stored = load_staged_candidates(root / "output")

        self.assertEqual(staged["ticker"], row["symbol"])
        self.assertEqual(staged["staged_bucket"], "large")
        self.assertEqual(staged["source_scan_id"], result.scan_id)
        self.assertEqual(staged["greenrock_score"], row["greenrock_score"])
        self.assertEqual(staged["confidence"], row["greenrock_confidence"])
        self.assertEqual(staged["evidence_agreement"], row["evidence_agreement"])
        self.assertEqual(staged["guardrail"], row["fundamental_guardrail"])
        self.assertEqual(staged["research_priority"], row["research_priority"])
        self.assertEqual(staged["top_bullish_signal"], row["top_bullish_signal"])
        self.assertEqual(stored[0]["notes"], "review setup")

    def test_staging_candidate_can_move_and_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_staged_candidate(root / "output", "AAPL", "research")
            moved = move_staged_candidate(root / "output", "AAPL", "mega")
            removed = remove_staged_candidate(root / "output", "AAPL")
            remaining = load_staged_candidates(root / "output")

        self.assertEqual(moved["staged_bucket"], "mega")
        self.assertTrue(removed)
        self.assertEqual(remaining, ())

    def test_staging_readiness_detects_underfilled_ready_and_overfilled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_staged_candidate(root / "output", "MEGA1", "mega")
            for index in range(12):
                add_staged_candidate(root / "output", f"LARGE{index}", "large")
            readiness = {item.bucket: item.status for item in staging_readiness(root / "output")}

        self.assertEqual(readiness["mega"], "Ready")
        self.assertEqual(readiness["large"], "Overfilled")
        self.assertEqual(readiness["small_mid"], "Underfilled")

    def test_staging_web_actions_create_no_reports_approvals_or_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                db_path = initialize_database(root / "atlas.db")
                add_response = dispatch_request("POST", "/greenrock/staging/add", "ticker=AAPL&bucket=mega&source_list=manual&notes=operator")
                move_response = dispatch_request("POST", "/greenrock/staging/move", "ticker=AAPL&bucket=research")
                notes_response = dispatch_request("POST", "/greenrock/staging/notes", "ticker=AAPL&notes=updated")
                remove_response = dispatch_request("POST", "/greenrock/staging/remove", "ticker=AAPL")
                with connect(db_path) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    runs = list_workflow_runs(connection)

        self.assertEqual(add_response.status, 200)
        self.assertIn("AAPL staged as Mega Rock Candidate", add_response.body)
        self.assertIn("AAPL moved to Research Only", move_response.body)
        self.assertIn("Notes updated for AAPL", notes_response.body)
        self.assertIn("AAPL removed from staging", remove_response.body)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_cli_staging_commands_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                add_output = _run_cli(["greenrock", "staging", "add", "AAPL", "--bucket", "mega", "--notes", "watch"])
                move_output = _run_cli(["greenrock", "staging", "move", "AAPL", "--bucket", "large"])
                ready_output = _run_cli(["greenrock", "staging", "ready"])
                list_output = _run_cli(["greenrock", "staging", "list"])
                remove_output = _run_cli(["greenrock", "staging", "remove", "AAPL"])

        self.assertIn("GreenRock staging candidate saved", add_output)
        self.assertIn("bucket: Mega Rock Candidate", add_output)
        self.assertIn("GreenRock staging candidate moved", move_output)
        self.assertIn("bucket: Large Cap Candidate", move_output)
        self.assertIn("GreenRock staging readiness", ready_output)
        self.assertIn("AAPL large", list_output)
        self.assertIn("GreenRock staging candidate removed", remove_output)

    def test_staging_draft_generation_creates_run_artifacts_and_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            add_staged_candidate(root / "output", "SOFI", "small_mid", notes="review note")
            with connect(db_path) as connection:
                workflow_run, artifacts, approval = run_greenrock_staging_report_workflow(
                    connection,
                    root / "output",
                    allow_underfilled=True,
                )
                approvals = list_approvals(connection)
                stored_artifacts = list_artifacts(connection)
                runs = list_workflow_runs(connection)
            report_path = Path(workflow_run.output_paths["report_draft"])
            markdown = report_path.read_text(encoding="utf-8")

        self.assertEqual(workflow_run.status, "awaiting_approval")
        self.assertEqual(workflow_run.data_mode, "real")
        self.assertIsNotNone(approval)
        self.assertEqual(approval.status.value, "pending")
        self.assertEqual(len(artifacts), 5)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(len(stored_artifacts), 5)
        self.assertEqual(len(runs), 1)
        self.assertNotIn("report_final_pdf", {artifact.artifact_type for artifact in stored_artifacts})
        self.assertIn("**Candidate Source:** Staging-sourced", markdown)
        self.assertIn("SOFI", markdown)
        self.assertIn("review note", markdown)

    def test_staging_draft_generation_blocks_underfilled_without_allow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                add_staged_candidate(root / "output", "SOFI", "small_mid")
                output, exit_code = _run_cli_raw(["greenrock", "report-from-staging"])
                with connect(initialize_database(root / "atlas.db")) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    runs = list_workflow_runs(connection)

        self.assertEqual(exit_code, 1)
        self.assertIn("GreenRock staging report blocked", output)
        self.assertIn("--allow-underfilled", output)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_cli_report_from_staging_allow_underfilled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                add_staged_candidate(root / "output", "SOFI", "small_mid")
                output = _run_cli(["greenrock", "report-from-staging", "--allow-underfilled"])

        self.assertIn("GreenRock staging report draft created", output)
        self.assertIn("selection_mode: STAGING", output)
        self.assertIn("Draft is blocked until approved by a human", output)

    def test_browser_staging_generation_confirmation_and_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                add_staged_candidate(root / "output", "SOFI", "small_mid")
                confirmation = dispatch_request("GET", "/greenrock/staging/generate/confirm")
                response = dispatch_request("POST", "/greenrock/staging/generate", "allow_underfilled=yes")
                with connect(initialize_database(root / "atlas.db")) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    runs = list_workflow_runs(connection)

        self.assertEqual(confirmation.status, 200)
        self.assertIn("Generate Draft From Staging?", confirmation.body)
        self.assertIn("Allow underfilled sections", confirmation.body)
        self.assertEqual(response.status, 303)
        self.assertIn("/greenrock/staging?status=", response.location)
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(len(artifacts), 5)

    def test_population_scan_missing_provider_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
                "ATLAS_MARKET_DATA_PROVIDER": "",
            }
            with patch.dict("os.environ", env, clear=False):
                output, exit_code = _run_cli_raw(["greenrock", "scan", "--population", "qqq"])

        self.assertEqual(exit_code, 1)
        self.assertIn("GreenRock population scan blocked", output)
        self.assertIn("export ATLAS_MARKET_DATA_PROVIDER=yfinance", output)
        self.assertEqual(list((root / "output").glob("greenrock/scans/*")), [])


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code


def _run_cli(args: list[str]) -> str:
    output, exit_code = _run_cli_raw(args)
    if exit_code != 0:
        raise AssertionError(f"CLI exited with {exit_code}: {args}\n{output}")
    return output


class FailingSectionedProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "failing_sectioned_provider"

    def fetch_stocks(self):
        return ()

    def fetch_grouped_stocks(self):
        noise = next(stock for stock in load_mock_stocks() if stock.symbol == "NOISE")
        return {
            "mega_rock": _clones(noise, "MEGA", 1, 1_200_000_000_000),
            "large_cap": _clones(noise, "LARGE", 11, 25_000_000_000),
            "small_mid_cap": _clones(noise, "SMALL", 11, 2_000_000_000),
        }


def _clones(stock: MockStock, prefix: str, count: int, market_cap: float) -> tuple[MockStock, ...]:
    return tuple(
        replace(
            stock,
            symbol=f"{prefix}{index:02d}",
            company_name=f"{prefix} Failing {index:02d}",
            market_cap=market_cap + index,
        )
        for index in range(count)
    )


if __name__ == "__main__":
    unittest.main()
