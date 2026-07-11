"""Tests for GreenRock report draft quality."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.core.approvals import list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.reports import list_reports
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock import pdf_export
from atlas_os.greenrock.report import build_report_draft
from atlas_os.greenrock.report_dry_run import (
    ReportDryRunScheduleConfig,
    build_report_dry_run_markdown,
    due_report_schedule,
    preview_report_schedule,
    report_schedule_ledger_path,
    run_due_report_dry_runs,
)
from atlas_os.greenrock.staging import save_staged_candidates
from atlas_os.greenrock.staging_report import build_staging_report_markdown
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow


class GreenRockReportTests(unittest.TestCase):
    def test_pdf_green_table_headers_use_yellow_text(self) -> None:
        source = Path(pdf_export.__file__).read_text(encoding="utf-8")

        self.assertIn('("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174C3C"))', source)
        self.assertIn('("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#F3C969"))', source)

    def test_pdf_footer_uses_real_data_mode_when_report_is_real(self) -> None:
        class FakeCanvas:
            def __init__(self) -> None:
                self.drawn: list[str] = []

            def saveState(self) -> None:
                pass

            def setFont(self, *_args) -> None:
                pass

            def setFillColorRGB(self, *_args) -> None:
                pass

            def drawString(self, _x, _y, text: str) -> None:
                self.drawn.append(text)

            def drawRightString(self, _x, _y, text: str) -> None:
                self.drawn.append(text)

            def restoreState(self) -> None:
                pass

        canvas = FakeCanvas()
        doc = type("Doc", (), {"leftMargin": 36, "page": 1, "greenrock_data_mode": "Real"})()

        pdf_export._footer(canvas, doc)

        footer = "\n".join(canvas.drawn)
        self.assertIn("GreenRock Analysts - Real data draft/export", footer)
        self.assertNotIn("Mock data draft/export", footer)

    def test_report_draft_contains_required_sections(self) -> None:
        report = build_report_draft(run_id="greenrock-test-run")

        required_text = [
            "# GreenRock Analysts Monthly Opportunity Report",
            "## Technical Dislocation Screen",
            "**Date:**",
            "**Run ID:** greenrock-test-run",
            "**Data Mode:** MOCK",
            "**Selection Mode:** STRICT",
            "## How to Read This Report",
            "## Executive Summary",
            "## Market Setup / Regime Placeholder",
            "## Source Watchlists",
            "## Candidate Source Disclosure",
            "**Candidate Source:** Mock data",
            "- Source type: Mock data",
            "## Top Large-Cap Candidates",
            "## Top Small/Mid-Cap Candidates",
            "GreenRock Score",
            "Signal",
            "**Why It Screened In**",
            "**What Would Invalidate the Setup**",
            "## Risk Notes",
            "## Methodology Appendix",
            "## Human Approval Disclaimer",
            "## Data Mode Disclaimer",
            "## Compliance Notes",
        ]
        for text in required_text:
            self.assertIn(text, report.markdown)

    def test_report_uses_compliance_friendly_language(self) -> None:
        report = build_report_draft(run_id="greenrock-test-run")

        self.assertIn("not recommendations", report.markdown)
        self.assertIn("does not provide personalized investment advice", report.markdown)
        self.assertIn("does not", report.markdown)
        self.assertIn("guarantee outcomes", report.markdown)
        self.assertIn("blocked from publication", report.markdown)
        self.assertIn("mock sample data", report.markdown)
        self.assertNotIn("you should buy", report.markdown.lower())
        self.assertNotIn("guaranteed", report.markdown.lower())

    def test_workflow_report_file_contains_run_id_and_run_specific_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                workflow_run, _, approval = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )

            report_path = root / "output" / "greenrock" / workflow_run.run_id / "greenrock_report_draft.md"
            markdown = report_path.read_text(encoding="utf-8")

            self.assertTrue(report_path.exists())
            self.assertIn(f"**Run ID:** {workflow_run.run_id}", markdown)
            self.assertIn("**Data Mode:** MOCK", markdown)
        self.assertEqual(approval.artifact_path, str(report_path))

    def test_staging_report_source_disclosure_renders(self) -> None:
        markdown = build_staging_report_markdown(
            "greenrock-staging-test",
            (
                {
                    "ticker": "SOFI",
                    "staged_bucket": "small_mid",
                    "greenrock_score": "81.2",
                    "confidence": "74.5",
                    "evidence_agreement": "79.0",
                    "guardrail": "Mixed",
                    "research_priority": "This Week",
                    "top_bullish_signal": "Volume acceleration",
                    "top_caution_signal": "Mixed technical signals",
                    "source_list": "watchlist",
                    "source_scan_id": "scan-micro_moonshot-20260628000000",
                    "notes": "staging note",
                },
            ),
        )

        self.assertIn("**Candidate Source:** Staging-sourced", markdown)
        self.assertIn("**Data Mode:** REAL", markdown)
        self.assertIn("**Selection Mode:** STAGING", markdown)
        self.assertIn("scan-micro_moonshot-20260628000000", markdown)
        self.assertIn("staging note", markdown)

    def test_staging_report_uses_clean_empty_section_message(self) -> None:
        markdown = build_staging_report_markdown("greenrock-staging-empty", ())

        self.assertIn("No staged candidates in this bucket.", markdown)
        self.assertNotIn("| - | - | - |", markdown)

    def test_staging_report_uses_compact_main_tables_and_signal_notes(self) -> None:
        markdown = build_staging_report_markdown(
            "greenrock-staging-test",
            (
                {
                    "ticker": "SOFI",
                    "staged_bucket": "small_mid",
                    "greenrock_score": "81.2",
                    "confidence": "74.5",
                    "evidence_agreement": "79.0",
                    "guardrail": "Mixed",
                    "research_priority": "This Week",
                    "top_bullish_signal": "Volume acceleration",
                    "top_caution_signal": "Mixed technical signals",
                    "source_list": "watchlist",
                    "source_scan_id": "scan-micro_moonshot-20260628000000",
                    "notes": "staging note",
                },
            ),
        )
        small_mid_section = markdown.split("## Small/Mid Candidates", maxsplit=1)[1]
        main_table = small_mid_section.split("### Candidate Evidence Notes", maxsplit=1)[0]

        self.assertIn("| Ticker | Score | Confidence | Evidence | Guardrail | Priority | Source | Notes |", main_table)
        self.assertNotIn("Top Bullish Signal", main_table)
        self.assertNotIn("Top Caution Signal", main_table)
        self.assertIn("### Candidate Evidence Notes", small_mid_section)
        self.assertIn("- Top bullish signal: Volume acceleration", small_mid_section)
        self.assertIn("- Top caution signal: Mixed technical signals", small_mid_section)

    def test_report_dry_run_separates_research_sections_and_derivative_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            save_staged_candidates(
                output_dir,
                (
                    {
                        "ticker": "SOFI",
                        "staged_bucket": "small_mid",
                        "greenrock_score": "81.2",
                        "confidence": "74.5",
                        "evidence_agreement": "79.0",
                        "guardrail": "Mixed",
                        "research_priority": "This Week",
                        "top_bullish_signal": "Volume acceleration",
                        "top_caution_signal": "Mixed technical signals",
                        "notes": "wall review candidate",
                    },
                ),
            )
            _write_derivative_fixture(output_dir)

            markdown = build_report_dry_run_markdown(output_dir, dry_run_id="dry-run-test")

        self.assertIn("# GreenRock Report Agent Dry Run", markdown)
        self.assertIn("## Market Scan", markdown)
        self.assertIn("## Wall Candidates", markdown)
        self.assertIn("SOFI", markdown)
        self.assertIn("## Derivative Workbench Top Research", markdown)
        self.assertIn("OTM-only", markdown)
        self.assertIn("Supported by liquidity", markdown)
        self.assertIn("## Exclusions / No-Recommendation Explanations", markdown)
        self.assertIn("ITM or ATM; Top Research is OTM-only.", markdown)
        self.assertIn("## Strategy Intent", markdown)
        self.assertIn("speculative_convexity", markdown)
        self.assertIn("## Risk Notes", markdown)
        self.assertIn("## Human Review Required", markdown)
        self.assertIn("No email, publishing, brokerage execution", markdown)

    def test_report_dry_run_cli_creates_no_approval_report_or_artifact_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict(os.environ, env, clear=False):
                output, code = _run_cli_raw(["greenrock", "report-dry-run"])
                with connect(initialize_database(root / "atlas.db")) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    reports = list_reports(connection)
            dry_runs = tuple((root / "output" / "greenrock" / "report_dry_runs").glob("*.md"))
            dry_run_text = dry_runs[0].read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn("Report Agent dry run created", output)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(reports, ())
        self.assertEqual(len(dry_runs), 1)
        self.assertIn("DRAFT / REVIEW ONLY", dry_run_text)

    def test_report_schedule_normal_month_end_preview(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York")

        rows = preview_report_schedule(Path("/tmp/unused"), now=_dt("2026-07-01T10:00:00-04:00"), months=1, config=config)

        self.assertEqual(rows[0].scheduled_for, "2026-07-30T19:00-04:00")
        self.assertEqual(rows[0].schedule_reason, "month_end_before_last_trading_day")
        self.assertTrue(rows[0].review_required)

    def test_report_schedule_monday_last_trading_day_friday_and_sunday(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York")

        rows = preview_report_schedule(Path("/tmp/unused"), now=_dt("2026-08-01T10:00:00-04:00"), months=2, config=config)

        self.assertEqual([row.scheduled_for for row in rows], ["2026-08-28T19:00-04:00", "2026-08-30T11:00-04:00"])
        self.assertEqual(rows[0].schedule_reason, "friday_evening_before_monday_last_trading_day")
        self.assertEqual(rows[1].schedule_reason, "sunday_morning_refresh_before_monday_last_trading_day")

    def test_report_schedule_sunday_refresh_can_be_disabled(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York", sunday_refresh_enabled=False)

        rows = preview_report_schedule(Path("/tmp/unused"), now=_dt("2026-08-01T10:00:00-04:00"), months=2, config=config)

        self.assertEqual(rows[0].scheduled_for, "2026-08-28T19:00-04:00")
        self.assertNotIn("sunday", " ".join(row.schedule_reason for row in rows))

    def test_report_schedule_holiday_adjusted_last_trading_day(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York", market_holidays=("2026-07-31",))

        rows = preview_report_schedule(Path("/tmp/unused"), now=_dt("2026-07-01T10:00:00-04:00"), months=1, config=config)

        self.assertEqual(rows[0].scheduled_for, "2026-07-29T19:00-04:00")
        self.assertEqual(rows[0].schedule_reason, "month_end_before_last_trading_day")

    def test_report_schedule_prevents_duplicate_generation(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York")
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"

            first = run_due_report_dry_runs(output_dir, now=_dt("2026-07-30T20:00:00-04:00"), config=config)
            second = run_due_report_dry_runs(output_dir, now=_dt("2026-07-30T21:00:00-04:00"), config=config)
            ledger = json.loads(report_schedule_ledger_path(output_dir).read_text(encoding="utf-8"))

        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        self.assertEqual(len(ledger["generated"]), 1)

    def test_report_schedule_no_due_report(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York")
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"

            due = due_report_schedule(output_dir, now=_dt("2026-07-30T18:59:00-04:00"), config=config)

        self.assertEqual(due, ())

    def test_report_schedule_timezone_handling_is_deterministic(self) -> None:
        config = ReportDryRunScheduleConfig(timezone="America/New_York")
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"

            due = due_report_schedule(output_dir, now=datetime(2026, 7, 30, 23, 0, tzinfo=ZoneInfo("UTC")), config=config)

        self.assertEqual(due[0].scheduled_for, "2026-07-30T19:00-04:00")

    def test_report_schedule_cli_preview_and_run_due_are_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict(os.environ, env, clear=False):
                preview_output, preview_code = _run_cli_raw(["greenrock", "report-schedule", "preview", "--count", "1"])
                run_output, run_code = _run_cli_raw(["greenrock", "report-schedule", "run-due"])
                with connect(initialize_database(root / "atlas.db")) as connection:
                    approvals = list_approvals(connection)
                    artifacts = list_artifacts(connection)
                    reports = list_reports(connection)

        self.assertEqual(preview_code, 0)
        self.assertEqual(run_code, 0)
        self.assertIn("schedule preview", preview_output)
        self.assertIn("No due scheduled dry runs.", run_output)
        self.assertEqual(approvals, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(reports, ())


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code


def _write_derivative_fixture(output_dir: Path) -> None:
    snapshot_dir = output_dir / "greenrock" / "derivatives" / "snapshots" / "SOFI" / "deriv-sofi-test"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    analysis = {
        "ticker": "SOFI",
        "snapshot_id": "deriv-sofi-test",
        "created_at": "2026-07-10T00:00:00+00:00",
        "provider": "fixture",
        "underlying_price": 10.0,
        "top_calls": {
            "30": [
                {
                    "contract": {"option_type": "call", "expiration": "2026-08-21", "strike": 11.0},
                    "score": 82.4,
                    "ranking_rationale": "Supported by liquidity, spread, OTM fit; watch no major factor drag.",
                    "strategy_intent": "speculative_convexity",
                    "intent_rationale": "No position context is recorded and cross-window research is constructive.",
                    "manifesto_alignment": "strengthening",
                    "position_context_alignment": "speculative_only",
                }
            ]
        },
        "top_puts": {
            "30": [
                {
                    "contract": {"option_type": "put", "expiration": "2026-08-21", "strike": 9.0},
                    "score": 79.1,
                    "ranking_rationale": "Supported by premium, timing; watch no major factor drag.",
                    "strategy_intent": "research_only",
                    "intent_rationale": "Research-only options context; no execution action is available.",
                    "manifesto_alignment": "stable",
                    "position_context_alignment": "unknown",
                }
            ]
        },
        "excluded_calls": {
            "30": [
                {
                    "contract": {"option_type": "call", "expiration": "2026-08-21", "strike": 9.0},
                    "reasons": ["ITM or ATM; Top Research is OTM-only."],
                }
            ]
        },
        "excluded_puts": {},
    }
    (snapshot_dir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


if __name__ == "__main__":
    unittest.main()
