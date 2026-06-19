"""Tests for workflow run persistence."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_os.core.artifacts import list_artifacts_for_run
from atlas_os.core.approvals import list_approvals
from atlas_os.core.workflow_runs import get_workflow_run
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow


class WorkflowPersistenceTests(unittest.TestCase):
    def test_greenrock_workflow_persists_run_metadata_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                workflow_run, artifacts, approval = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )
                stored_run = get_workflow_run(connection, workflow_run.run_id)
                stored_artifacts = list_artifacts_for_run(connection, workflow_run.run_id)

            self.assertEqual(stored_run.division, "greenrock")
            self.assertEqual(stored_run.workflow_name, "greenrock.local-screening")
            self.assertEqual(stored_run.status, "awaiting_approval")
            self.assertIsNotNone(stored_run.started_at)
            self.assertIsNotNone(stored_run.completed_at)
            self.assertTrue(stored_run.mock_data_used)
            self.assertEqual(len(artifacts), 4)
            self.assertEqual(len(stored_artifacts), 4)
            self.assertEqual(approval.status.value, "pending")
            self.assertIn("report_draft", stored_run.output_paths)

    def test_output_paths_are_stored_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                workflow_run, _, _ = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )
                row = connection.execute(
                    "SELECT output_paths FROM workflow_runs WHERE run_id = ?",
                    (workflow_run.run_id,),
                ).fetchone()

            output_paths = json.loads(row["output_paths"])
            self.assertEqual(
                set(output_paths),
                {"all", "large_cap", "small_cap", "report_draft"},
            )
            for path in output_paths.values():
                self.assertIn(f"greenrock/{workflow_run.run_id}", path)

    def test_repeated_report_drafts_write_run_specific_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                first_run, _, _ = run_greenrock_screening_workflow(
                    connection,
                    output_dir,
                    include_report_draft=True,
                )
                second_run, _, _ = run_greenrock_screening_workflow(
                    connection,
                    output_dir,
                    include_report_draft=True,
                )
                first_artifacts = list_artifacts_for_run(connection, first_run.run_id)
                second_artifacts = list_artifacts_for_run(connection, second_run.run_id)
                approvals = list_approvals(connection)

            first_folder = output_dir / "greenrock" / first_run.run_id
            second_folder = output_dir / "greenrock" / second_run.run_id
            first_report = first_folder / "greenrock_report_draft.md"
            second_report = second_folder / "greenrock_report_draft.md"

            self.assertNotEqual(first_run.run_id, second_run.run_id)
            self.assertTrue(first_folder.is_dir())
            self.assertTrue(second_folder.is_dir())
            self.assertTrue(first_report.exists())
            self.assertTrue(second_report.exists())

            first_paths = {artifact.path for artifact in first_artifacts}
            second_paths = {artifact.path for artifact in second_artifacts}
            self.assertTrue(all(f"greenrock/{first_run.run_id}" in path for path in first_paths))
            self.assertTrue(all(f"greenrock/{second_run.run_id}" in path for path in second_paths))
            self.assertIn(str(first_report), first_paths)
            self.assertIn(str(second_report), second_paths)

            approval_by_run = {approval.run_id: approval for approval in approvals}
            self.assertEqual(approval_by_run[first_run.run_id].artifact_path, str(first_report))
            self.assertEqual(approval_by_run[second_run.run_id].artifact_path, str(second_report))

    def test_schema_initializes_required_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = initialize_database(Path(directory) / "atlas.db")
            with sqlite3.connect(db_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertIn("workflow_runs", tables)
        self.assertIn("artifacts", tables)
        self.assertIn("approvals", tables)


if __name__ == "__main__":
    unittest.main()
