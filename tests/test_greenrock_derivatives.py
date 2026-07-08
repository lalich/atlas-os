"""Tests for the GreenRock Derivative Workbench."""

from __future__ import annotations

import tempfile
import unittest
import io
import csv
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.core.approvals import list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.workflow_runs import list_workflow_runs
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.derivatives import (
    OptionContract,
    OptionsChainSnapshot,
    OptionsDataProvider,
    analyze_staged,
    chain_quality_summary,
    contract_score_factors,
    create_options_snapshot,
    derivative_timing_score,
    latest_derivative_analysis,
    options_manifesto,
    price_american_binomial,
    provider_diagnostics,
    rank_contracts,
    scenario_analysis,
    select_expiration_windows,
)
from atlas_os.greenrock.market_data import MarketDataConfigurationError
from atlas_os.greenrock.staging import add_staged_candidate, load_staged_candidates
from atlas_os.web_app import dispatch_request


class FakeOptionsProvider(OptionsDataProvider):
    source_name = "fake_options"

    def __init__(self, empty: bool = False, missing_iv: bool = False) -> None:
        self.empty = empty
        self.missing_iv = missing_iv

    def fetch_snapshot(self, ticker: str) -> OptionsChainSnapshot:
        today = date.today()
        expirations = tuple((today + timedelta(days=days)).isoformat() for days in (28, 63, 95))
        if self.empty:
            return OptionsChainSnapshot(ticker.upper(), self.source_name, 100.0, _prices(), _volumes(), (), (), ())
        calls = []
        puts = []
        for expiration in expirations:
            for strike in (90, 100, 110):
                iv = None if self.missing_iv else 0.35
                calls.append(OptionContract(f"{ticker.upper()}{expiration}C{strike}", "call", expiration, strike, 2.0, 2.3, 2.15, 120, 800, iv))
                puts.append(OptionContract(f"{ticker.upper()}{expiration}P{strike}", "put", expiration, strike, 1.8, 2.1, 1.95, 100, 700, iv))
        return OptionsChainSnapshot(ticker.upper(), self.source_name, 100.0, _prices(), _volumes(), expirations, tuple(calls), tuple(puts))


