"""Tests for GreenRock report draft quality."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.report import build_report_draft
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow


class GreenRockReportTests(unittest.TestCase):
    def test_report_draft_contains_required_sections(self) -> None:
        report = build_report_draft(run_id="greenrock-test-run")

        required_text = [
            "# GreenRock Analysts Monthly Report",
            "**Date:**",
            "**Run ID:** greenrock-test-run",
            "## Executive Summary",
            "## Methodology",
            "## Large Cap Candidates",
            "## Small Cap Candidates",
            "## Large Cap Screening Rationale",
            "## Small Cap Screening Rationale",
            "## Risk Notes",
            "## Human Approval Disclaimer",
            "## Mock-Data Disclaimer",
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
            self.assertEqual(approval.artifact_path, str(report_path))


if __name__ == "__main__":
    unittest.main()

