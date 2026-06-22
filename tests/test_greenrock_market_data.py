"""Tests for GreenRock market data provider modes."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.market_data import MarketDataProvider
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.screener import run_screen
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow
from atlas_os.web_app import dispatch_request


class FakeMarketDataProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "fake_provider"

    def fetch_stocks(self):
        return load_mock_stocks()


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


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code


if __name__ == "__main__":
    unittest.main()
