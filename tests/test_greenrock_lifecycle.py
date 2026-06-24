"""Tests for GreenRock report lifecycle cleanup."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.core.approvals import approve_approval, list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.audit_log import list_audit_logs
from atlas_os.core.workflow_runs import list_workflow_runs
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.lifecycle import cleanup_greenrock_drafts
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow
from atlas_os.web_app import dispatch_request, export_greenrock_pdf


class GreenRockLifecycleTests(unittest.TestCase):
    def test_browser_run_greenrock_report_creates_draft_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                response = dispatch_request("POST", "/greenrock/run-report")
                db_path = initialize_database(root / "atlas.db")
                with connect(db_path) as connection:
                    runs = list_workflow_runs(connection)
                    approvals = list_approvals(connection)

        self.assertEqual(response.status, 303)
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].status.value, "pending")

    def test_cleanup_dry_run_removes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                first_run, _, _ = run_greenrock_screening_workflow(connection, root / "output")
                second_run, _, _ = run_greenrock_screening_workflow(connection, root / "output")
                artifact_paths = [Path(artifact.path) for artifact in list_artifacts(connection)]
                result = cleanup_greenrock_drafts(connection, dry_run=True)
                active_artifacts = list_artifacts(connection)
                artifact_paths_still_exist = all(path.exists() for path in artifact_paths)

        self.assertTrue(result.dry_run)
        self.assertEqual(result.latest_draft_run_id, second_run.run_id)
        self.assertTrue(result.removed_files)
        self.assertTrue(artifact_paths_still_exist)
        self.assertEqual(len(active_artifacts), 10)
        self.assertNotEqual(first_run.run_id, second_run.run_id)

    def test_cleanup_preserves_latest_draft_final_pdfs_and_audit_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                db_path = initialize_database(root / "atlas.db")
                with connect(db_path) as connection:
                    first_run, _, first_approval = run_greenrock_screening_workflow(connection, root / "output")
                    approve_approval(connection, first_approval.id)
                    pdf_artifact = export_greenrock_pdf(first_approval.id)
                    second_run, _, _ = run_greenrock_screening_workflow(connection, root / "output")
                    audit_count_before = len(list_audit_logs(connection))
                    approval_count_before = len(list_approvals(connection))
                    first_draft_paths = [
                        Path(artifact.path)
                        for artifact in list_artifacts(connection)
                        if artifact.run_id == first_run.run_id and artifact.artifact_type != "report_final_pdf"
                    ]

                    result = cleanup_greenrock_drafts(connection, dry_run=False)
                    active_artifacts = list_artifacts(connection)
                    archived_artifacts = [
                        artifact for artifact in list_artifacts(connection, include_archived=True)
                        if artifact.status == "archived_removed"
                    ]
                    audit_count_after = len(list_audit_logs(connection))
                    approval_count_after = len(list_approvals(connection))

            latest_report = root / "output" / "greenrock" / second_run.run_id / "greenrock_report_draft.md"
            pdf_path = Path(pdf_artifact.path)
            latest_report_exists = latest_report.exists()
            pdf_exists = pdf_path.exists()
            first_drafts_removed = all(not path.exists() for path in first_draft_paths)

        self.assertEqual(result.latest_draft_run_id, second_run.run_id)
        self.assertTrue(latest_report_exists)
        self.assertTrue(pdf_exists)
        self.assertTrue(first_drafts_removed)
        self.assertTrue(all(artifact.run_id != first_run.run_id or artifact.artifact_type == "report_final_pdf" for artifact in active_artifacts))
        self.assertTrue(archived_artifacts)
        self.assertEqual(audit_count_before, audit_count_after)
        self.assertEqual(approval_count_before, approval_count_after)

    def test_cleanup_cli_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                _run_cli(["greenrock", "report-draft"])
                _run_cli(["greenrock", "report-draft"])
                output = _run_cli(["greenrock", "cleanup-drafts", "--dry-run"])

        self.assertIn("GreenRock draft cleanup", output)
        self.assertIn("dry_run: True", output)
        self.assertIn("removed_file_count:", output)
        self.assertIn("Audit logs and approval records were preserved.", output)

    def test_reports_index_surfaces_latest_draft_not_old_draft_clutter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                first = _run_cli(["greenrock", "report-draft"])
                second = _run_cli(["greenrock", "report-draft"])
                first_run = _line_value(first, "run_id")
                second_run = _line_value(second, "run_id")
                response = dispatch_request("GET", "/reports")

        self.assertEqual(response.status, 200)
        self.assertIn(second_run, response.body)
        self.assertNotIn(first_run, response.body)

    def test_final_reports_archive_route_lists_exported_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")
                run_id = _line_value(draft_output, "run_id")
                _run_cli(["approvals", "approve", approval_id])
                _run_cli(["greenrock", "export-pdf", approval_id])
                response = dispatch_request("GET", "/greenrock/final-reports")

        self.assertEqual(response.status, 200)
        self.assertIn("Final PDF Archive", response.body)
        self.assertIn(run_id, response.body)
        self.assertIn("greenrock_report_final.pdf", response.body)


def _run_cli(args: list[str]) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    if exit_code != 0:
        raise AssertionError(f"CLI exited with {exit_code}: {args}\n{buffer.getvalue()}")
    return buffer.getvalue()


def _line_value(output: str, label: str) -> str:
    prefix = f"{label}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError(f"Missing {label} in output:\n{output}")


if __name__ == "__main__":
    unittest.main()