class GreenRockDerivativesTests(unittest.TestCase):
    def test_provider_missing_fails_cleanly(self) -> None:
        with patch.dict("os.environ", {"ATLAS_MARKET_DATA_PROVIDER": ""}, clear=False):
            diagnostics = provider_diagnostics("LC01")

        self.assertEqual(diagnostics["status"], "blocked")
        self.assertIn("ATLAS_MARKET_DATA_PROVIDER", diagnostics["message"])

    def test_provider_diagnostics_reports_available_chain_fields(self) -> None:
        diagnostics = provider_diagnostics("LC01", provider=FakeOptionsProvider())

        self.assertEqual(diagnostics["status"], "ready")
        self.assertTrue(diagnostics["underlying_price_available"])
        self.assertTrue(diagnostics["calls_available"])
        self.assertTrue(diagnostics["puts_available"])
        self.assertTrue(diagnostics["implied_volatility_available"])

    def test_ticker_with_no_options_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                create_options_snapshot(Path(directory) / "output", "LC01", provider=FakeOptionsProvider(empty=True))

    def test_expiration_selection_nearest_30_60_90(self) -> None:
        today = date(2026, 1, 1)
        expirations = tuple((today + timedelta(days=days)).isoformat() for days in (27, 65, 91, 140))
        windows = select_expiration_windows(expirations, today=today)

        self.assertEqual([window.target_dte for window in windows], [30, 60, 90])
        self.assertEqual([window.actual_dte for window in windows], [27, 65, 91])

    def test_binomial_american_call_and_put_sanity(self) -> None:
        call = price_american_binomial("call", 100, 100, 45, 0.30)
        put = price_american_binomial("put", 100, 100, 45, 0.30)

        self.assertEqual(call.model_used, "american_binomial")
        self.assertGreater(call.theoretical_value, 0)
        self.assertGreater(put.theoretical_value, 0)
        self.assertGreaterEqual(call.theoretical_value, call.intrinsic_value)
        self.assertGreaterEqual(put.theoretical_value, put.intrinsic_value)

    def test_early_exercise_behavior_for_puts(self) -> None:
        put = price_american_binomial("put", 50, 100, 180, 0.20, risk_free_rate=0.08)

        self.assertTrue(put.early_exercise)
        self.assertGreaterEqual(put.theoretical_value, put.intrinsic_value)

    def test_greeks_finite_difference_outputs(self) -> None:
        result = price_american_binomial("call", 100, 105, 60, 0.35)

        self.assertGreater(result.delta, 0)
        self.assertNotEqual(result.vega, 0)
        self.assertLessEqual(result.theta, 0)

    def test_near_zero_dte_and_invalid_input_handling(self) -> None:
        expired = price_american_binomial("call", 120, 100, 0, 0.30)
        self.assertEqual(expired.theoretical_value, 20)
        self.assertIn("Near-zero DTE", " ".join(expired.warnings))
        with self.assertRaises(ValueError):
            price_american_binomial("call", 0, 100, 30, 0.30)
        unavailable = price_american_binomial("call", 100, 100, 30, None)
        self.assertEqual(unavailable.model_status, "unavailable")

    def test_derivative_timing_score_is_deterministic(self) -> None:
        first = derivative_timing_score(_prices(), _volumes())
        second = derivative_timing_score(_prices(), _volumes())

        self.assertEqual(first.score, second.score)
        self.assertIn("moving_average_structure", first.components)

    def test_contract_research_score_penalizes_poor_liquidity_and_wide_spreads(self) -> None:
        timing = derivative_timing_score(_prices(), _volumes())
        liquid = OptionContract("GOOD", "call", (date.today() + timedelta(days=30)).isoformat(), 100, 2.0, 2.2, 2.1, 200, 1000, 0.35)
        poor = OptionContract("POOR", "call", liquid.expiration, 100, 1.0, 3.0, 2.0, 0, 0, 0.35)
        model = price_american_binomial("call", 100, 100, 30, 0.35)

        good_score = sum(contract_score_factors(liquid, "call", 100, 2.1, model, timing).values())
        poor_score = sum(contract_score_factors(poor, "call", 100, 2.0, model, timing).values())

        self.assertGreater(good_score, poor_score)

    def test_contract_ranking_and_scenarios_for_call_and_put(self) -> None:
        snapshot = FakeOptionsProvider().fetch_snapshot("LC01")
        timing = derivative_timing_score(snapshot.price_history, snapshot.volume_history)
        call_rank = rank_contracts(snapshot.calls, "call", 100, 30, timing)[0]
        put_rank = rank_contracts(snapshot.puts, "put", 100, 30, timing)[0]
        call_grid = scenario_analysis(call_rank.contract, 100, call_rank.contract.premium or 1, 30)
        put_grid = scenario_analysis(put_rank.contract, 100, put_rank.contract.premium or 1, 30)

        self.assertGreater(call_rank.score, 0)
        self.assertGreater(put_rank.score, 0)
        self.assertEqual(len(call_grid), 32)
        self.assertEqual(len(put_grid), 32)

    def test_top_research_rankings_are_otm_only(self) -> None:
        snapshot = FakeOptionsProvider().fetch_snapshot("LC01")
        timing = derivative_timing_score(snapshot.price_history, snapshot.volume_history)
        calls = rank_contracts(snapshot.calls, "call", 100, 30, timing)
        puts = rank_contracts(snapshot.puts, "put", 100, 30, timing)

        self.assertTrue(calls)
        self.assertTrue(puts)
        self.assertTrue(all(item.contract.strike > 100 for item in calls))
        self.assertTrue(all(item.contract.strike < 100 for item in puts))

    def test_far_otm_contracts_are_penalized_against_reasonable_otm(self) -> None:
        timing = derivative_timing_score(_prices(), _volumes())
        expiration = (date.today() + timedelta(days=30)).isoformat()
        reasonable = OptionContract("REASONABLE", "call", expiration, 105, 2.0, 2.2, 2.1, 200, 1000, 0.35)
        far = OptionContract("FAR", "call", expiration, 160, 0.10, 0.12, 0.11, 200, 1000, 0.35)

        ranked = rank_contracts((far, reasonable), "call", 100, 30, timing)

        self.assertEqual(ranked[0].contract.contract_symbol, "REASONABLE")
        self.assertGreater(ranked[0].factors["otm_proximity"], ranked[1].factors["otm_proximity"])

    def test_snapshot_persistence_and_manifesto(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis = create_options_snapshot(root / "output", "LC01", provider=FakeOptionsProvider())
            loaded = latest_derivative_analysis(root / "output", "LC01")
            manifesto = options_manifesto(root / "output")
            self.assertTrue((Path(analysis.snapshot_path) / "metadata.json").exists())
            self.assertTrue((Path(analysis.snapshot_path) / "calls.csv").exists())
            self.assertEqual(loaded["ticker"], "LC01")
            self.assertEqual(manifesto["status"], "available")

    def test_itm_contracts_remain_in_raw_snapshot_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis = create_options_snapshot(root / "output", "LC01", provider=FakeOptionsProvider())
            with (Path(analysis.snapshot_path) / "calls.csv").open(newline="", encoding="utf-8") as csv_file:
                calls = tuple(csv.DictReader(csv_file))
            with (Path(analysis.snapshot_path) / "puts.csv").open(newline="", encoding="utf-8") as csv_file:
                puts = tuple(csv.DictReader(csv_file))

        self.assertTrue(any(float(row["strike"]) <= 100 for row in calls))
        self.assertTrue(any(float(row["strike"]) >= 100 for row in puts))

    def test_manual_ticker_does_not_mutate_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_staged_candidate(root / "output", "LC01", "research")
            before = load_staged_candidates(root / "output")
            create_options_snapshot(root / "output", "LC02", provider=FakeOptionsProvider())
            after = load_staged_candidates(root / "output")

        self.assertEqual(before, after)

    def test_analyze_staged_creates_no_reports_approvals_or_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            add_staged_candidate(root / "output", "LC01", "research")
            db_path = initialize_database(root / "atlas.db")
            analyses = analyze_staged(root / "output", provider_factory=lambda ticker: FakeOptionsProvider())
            with connect(db_path) as connection:
                approvals = list_approvals(connection)
                artifacts = list_artifacts(connection)
                runs = list_workflow_runs(connection)

        self.assertEqual(len(analyses), 1)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_derivatives_page_loads_and_wall_manifesto_appears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"ATLAS_DB_PATH": str(root / "atlas.db"), "ATLAS_OUTPUT_DIR": str(root / "output")}
            with patch.dict("os.environ", env, clear=False):
                create_options_snapshot(root / "output", "LC01", provider=FakeOptionsProvider())
                page = dispatch_request("GET", "/greenrock/derivatives")
                wall = dispatch_request("GET", "/atlas/wall")

        self.assertEqual(page.status, 200)
        self.assertIn("Derivative Workbench", page.body)
        self.assertIn("American-style", page.body)
        self.assertEqual(wall.status, 200)
        self.assertIn("Options Manifesto", wall.body)
        self.assertIn("Derivative Workbench", wall.body)
        self.assertIn("wall-daily-intelligence", wall.body)
        self.assertIn("wall-options-manifesto", wall.body)
        self.assertNotIn("wall-split-intel", wall.body)

    def test_cli_derivatives_doctor_and_analyze_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
                "ATLAS_MARKET_DATA_PROVIDER": "",
            }
            with patch.dict("os.environ", env, clear=False):
                blocked_output, blocked_code = _run_cli_raw(["greenrock", "derivatives", "doctor", "LC01"])
            env["ATLAS_MARKET_DATA_PROVIDER"] = "yfinance"
            with patch.dict("os.environ", env, clear=False), patch(
                "atlas_os.cli.create_options_snapshot",
                side_effect=lambda output_dir, ticker: create_options_snapshot(output_dir, ticker, provider=FakeOptionsProvider()),
            ):
                output, code = _run_cli_raw(["greenrock", "derivatives", "analyze", "LC01"])

        self.assertEqual(blocked_code, 1)
        self.assertIn("GreenRock derivatives doctor", blocked_output)
        self.assertEqual(code, 0)
        self.assertIn("model: american_binomial", output)
        self.assertIn("baw_comparison: unavailable", output)


def _prices() -> tuple[float, ...]:
    return tuple(80 + index * 0.12 + (index % 9) * 0.35 for index in range(260))


def _volumes() -> tuple[int, ...]:
    return tuple(100_000 + index * 250 + (index % 5) * 7_500 for index in range(260))


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code
