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
    BinomialResult,
    ContractResearch,
    CrossWindowResearch,
    OptionContract,
    OptionsChainSnapshot,
    OptionsDataProvider,
    analyze_staged,
    chain_quality_summary,
    classify_cross_window,
    contract_exclusion_reasons,
    contract_research_score,
    contract_score_factors,
    create_options_snapshot,
    cross_window_research,
    derive_position_context,
    derivative_timing_score,
    excluded_contracts,
    latest_derivative_analysis,
    options_manifesto,
    position_context_path,
    price_american_binomial,
    provider_diagnostics,
    rank_contracts,
    ranking_rationale,
    scenario_analysis,
    select_expiration_windows,
    strategy_intent_for_contract,
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


class FailingOptionsProvider(OptionsDataProvider):
    source_name = "failing_options"

    def fetch_snapshot(self, ticker: str) -> OptionsChainSnapshot:
        raise RuntimeError("provider fetch failed")


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

    def test_provider_diagnostics_fail_closed_when_provider_fetch_fails(self) -> None:
        diagnostics = provider_diagnostics("LC01", provider=FailingOptionsProvider())

        self.assertEqual(diagnostics["status"], "blocked")
        self.assertIn("provider fetch failed", diagnostics["message"])

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

    def test_top_research_excludes_quality_guardrail_failures(self) -> None:
        timing = derivative_timing_score(_prices(), _volumes())
        expiration = (date.today() + timedelta(days=30)).isoformat()
        good = OptionContract("GOOD", "call", expiration, 105, 2.0, 2.2, 2.1, 200, 1000, 0.35)
        missing_iv = OptionContract("NOIV", "call", expiration, 106, 2.0, 2.2, 2.1, 200, 1000, None)
        wide = OptionContract("WIDE", "call", expiration, 107, 1.0, 3.0, 2.0, 200, 1000, 0.35)
        illiquid = OptionContract("ILLQ", "call", expiration, 108, 2.0, 2.2, 2.1, 0, 0, 0.35)
        no_quote = OptionContract("NOQUOTE", "call", expiration, 109, None, None, None, 200, 1000, 0.35)

        ranked = rank_contracts((good, missing_iv, wide, illiquid, no_quote), "call", 100, 30, timing)

        self.assertEqual([item.contract.contract_symbol for item in ranked], ["GOOD"])
        self.assertIn("Missing IV.", contract_exclusion_reasons(missing_iv, "call", 100))
        self.assertIn("Wide spread.", contract_exclusion_reasons(wide, "call", 100))
        self.assertIn("Poor liquidity.", contract_exclusion_reasons(illiquid, "call", 100))
        self.assertIn("Unusable premium.", contract_exclusion_reasons(no_quote, "call", 100))
        self.assertIn("Missing/invalid quote data.", contract_exclusion_reasons(no_quote, "call", 100))

    def test_excluded_contracts_capture_itm_and_quote_reasons(self) -> None:
        expiration = (date.today() + timedelta(days=30)).isoformat()
        itm = OptionContract("ITM", "call", expiration, 90, 10.0, 10.5, 10.25, 200, 1000, 0.35)
        invalid = OptionContract("INVALID", "call", "", 105, None, None, None, 0, 0, None)

        rows = excluded_contracts((itm, invalid), "call", 100)
        reasons = {row.contract.contract_symbol: row.reasons for row in rows}

        self.assertIn("ITM or ATM; Top Research is OTM-only.", reasons["ITM"])
        self.assertIn("Missing/invalid quote data.", reasons["INVALID"])
        self.assertIn("Missing IV.", reasons["INVALID"])

    def test_far_otm_contracts_are_penalized_against_reasonable_otm(self) -> None:
        timing = derivative_timing_score(_prices(), _volumes())
        expiration = (date.today() + timedelta(days=30)).isoformat()
        reasonable = OptionContract("REASONABLE", "call", expiration, 105, 2.0, 2.2, 2.1, 200, 1000, 0.35)
        far = OptionContract("FAR", "call", expiration, 160, 0.10, 0.12, 0.11, 200, 1000, 0.35)

        ranked = rank_contracts((far, reasonable), "call", 100, 30, timing)

        self.assertEqual(ranked[0].contract.contract_symbol, "REASONABLE")
        self.assertGreater(ranked[0].factors["otm_proximity"], ranked[1].factors["otm_proximity"])

    def test_contract_ranking_is_deterministic_with_tie_breakers(self) -> None:
        timing = derivative_timing_score(_prices(), _volumes())
        expiration = (date.today() + timedelta(days=30)).isoformat()
        first = OptionContract("AAA", "call", expiration, 105, 2.0, 2.2, 2.1, 200, 1000, 0.35)
        second = OptionContract("BBB", "call", expiration, 105, 2.0, 2.2, 2.1, 200, 1000, 0.35)

        ranked_once = rank_contracts((second, first), "call", 100, 30, timing, target_dte=30)
        ranked_twice = rank_contracts((second, first), "call", 100, 30, timing, target_dte=30)

        self.assertEqual([item.contract.contract_symbol for item in ranked_once], ["AAA", "BBB"])
        self.assertEqual([item.contract.contract_symbol for item in ranked_once], [item.contract.contract_symbol for item in ranked_twice])

    def test_calibrated_ranking_prefers_liquid_tighter_reasonable_contract(self) -> None:
        timing = derivative_timing_score(_prices(), _volumes())
        expiration = (date.today() + timedelta(days=30)).isoformat()
        balanced = OptionContract("BALANCED", "put", expiration, 95, 2.0, 2.2, 2.1, 250, 1200, 0.38)
        thin = OptionContract("THIN", "put", expiration, 80, 1.0, 1.4, 1.2, 12, 55, 0.80)

        ranked = rank_contracts((thin, balanced), "put", 100, 30, timing, target_dte=30)

        self.assertEqual(ranked[0].contract.contract_symbol, "BALANCED")
        self.assertGreater(ranked[0].factors["liquidity"], ranked[1].factors["liquidity"])
        self.assertGreater(ranked[0].factors["spread"], ranked[1].factors["spread"])
        self.assertGreater(ranked[0].factors["iv_condition"], ranked[1].factors["iv_condition"])

    def test_ranking_rationale_is_concise_and_factor_based(self) -> None:
        factors = {
            "liquidity": 90,
            "spread": 88,
            "otm_proximity": 82,
            "iv_condition": 42,
            "premium_quality": 77,
            "window_fit": 100,
            "timing_alignment": 65,
            "scenario_behavior": 48,
        }

        rationale = ranking_rationale(factors)

        self.assertIn("Supported by liquidity, spread, OTM fit", rationale)
        self.assertIn("watch IV, scenario", rationale)
        self.assertLessEqual(len(rationale), 100)
        self.assertGreater(contract_research_score(factors), 0)

    def test_cross_window_classification_states_are_deterministic(self) -> None:
        self.assertEqual(classify_cross_window((60.0,), total_available_windows=1), "insufficient_data")
        self.assertEqual(classify_cross_window((60.0,), total_available_windows=3), "isolated")
        self.assertEqual(classify_cross_window((60.0, 66.0, 72.0), total_available_windows=3), "strengthening")
        self.assertEqual(classify_cross_window((72.0, 66.0, 60.0), total_available_windows=3), "weakening")
        self.assertEqual(classify_cross_window((70.0, 72.0, 71.0), total_available_windows=3), "stable")
        self.assertEqual(classify_cross_window((60.0, 66.0, 72.0), total_available_windows=3), classify_cross_window((60.0, 66.0, 72.0), total_available_windows=3))

    def test_cross_window_research_detects_strengthening_stable_weakening_and_isolated(self) -> None:
        windows = ("30", "60", "90")
        strengthening = cross_window_research(_research_groups("call", windows, (60, 67, 74), 105), {}, 100)[0]
        stable = cross_window_research(_research_groups("call", windows, (70, 71, 72), 105), {}, 100)[0]
        weakening = cross_window_research(_research_groups("call", windows, (74, 67, 60), 105), {}, 100)[0]
        isolated = cross_window_research({"30": (_research_item("call", "30", 105, 70),), "60": (_research_item("call", "60", 112, 68),), "90": (_research_item("call", "90", 125, 66),)}, {}, 100)[0]

        self.assertEqual(strengthening.classification, "strengthening")
        self.assertEqual(stable.classification, "stable")
        self.assertEqual(weakening.classification, "weakening")
        self.assertEqual(isolated.classification, "isolated")
        self.assertIn("Score improves", strengthening.rationale)
        self.assertEqual(strengthening.score_movement, 14)

    def test_cross_window_research_reports_insufficient_data(self) -> None:
        rows = cross_window_research({"30": (_research_item("call", "30", 105, 70),), "60": (), "90": ()}, {}, 100)

        self.assertEqual(rows[0].classification, "insufficient_data")
        self.assertIn("Fewer than two windows", rows[0].rationale)

    def test_position_context_defaults_when_no_portfolio_context_available(self) -> None:
        context = derive_position_context("LC01", None, None, "", "", True, True, "none")

        self.assertEqual(context.position_direction, "unknown")
        self.assertFalse(context.flags["covered_call_candidate"])
        self.assertTrue(context.flags["speculative_only"])
        self.assertIn("No local position context file found", " ".join(context.notes))

    def test_long_stock_creates_covered_call_and_hedge_context(self) -> None:
        context = derive_position_context("LC01", 200, 42.5, "", "", True, True)

        self.assertEqual(context.position_direction, "long_stock")
        self.assertTrue(context.flags["covered_call_candidate"])
        self.assertTrue(context.flags["hedge_candidate"])
        self.assertFalse(context.flags["exposure_conflict"])

    def test_no_stock_creates_speculative_and_cash_secured_put_context(self) -> None:
        context = derive_position_context("LC01", 0, None, "", "", True, True)

        self.assertEqual(context.position_direction, "none")
        self.assertTrue(context.flags["cash_secured_put_candidate"])
        self.assertTrue(context.flags["speculative_only"])
        self.assertFalse(context.flags["covered_call_candidate"])

    def test_conflicting_position_exposure_is_flagged(self) -> None:
        context = derive_position_context("LC01", 100, 50, "short calls", "short_options", True, True)

        self.assertEqual(context.position_direction, "mixed")
        self.assertTrue(context.flags["exposure_conflict"])

    def test_strategy_intent_income_overlay_for_long_stock_calls(self) -> None:
        context = derive_position_context("LC01", 200, 42.5, "", "", True, True)
        intent, rationale, manifesto, position = strategy_intent_for_contract(_research_item("call", "30", 105, 70), _cross_rows("call", "stable"), context)

        self.assertEqual(intent, "income_overlay")
        self.assertIn("covered-call", rationale)
        self.assertEqual(manifesto, "stable")
        self.assertEqual(position, "aligned_with_long_stock")

    def test_strategy_intent_cash_secured_entry_for_no_stock_puts(self) -> None:
        context = derive_position_context("LC01", 0, None, "", "", True, True)
        intent, rationale, _, position = strategy_intent_for_contract(_research_item("put", "30", 95, 70), _cross_rows("put", "stable"), context)

        self.assertEqual(intent, "cash_secured_entry")
        self.assertIn("cash-secured", rationale)
        self.assertEqual(position, "aligned_with_no_stock")

    def test_strategy_intent_downside_hedge_for_long_stock_puts(self) -> None:
        context = derive_position_context("LC01", 200, 42.5, "", "", True, True)
        intent, rationale, _, position = strategy_intent_for_contract(_research_item("put", "30", 95, 70), _cross_rows("put", "weakening"), context)

        self.assertEqual(intent, "downside_hedge")
        self.assertIn("hedge", rationale)
        self.assertEqual(position, "hedge_context")

    def test_strategy_intent_speculative_convexity_without_position(self) -> None:
        context = derive_position_context("LC01", None, None, "", "", True, True)
        intent, rationale, manifesto, position = strategy_intent_for_contract(_research_item("call", "30", 105, 70), _cross_rows("call", "strengthening"), context)

        self.assertEqual(intent, "speculative_convexity")
        self.assertIn("constructive", rationale)
        self.assertEqual(manifesto, "strengthening")
        self.assertEqual(position, "speculative_only")

    def test_strategy_intent_avoid_conflict_overrides_other_context(self) -> None:
        context = derive_position_context("LC01", 100, 50, "short calls", "short_options", True, True)
        intent, rationale, _, position = strategy_intent_for_contract(_research_item("call", "30", 105, 70), _cross_rows("call", "stable"), context)

        self.assertEqual(intent, "avoid_conflict")
        self.assertIn("conflicts", rationale)
        self.assertEqual(position, "conflict")

    def test_strategy_intent_research_only_fallback(self) -> None:
        context = derive_position_context("LC01", None, None, "long call", "long_options", True, True)
        intent, rationale, manifesto, position = strategy_intent_for_contract(_research_item("call", "30", 105, 70), _cross_rows("call", "isolated"), context)

        self.assertEqual(intent, "research_only")
        self.assertIn("Research-only", rationale)
        self.assertEqual(manifesto, "isolated")
        self.assertEqual(position, "long_options")

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

    def test_analysis_persists_score_factors_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_options_snapshot(root / "output", "LC01", provider=FakeOptionsProvider())
            loaded = latest_derivative_analysis(root / "output", "LC01")

        factor_keys = loaded["top_calls"]["30"][0]["score_factors"].keys()
        self.assertIn("liquidity", factor_keys)
        self.assertIn("spread_quality", factor_keys)
        self.assertIn("iv_condition", factor_keys)
        self.assertIn("otm_distance", factor_keys)
        self.assertIn("premium_quality", factor_keys)
        self.assertIn("window_fit", factor_keys)
        self.assertIn("timing_window_alignment", factor_keys)
        self.assertIn("scenario_behavior", factor_keys)
        self.assertIn("ranking_rationale", loaded["top_calls"]["30"][0])
        self.assertIn("Supported by", loaded["top_calls"]["30"][0]["ranking_rationale"])
        self.assertIn("strategy_intent", loaded["top_calls"]["30"][0])
        self.assertIn("intent_rationale", loaded["top_calls"]["30"][0])
        self.assertIn("manifesto_alignment", loaded["top_calls"]["30"][0])
        self.assertIn("position_context_alignment", loaded["top_calls"]["30"][0])
        self.assertIn("cross_window", loaded)
        self.assertTrue(loaded["cross_window"])
        self.assertIn("classification", loaded["cross_window"][0])
        self.assertIn("position_context", loaded)
        self.assertEqual(loaded["position_context"]["position_direction"], "unknown")
        excluded_reasons = [reason for row in loaded["excluded_calls"]["30"] for reason in row["reasons"]]
        self.assertIn("ITM or ATM; Top Research is OTM-only.", excluded_reasons)

    def test_local_position_context_file_is_read_only_and_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = position_context_path(root / "output")
            path.parent.mkdir(parents=True)
            path.write_text("ticker,shares,average_cost,option_exposure,option_direction\nLC01,200,42.5,,\n", encoding="utf-8")
            create_options_snapshot(root / "output", "LC01", provider=FakeOptionsProvider())
            loaded = latest_derivative_analysis(root / "output", "LC01")

        self.assertEqual(loaded["position_context"]["current_shares"], 200.0)
        self.assertEqual(loaded["position_context"]["average_cost"], 42.5)
        self.assertEqual(loaded["position_context"]["position_direction"], "long_stock")
        self.assertTrue(loaded["position_context"]["flags"]["covered_call_candidate"])

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

    def test_derivatives_page_shows_score_factors_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"ATLAS_DB_PATH": str(root / "atlas.db"), "ATLAS_OUTPUT_DIR": str(root / "output")}
            with patch.dict("os.environ", env, clear=False):
                create_options_snapshot(root / "output", "LC01", provider=FakeOptionsProvider())
                page = dispatch_request("GET", "/greenrock/derivatives")

        self.assertEqual(page.status, 200)
        self.assertIn("Score Factors", page.body)
        self.assertIn("Excluded From Top Research", page.body)
        self.assertIn("ITM or ATM; Top Research is OTM-only.", page.body)
        self.assertIn("Liq", page.body)
        self.assertIn("Window", page.body)
        self.assertIn("Scenario", page.body)
        self.assertIn("Rationale", page.body)
        self.assertIn("Supported by", page.body)
        self.assertIn("Cross-Window Intelligence", page.body)
        self.assertIn("Score Move", page.body)
        self.assertIn("Position Context", page.body)
        self.assertIn("Speculative Only", page.body)
        self.assertIn("Intent", page.body)
        self.assertIn("speculative_convexity", page.body)

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


