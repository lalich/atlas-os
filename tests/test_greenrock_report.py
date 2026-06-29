"""Tests for GreenRock report draft quality."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock import pdf_export
from atlas_os.greenrock.report import build_report_draft
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


if __name__ == "__main__":
    unittest.main()
