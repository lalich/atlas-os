"""Tests for the local Atlas Command Center."""

from __future__ import annotations

import tempfile
import types
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import build_parser, main
from atlas_os.config import get_settings
from atlas_os.core.approvals import list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.workflow_runs import list_workflow_runs
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.market_data import MarketDataProvider
from atlas_os.greenrock.models import FundamentalSnapshot, PriceBar
from atlas_os.greenrock.pdf_export import render_markdown_report_to_pdf
from atlas_os.greenrock.report import build_sample_report
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.score import calculate_score_preview, confidence_band
from atlas_os.greenrock.staging import add_staged_candidate
from atlas_os.web_app import dispatch_request


class CommandCenterTests(unittest.TestCase):
    def test_atlas_serve_command_exists(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)

    def test_dashboard_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/")

        self.assertEqual(response.status, 200)
        self.assertIn("Atlas Inbox", response.body)
        self.assertIn("What needs your attention", response.body)
        self.assertIn("Pending Approvals", response.body)
        self.assertIn("Development Mode", response.body)
        self.assertIn("Last Refresh:", response.body)
        self.assertIn("GreenRock Picks Board", response.body)

    def test_project_directory_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/projects")

        self.assertEqual(response.status, 200)
        self.assertIn("Project Directory", response.body)
        self.assertIn("GreenRock Analysts", response.body)
        self.assertIn("Variance Capital / The Bat Signal", response.body)

    def test_greenrock_page_route_returns_200_and_renders_approvals(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/greenrock")

        self.assertEqual(response.status, 200)
        self.assertIn("Report Review Console", response.body)
        self.assertIn("Approvals", response.body)
        self.assertIn("pending", response.body)
        self.assertIn("Approve", response.body)
        self.assertIn("Reject", response.body)
        self.assertIn("GreenRock Score", response.body)
        self.assertIn("Signal Label", response.body)
        self.assertIn("Mega Rock Candidate Pool", response.body)
        self.assertIn("AAPL", response.body)
        self.assertIn("Run Sample/Mock Report", response.body)
        self.assertIn("Run Legacy Watchlist Report", response.body)
        self.assertIn("Generate Draft From Staging", response.body)
        self.assertIn("GreenRock Picks Board", response.body)
        self.assertIn("Score Any Ticker", response.body)
        self.assertIn("/static/greenrock_logo.png", response.body)

    def test_greenrock_picks_route_returns_200_with_finviz_links_and_23_slots(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/greenrock/picks")
            cli_exit = main(["greenrock", "picks-board"])

        self.assertEqual(response.status, 200)
        self.assertEqual(cli_exit, 0)
        self.assertIn("Picks Board", response.body)
        self.assertIn("Mega Rock Pick", response.body)
        self.assertIn("Large-Cap Picks", response.body)
        self.assertIn("Small/Mid-Cap Picks", response.body)
        self.assertIn("https://finviz.com/quote.ashx?t=", response.body)
        self.assertIn("Powered by Atlas OS", response.body)
        self.assertIn("MOCK DATA", response.body)
        self.assertIn("GreenRock Score Calculator", response.body)
        self.assertIn("/static/greenrock_logo.png", response.body)
        self.assertIn("Mega Rock: 1/1", response.body)
        self.assertIn("Large Cap: 11/11", response.body)
        self.assertIn("Small/Mid: 11/11", response.body)
        self.assertEqual(response.body.count("data-pick-slot="), 23)

    def test_picks_route_shows_incomplete_section_warnings(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            settings = get_settings()
            with connect(initialize_database(settings.db_path)) as connection:
                run = list_workflow_runs(connection)[0]
            small_csv = Path(run.output_paths["small_cap"])
            header = small_csv.read_text(encoding="utf-8").splitlines()[0]
            small_csv.write_text(header + "\n", encoding="utf-8")
            response = dispatch_request("GET", "/greenrock/picks")

        self.assertEqual(response.status, 200)
        self.assertIn("Data Quality Warning", response.body)
        self.assertIn("Small/mid-cap section has 0/11 picks", response.body)

    def test_score_page_route_returns_200_and_form_returns_result(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env():
            page = dispatch_request("GET", "/greenrock/score")
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score", "ticker=LC01")

        self.assertEqual(page.status, 200)
        self.assertIn("GreenRock Score Calculator", page.body)
        self.assertIn("/static/greenrock_logo.png", page.body)
        self.assertIn("logo-score-button", page.body)
        self.assertIn("aria-label=\"Calculate GreenRock Score\"", page.body)
        score_form = page.body.split('<form method="post" action="/greenrock/score" class="score-form">', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
        self.assertIn("score-button-logo", score_form)
        self.assertNotIn("<span>Calculate Score</span>", score_form)
        self.assertIn("Score any ticker against the GreenRock technical dislocation framework.", page.body)
        self.assertNotIn('name="data_mode"', page.body)
        self.assertNotIn(">Mock<", page.body)
        self.assertNotIn(">Real<", page.body)
        self.assertEqual(result.status, 200)
        self.assertIn("LC01 Score Preview", result.body)
        self.assertIn("GreenRock Score", result.body)
        self.assertIn("GreenRock Confidence", result.body)
        self.assertIn(preview.confidence_band, result.body)
        self.assertIn("Evidence Agreement", result.body)
        self.assertIn("Evidence Engine", result.body)
        self.assertIn("Neutral / Watch Items", result.body)
        self.assertIn(preview.score_confidence_divergence, result.body)
        self.assertIn("Research Priority", result.body)
        self.assertIn("Analyst Summary", result.body)
        summary_section = result.body.split("Analyst Summary", maxsplit=1)[1].split("Why Confidence Is This Level", maxsplit=1)[0]
        self.assertIn("https://finviz.com/quote.ashx?t=LC01", summary_section)
        self.assertIn("Why Confidence Is This Level", result.body)
        self.assertIn("Positive Confidence Drivers", result.body)
        self.assertIn("Confidence Drags", result.body)
        self.assertIn("How the Score Works", result.body)
        self.assertIn("How the Score Ranks", result.body)
        self.assertIn(f"{preview.candidate.symbol}: {preview.candidate.score:.1f}", result.body)
        self.assertIn("Score Breakdown", result.body)
        self.assertIn("Current methodology weights total 100 points", result.body)
        self.assertIn("https://finviz.com/quote.ashx?t=LC01", result.body)

    def test_score_page_renders_explainability_and_price_targets(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env():
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score", "ticker=LC01")

        self.assertEqual(result.status, 200)
        for component in (
            "52-week low proximity",
            "Bollinger Band setup",
            "RSI",
            "Volume acceleration",
            "Moving average structure",
            "Bullish / Bearish Evidence",
        ):
            self.assertIn(component, result.body)
        self.assertIn("Raw metric", result.body)
        self.assertIn("Component score", result.body)
        self.assertIn("Weight", result.body)
        self.assertIn("plain-English rationale", result.body)
        self.assertIn("Bullish Evidence", result.body)
        self.assertIn("Bearish Evidence", result.body)
        self.assertIn("What to Watch Next", result.body)
        self.assertIn("Moving-average evidence is mixed.", result.body)
        self.assertIn("Watch for price reclaiming", result.body)
        self.assertIn("Atlas flags LC01", result.body)
        self.assertIn("Fundamental Guardrails", result.body)
        self.assertIn(preview.fundamental_guardrails.label, result.body)
        self.assertIn("Bullish Fundamental Evidence", result.body)
        self.assertIn("Bearish Fundamental Evidence", result.body)
        self.assertIn("Confidence Impact", result.body)
        self.assertIn("Evidence Engine", result.body)
        self.assertIn("Agreement", result.body)
        self.assertIn("Contribution", result.body)
        self.assertIn("1-Year Statistical Price Targets", result.body)
        self.assertIn("Historical lookback", result.body)
        self.assertIn("5 years", result.body)
        self.assertIn("Horizon", result.body)
        self.assertIn("not forecasts or guarantees", result.body)
        self.assertIn("All-Time High", result.body)
        self.assertIn("+2 SD", result.body)
        self.assertIn("+3 SD", result.body)
        self.assertIn("+5 SD", result.body)
        self.assertIn("+7 SD", result.body)
        self.assertIn("target-below-ath", result.body)
        self.assertIn("target-above-ath", result.body)

    def test_score_intelligence_fields_are_calculated(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        lower_confidence = calculate_score_preview("LC01", provider=FlatHistoryProvider())
        mock_preview = calculate_score_preview("LC01", data_mode="mock")

        self.assertGreaterEqual(preview.confidence_score, 0)
        self.assertLessEqual(preview.confidence_score, 100)
        self.assertLess(preview.confidence_score, 100)
        self.assertLess(lower_confidence.confidence_score, preview.confidence_score)
        self.assertEqual(preview.confidence_band, confidence_band(preview.confidence_score))
        self.assertTrue(preview.confidence_drivers)
        self.assertTrue(preview.confidence_drags)
        self.assertEqual(mock_preview.research_priority, "This Week")
        self.assertEqual(preview.research_priority, "Ignore")
        self.assertTrue(preview.bullish_evidence)
        self.assertTrue(preview.bearish_evidence)
        self.assertTrue(preview.neutral_evidence)
        self.assertTrue(preview.evidence_items)
        self.assertGreaterEqual(preview.evidence_agreement_score, 0)
        self.assertLessEqual(preview.evidence_agreement_score, 100)
        self.assertTrue(preview.score_confidence_divergence)
        self.assertTrue(preview.watch_next)
        self.assertIn("Atlas flags LC01", preview.analyst_summary)

    def test_evidence_agreement_rises_and_falls_with_signal_alignment(self) -> None:
        aligned = calculate_score_preview("LC01", provider=StrongFundamentalsProvider())
        conflicted = calculate_score_preview("LC01", provider=ConflictingSignalProvider())

        self.assertGreater(aligned.evidence_agreement_score, conflicted.evidence_agreement_score)
        self.assertTrue(any(item.direction == "bullish" for item in aligned.evidence_items))
        self.assertTrue(any(item.direction == "bearish" for item in conflicted.evidence_items))

    def test_weak_or_missing_support_lowers_confidence_more_than_score(self) -> None:
        strong = calculate_score_preview("LC01", provider=StrongFundamentalsProvider())
        weak = calculate_score_preview("LC01", provider=WeakFundamentalsProvider())
        missing = calculate_score_preview("LC01", provider=MissingFundamentalsProvider())

        self.assertGreater(strong.confidence_score - weak.confidence_score, strong.candidate.score - weak.candidate.score)
        self.assertGreater(strong.confidence_score - missing.confidence_score, strong.candidate.score - missing.candidate.score)
        self.assertIn("Confidence", weak.score_confidence_divergence)

    def test_fundamental_guardrails_weight_confidence_more_than_score(self) -> None:
        strong = calculate_score_preview("LC01", provider=StrongFundamentalsProvider())
        weak = calculate_score_preview("LC01", provider=WeakFundamentalsProvider())

        self.assertEqual(strong.fundamental_guardrails.label, "Strong Balance Sheet")
        self.assertEqual(strong.fundamental_guardrail_adjustment, 2.0)
        self.assertGreater(strong.confidence_score, weak.confidence_score)
        self.assertLessEqual(
            abs(strong.candidate.score - weak.candidate.score),
            7.0,
        )
        self.assertGreater(
            abs(strong.confidence_score - weak.confidence_score),
            abs(strong.candidate.score - weak.candidate.score),
        )

    def test_missing_and_weak_fundamentals_lower_confidence(self) -> None:
        strong = calculate_score_preview("LC01", provider=StrongFundamentalsProvider())
        missing = calculate_score_preview("LC01", provider=MissingFundamentalsProvider())
        low_quick = calculate_score_preview("LC01", provider=LowQuickRatioProvider())
        dilution = calculate_score_preview("LC01", provider=DilutionProvider())

        self.assertEqual(missing.fundamental_guardrails.label, "Insufficient Data")
        self.assertLess(missing.confidence_score, strong.confidence_score)
        self.assertEqual(low_quick.fundamental_guardrails.label, "Red Flag")
        self.assertLess(low_quick.confidence_score, strong.confidence_score)
        self.assertEqual(dilution.fundamental_guardrails.label, "Caution")
        self.assertLess(dilution.confidence_score, strong.confidence_score)
        self.assertLessEqual(abs(low_quick.fundamental_guardrail_adjustment), 5.0)

    def test_confidence_calibration_drags(self) -> None:
        complete = calculate_score_preview("LC01", provider=FullHistoryProvider())
        missing_market_cap = calculate_score_preview("LC01", provider=MissingMarketCapProvider())
        short_history = calculate_score_preview("LC01", provider=ShortHistoryProvider())
        noisy = calculate_score_preview("LC01", provider=NoisyHistoryProvider())
        conflicting = calculate_score_preview("LC01", provider=ConflictingSignalProvider())

        self.assertLess(missing_market_cap.confidence_score, complete.confidence_score)
        self.assertIn("Missing market cap.", missing_market_cap.confidence_drags)
        self.assertLess(short_history.confidence_score, complete.confidence_score)
        self.assertTrue(any("Less than 1 year" in item for item in short_history.confidence_drags))
        self.assertLess(noisy.confidence_score, complete.confidence_score)
        self.assertTrue(any("Volatile/noisy price action" in item for item in noisy.confidence_drags))
        self.assertLess(conflicting.confidence_score, complete.confidence_score)
        self.assertTrue(any("Mixed technical signals" in item or "conflict" in item for item in conflicting.confidence_drags))

    def test_confidence_band_mapping(self) -> None:
        self.assertEqual(confidence_band(95), "Very High Confidence")
        self.assertEqual(confidence_band(80), "High Confidence")
        self.assertEqual(confidence_band(65), "Moderate Confidence")
        self.assertEqual(confidence_band(45), "Low Confidence")
        self.assertEqual(confidence_band(20), "Very Low Confidence")

    def test_score_page_shows_clean_price_target_warning(self) -> None:
        preview = calculate_score_preview("LC01", provider=FlatHistoryProvider())
        with _isolated_env():
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score", "ticker=LC01")

        self.assertEqual(result.status, 200)
        self.assertIn("Price targets cannot be calculated cleanly", result.body)
        self.assertIn("1-year statistical price targets unavailable", result.body)

    def test_score_preview_uses_full_history_for_ath_and_five_year_targets(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())

        self.assertEqual(preview.all_time_high, 500.0)
        self.assertEqual(preview.price_target_lookback, "5 years")
        self.assertEqual(preview.price_target_horizon, "1 year")
        self.assertTrue(all(target.price is not None for target in preview.price_targets))

    def test_save_to_list_appears_after_analysis_sections(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env():
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score", "ticker=LC01")

        self.assertLess(result.body.index("Analyst Summary"), result.body.index("Save Ticker to List"))
        self.assertLess(result.body.index("Bullish Evidence"), result.body.index("Save Ticker to List"))
        self.assertLess(result.body.index("What to Watch Next"), result.body.index("Save Ticker to List"))
        self.assertLess(result.body.index("1-Year Statistical Price Targets"), result.body.index("Save Ticker to List"))

    def test_score_page_missing_real_provider_shows_setup_instructions(self) -> None:
        with _isolated_env():
            with patch.dict("os.environ", {"ATLAS_MARKET_DATA_PROVIDER": ""}, clear=False):
                result = dispatch_request("POST", "/greenrock/score", "ticker=AAPL")

        self.assertEqual(result.status, 200)
        self.assertIn("Score Preview Blocked", result.body)
        self.assertIn("export ATLAS_MARKET_DATA_PROVIDER=yfinance", result.body)
        self.assertIn('python3 -m pip install -e ".[market-data]"', result.body)

    def test_score_page_invalid_ticker_shows_clean_warning(self) -> None:
        with _isolated_env():
            with patch.dict("os.environ", {"ATLAS_MARKET_DATA_PROVIDER": ""}, clear=False):
                result = dispatch_request("POST", "/greenrock/score", "ticker=NOTREAL")

        self.assertEqual(result.status, 200)
        self.assertIn("Score Preview Blocked", result.body)
        self.assertIn("No report, approval, artifact", result.body)

    def test_score_calculation_creates_no_reports_approvals_or_artifacts(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env() as root:
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score", "ticker=LC01")
            with connect(initialize_database(root / "atlas.db")) as connection:
                approvals = list_approvals(connection)
                artifacts = list_artifacts(connection)
                runs = list_workflow_runs(connection)

        self.assertEqual(result.status, 200)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_save_ticker_to_watchlist_and_duplicate_are_local_only(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env() as root:
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                first = dispatch_request("POST", "/greenrock/score/save", "ticker=LC01&list_key=watchlist")
                second = dispatch_request("POST", "/greenrock/score/save", "ticker=LC01&list_key=watchlist")
            saved = (root / "output" / "greenrock" / "watchlists" / "watchlist.csv").read_text(encoding="utf-8")
            with connect(initialize_database(root / "atlas.db")) as connection:
                approvals = list_approvals(connection)
                artifacts = list_artifacts(connection)
                runs = list_workflow_runs(connection)

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertIn("LC01 saved to Watchlist", first.body)
        self.assertIn("duplicate ignored", second.body)
        self.assertEqual(saved.count("LC01"), 1)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_save_ticker_bucket_mismatch_warning_appears(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env() as root:
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score/save", "ticker=LC01&list_key=large_cap")
            large_path = root / "output" / "greenrock" / "universes" / "large_cap.csv"
            saved = large_path.read_text(encoding="utf-8") if large_path.exists() else ""

        self.assertEqual(result.status, 200)
        self.assertIn("Save blocked", result.body)
        self.assertIn("does not currently meet the requirements for Large Cap Watchlist", result.body)
        self.assertIn("Consider adding it to Small/Mid Watchlist or Personal Watchlist instead.", result.body)
        self.assertNotIn("LC01", saved)

    def test_personal_watchlist_fallback_works(self) -> None:
        preview = calculate_score_preview("LC01", provider=FullHistoryProvider())
        with _isolated_env() as root:
            with patch("atlas_os.web_app.calculate_score_preview", return_value=preview):
                result = dispatch_request("POST", "/greenrock/score/save", "ticker=LC01&list_key=personal_watchlist")
            saved = (root / "output" / "greenrock" / "watchlists" / "personal_watchlist.csv").read_text(encoding="utf-8")

        self.assertEqual(result.status, 200)
        self.assertIn("LC01 saved to Personal Watchlist", result.body)
        self.assertIn("LC01", saved)

    def test_report_pdf_generation_handles_missing_logo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown_path = root / "report.md"
            pdf_path = root / "report.pdf"
            markdown_path.write_text(build_sample_report().markdown, encoding="utf-8")
            rendered = render_markdown_report_to_pdf(markdown_path, pdf_path)

        self.assertEqual(rendered, pdf_path)

    def test_browser_run_buttons_pass_selected_data_mode(self) -> None:
        fake_run = types.SimpleNamespace(run_id="greenrock-test", data_mode="real")
        fake_approval = types.SimpleNamespace(id=42)
        with _isolated_env():
            with patch("atlas_os.web_app.run_greenrock_screening_workflow", return_value=(fake_run, (), fake_approval)) as workflow:
                real_response = dispatch_request("POST", "/greenrock/run-report", "data_mode=real")
                real_data_mode = workflow.call_args.kwargs["data_mode"]
            fake_run.data_mode = "mock"
            with patch("atlas_os.web_app.run_greenrock_screening_workflow", return_value=(fake_run, (), fake_approval)) as workflow:
                mock_response = dispatch_request("POST", "/greenrock/run-report", "data_mode=mock")
                mock_data_mode = workflow.call_args.kwargs["data_mode"]

        self.assertEqual(real_response.status, 303)
        self.assertEqual(mock_response.status, 303)
        self.assertEqual(real_data_mode, "real")
        self.assertEqual(mock_data_mode, "mock")

    def test_failed_browser_real_provider_creates_no_approval_or_artifacts(self) -> None:
        with _isolated_env() as root:
            with patch.dict(
                "os.environ",
                {
                    "ATLAS_MARKET_DATA_PROVIDER": "",
                    "ATLAS_GREENROCK_REAL_TICKERS": "",
                },
                clear=False,
            ):
                response = dispatch_request("POST", "/greenrock/run-report", "data_mode=real")
            with connect(initialize_database(root / "atlas.db")) as connection:
                approvals = list_approvals(connection)
                artifacts = list_artifacts(connection)
                runs = list_workflow_runs(connection)

        self.assertEqual(response.status, 303)
        self.assertIn("REAL+report+blocked", response.location)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(runs, ())

    def test_task_board_route_returns_200_and_can_create_task(self) -> None:
        with _isolated_env():
            create_response = dispatch_request(
                "POST",
                "/tasks",
                "name=Review+monthly+packet&division=greenrock&notes=Confirm+mock+data",
            )
            update_response = dispatch_request("POST", "/tasks/1/status", "status=awaiting_review")
            response = dispatch_request("GET", "/tasks")

        self.assertEqual(create_response.status, 303)
        self.assertEqual(update_response.status, 303)
        self.assertEqual(response.status, 200)
        self.assertIn("Review monthly packet", response.body)
        self.assertIn("Manual Operator Queue", response.body)
        self.assertIn("Confirm mock data", response.body)
        self.assertIn("Awaiting Review", response.body)

    def test_agent_monitor_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/agents")

        self.assertEqual(response.status, 200)
        self.assertIn("Planned Agent HUD", response.body)
        self.assertIn("Atlas Core", response.body)
        self.assertIn("GreenRock Analyst Agent", response.body)
        self.assertIn("inactive", response.body)
        self.assertIn("planned", response.body)

    def test_approval_confirmation_route_works(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/approvals/1/confirm?action=approve")

        self.assertEqual(response.status, 200)
        self.assertIn("Human Approval Gate", response.body)
        self.assertIn("Approve Approval 1", response.body)
        self.assertIn("Approve locally", response.body)

    def test_browser_approve_action_updates_approval_state(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            decide_response = dispatch_request(
                "POST",
                "/approvals/1/decide",
                "action=approve&return_to=/greenrock",
            )
            greenrock = dispatch_request("GET", "/greenrock")

        self.assertEqual(decide_response.status, 303)
        self.assertIn("approved", greenrock.body)
        self.assertIn("Export PDF after approval", greenrock.body)

    def test_browser_reject_action_updates_approval_state(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            decide_response = dispatch_request(
                "POST",
                "/approvals/1/decide",
                "action=reject&return_to=/greenrock",
            )
            greenrock = dispatch_request("GET", "/greenrock")

        self.assertEqual(decide_response.status, 303)
        self.assertIn("rejected", greenrock.body)

    def test_dashboard_renders_pending_approval_indicators(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/")

        self.assertEqual(response.status, 200)
        self.assertIn("1</strong><h2>Pending Approvals", response.body)
        self.assertIn("Review GreenRock Report", response.body)
        self.assertIn("Latest Data Source", response.body)
        self.assertIn("/greenrock/reports/", response.body)

    def test_greenrock_report_review_page_renders_metadata_candidates_and_controls(self) -> None:
        with _isolated_env() as root:
            main(["greenrock", "report-draft"])
            with connect(root / "atlas.db") as connection:
                run_id = list_workflow_runs(connection)[0].run_id
            response = dispatch_request("GET", f"/greenrock/reports/{run_id}/review")

        self.assertEqual(response.status, 200)
        self.assertIn("GreenRock Report Review Center", response.body)
        self.assertIn(run_id, response.body)
        self.assertIn("Data Mode", response.body)
        self.assertIn("MOCK", response.body)
        self.assertIn("Selection Mode", response.body)
        self.assertIn("Candidate Source", response.body)
        self.assertIn("Approval Status", response.body)
        self.assertIn("PDF Status", response.body)
        self.assertIn("Source Disclosure", response.body)
        self.assertIn("Mega Rock", response.body)
        self.assertIn("Large Cap", response.body)
        self.assertIn("Small/Mid", response.body)
        self.assertIn("LC04", response.body)
        self.assertIn("Approve pending report", response.body)
        self.assertIn("Reject pending report", response.body)
        self.assertIn("PDF export blocked until approval", response.body)

    def test_greenrock_report_review_page_renders_staging_evidence_notes(self) -> None:
        with _isolated_env() as root:
            add_staged_candidate(root / "output", "SOFI", "small_mid", notes="operator staging note")
            main(["greenrock", "report-from-staging", "--allow-underfilled", "--allow-missing-analytics"])
            with connect(root / "atlas.db") as connection:
                run_id = list_workflow_runs(connection)[0].run_id
            response = dispatch_request("GET", f"/greenrock/reports/{run_id}/review")

        self.assertEqual(response.status, 200)
        self.assertIn("Staging-sourced", response.body)
        self.assertIn("SOFI", response.body)
        self.assertIn("operator staging note", response.body)
        self.assertIn("Candidate Evidence Notes", response.body)
        self.assertIn("Top bullish signal", response.body)
        self.assertIn("Top caution signal", response.body)

    def test_greenrock_report_review_approval_redirects_back_to_review_and_unlocks_pdf(self) -> None:
        with _isolated_env() as root:
            main(["greenrock", "report-draft"])
            with connect(root / "atlas.db") as connection:
                run_id = list_workflow_runs(connection)[0].run_id
            review_path = f"/greenrock/reports/{run_id}/review"
            confirm = dispatch_request("GET", f"/approvals/1/confirm?action=approve&return_to={review_path}")
            decided = dispatch_request("POST", "/approvals/1/decide", f"action=approve&return_to={review_path}")
            review = dispatch_request("GET", review_path)

        self.assertEqual(confirm.status, 200)
        self.assertIn(review_path, confirm.body)
        self.assertEqual(decided.status, 303)
        self.assertIn(review_path, decided.location)
        self.assertIn("Export PDF", review.body)
        self.assertNotIn("PDF export blocked until approval", review.body)

    def test_greenrock_browser_pdf_export_works_for_approved_report(self) -> None:
        with _isolated_env() as root:
            main(["greenrock", "report-draft"])
            dispatch_request("POST", "/approvals/1/decide", "action=approve&return_to=/greenrock")
            export_response = dispatch_request("POST", "/greenrock/approvals/1/export-pdf")
            reports = dispatch_request("GET", "/reports")
            pdfs = list(root.glob("output/greenrock/*/greenrock_report_final.pdf"))
            pdf_is_valid = len(pdfs) == 1 and pdfs[0].read_bytes().startswith(b"%PDF")

        self.assertEqual(export_response.status, 303)
        self.assertEqual(len(pdfs), 1)
        self.assertTrue(pdf_is_valid)
        self.assertIn("greenrock_report_final.pdf", reports.body)

    def test_pending_approval_pdf_export_returns_safe_blocked_response(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("POST", "/greenrock/approvals/1/export-pdf")

        self.assertEqual(response.status, 400)
        self.assertIn("PDF Export Blocked", response.body)
        self.assertIn("requires an approved report", response.body)

    def test_invalid_approval_id_returns_clean_error(self) -> None:
        with _isolated_env():
            response = dispatch_request("POST", "/greenrock/approvals/not-a-number/export-pdf")

        self.assertEqual(response.status, 400)
        self.assertIn("Invalid Approval", response.body)
        self.assertIn("Approval ID must be a number", response.body)

    def test_malformed_greenrock_export_route_does_not_crash(self) -> None:
        with _isolated_env():
            response = dispatch_request("POST", "/greenrock/approvals/export-pdf")

        self.assertEqual(response.status, 404)
        self.assertIn("Route Not Found", response.body)


class FlatHistoryProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "flat_history_fixture"

    def fetch_stocks(self):
        stock = load_mock_stocks()[0]
        flat_prices = tuple(replace(price, close=50.0) for price in stock.prices)
        return (replace(stock, prices=flat_prices),)


class FullHistoryProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "full_history_fixture"

    def fetch_stocks(self):
        stock = load_mock_stocks()[0]
        start = date.today() - timedelta(days=1511)
        bars = []
        for index in range(1512):
            close = 80 + index * 0.02
            if index == 0:
                close = 500.0
            elif index >= 252:
                close = 100 + (5 if index % 2 else -5) + (index - 252) * 0.002
            if index > 1460:
                close -= (index - 1460) * 0.15
            bars.append(
                PriceBar(
                    date=start + timedelta(days=index),
                    close=round(max(close, 5.0), 2),
                    volume=1_000_000 + index * 100,
                )
            )
        return (replace(stock, prices=tuple(bars)),)


class StrongFundamentalsProvider(FullHistoryProvider):
    source_name = "strong_fundamentals_fixture"

    def fetch_stocks(self):
        stock = super().fetch_stocks()[0]
        return (
            replace(
                stock,
                fundamentals=FundamentalSnapshot(
                    cash_and_equivalents=8_000_000_000,
                    total_debt=2_000_000_000,
                    net_cash=6_000_000_000,
                    net_cash_per_share=12.0,
                    quick_ratio=1.8,
                    current_assets=12_000_000_000,
                    inventory=1_000_000_000,
                    current_liabilities=6_100_000_000,
                    shares_outstanding_current=500_000_000,
                    shares_outstanding_prior=505_000_000,
                    shares_outstanding_change_percent=-0.0099,
                    fundamental_data_source="fixture",
                    fundamental_data_warnings=(),
                ),
            ),
        )


class WeakFundamentalsProvider(FullHistoryProvider):
    source_name = "weak_fundamentals_fixture"

    def fetch_stocks(self):
        stock = super().fetch_stocks()[0]
        return (
            replace(
                stock,
                fundamentals=FundamentalSnapshot(
                    cash_and_equivalents=500_000_000,
                    total_debt=15_000_000_000,
                    net_cash=-14_500_000_000,
                    net_cash_per_share=-29.0,
                    quick_ratio=0.82,
                    current_assets=3_000_000_000,
                    inventory=900_000_000,
                    current_liabilities=2_560_000_000,
                    shares_outstanding_current=560_000_000,
                    shares_outstanding_prior=500_000_000,
                    shares_outstanding_change_percent=0.12,
                    fundamental_data_source="fixture",
                    fundamental_data_warnings=(),
                ),
            ),
        )


class MissingFundamentalsProvider(FullHistoryProvider):
    source_name = "missing_fundamentals_fixture"

    def fetch_stocks(self):
        stock = super().fetch_stocks()[0]
        return (replace(stock, fundamentals=None),)


class LowQuickRatioProvider(FullHistoryProvider):
    source_name = "low_quick_ratio_fixture"

    def fetch_stocks(self):
        stock = StrongFundamentalsProvider().fetch_stocks()[0]
        fundamentals = replace(stock.fundamentals, quick_ratio=0.6)
        return (replace(stock, fundamentals=fundamentals),)


class DilutionProvider(FullHistoryProvider):
    source_name = "dilution_fixture"

    def fetch_stocks(self):
        stock = StrongFundamentalsProvider().fetch_stocks()[0]
        fundamentals = replace(
            stock.fundamentals,
            shares_outstanding_current=560_000_000,
            shares_outstanding_prior=500_000_000,
            shares_outstanding_change_percent=0.12,
        )
        return (replace(stock, fundamentals=fundamentals),)


class MissingMarketCapProvider(FullHistoryProvider):
    source_name = "missing_market_cap_fixture"

    def fetch_stocks(self):
        stock = super().fetch_stocks()[0]
        return (replace(stock, market_cap=0, has_market_cap=False, skipped_reason="missing_market_cap"),)


class ShortHistoryProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "short_history_fixture"

    def fetch_stocks(self):
        stock = load_mock_stocks()[0]
        start = date.today() - timedelta(days=199)
        bars = tuple(
            PriceBar(
                date=start + timedelta(days=index),
                close=round(90 - index * 0.08 + ((index % 5) - 2) * 0.05, 2),
                volume=900_000 + index * 500,
            )
            for index in range(200)
        )
        return (replace(stock, prices=bars),)


class NoisyHistoryProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "noisy_history_fixture"

    def fetch_stocks(self):
        stock = load_mock_stocks()[0]
        start = date.today() - timedelta(days=1511)
        bars = []
        for index in range(1512):
            swing = 35 if index % 2 else -35
            close = max(5.0, 120 + swing + (index % 17) * 1.5)
            volume = 200_000 if index % 3 else 3_500_000
            bars.append(PriceBar(date=start + timedelta(days=index), close=round(close, 2), volume=volume))
        return (replace(stock, prices=tuple(bars)),)


class ConflictingSignalProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "conflicting_signal_fixture"

    def fetch_stocks(self):
        stock = load_mock_stocks()[0]
        start = date.today() - timedelta(days=1511)
        bars = tuple(
            PriceBar(
                date=start + timedelta(days=index),
                close=round(60 + index * 0.05, 2),
                volume=1_500_000 - (index % 200) * 100,
            )
            for index in range(1512)
        )
        return (replace(stock, prices=bars),)


class _isolated_env:
    def __enter__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patch = patch.dict(
            "os.environ",
            {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            },
            clear=False,
        )
        self.patch.__enter__()
        return root

    def __exit__(self, exc_type, exc, tb):
        self.patch.__exit__(exc_type, exc, tb)
        self.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