def _research_groups(option_type: str, windows: tuple[str, ...], scores: tuple[float, ...], strike: float) -> dict[str, tuple[ContractResearch, ...]]:
    return {
        window: (_research_item(option_type, window, strike, score),)
        for window, score in zip(windows, scores)
    }


def _research_item(option_type: str, window: str, strike: float, score: float) -> ContractResearch:
    expiration = (date.today() + timedelta(days=int(window))).isoformat()
    contract = OptionContract(f"{option_type.upper()}{window}{strike}", option_type, expiration, strike, 2.0, 2.2, 2.1, 200, 1000, 0.35)
    model = BinomialResult("american_binomial", "fixture", 2.1, 0.4, 0.01, -0.02, 0.1, 0.01, False, 0.0, 2.1, (), ())
    factors = {
        "liquidity": 90.0,
        "spread": 90.0,
        "otm_proximity": 90.0,
        "otm_distance_pct": abs(strike - 100) / 100 * 100,
        "iv_condition": 90.0,
        "premium_quality": 90.0,
        "window_fit": 90.0,
        "timing_alignment": 90.0,
        "scenario_behavior": 90.0,
    }
    return ContractResearch(contract, score, factors, (), model, strike + 2.1 if option_type == "call" else strike - 2.1, "fixture rationale")


def _cross_rows(option_type: str, classification: str):
    return (
        CrossWindowResearch(option_type, "near_otm", classification, ("30", "60"), (70.0, 72.0), (1, 1), 2.0, 0, "fixture"),
    )


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code
