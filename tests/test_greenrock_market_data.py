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
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.market_data import MarketDataProvider
from atlas_os.greenrock.market_data import get_market_data_provider
from atlas_os.greenrock.models import MockStock
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.score import calculate_score_preview
from atlas_os.greenrock.screener import run_screen
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
        self.assertIn("signal_label:", output)
        self.assertIn("rank_band:", output)
        self.assertIn("all_time_high:", output)
        self.assertIn("one_year_statistical_price_targets:", output)
        self.assertIn("historical_lookback:", output)
        self.assertIn("horizon: 1 year", output)
        self.assertIn("+2 SD:", output)
        self.assertIn("+3 SD:", output)
        self.assertIn("+5 SD:", output)
        self.assertIn("+7 SD:", output)
        self.assertIn("bonus_penalty_explanations:", output)
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
