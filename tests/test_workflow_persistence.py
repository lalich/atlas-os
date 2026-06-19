"""Tests for workflow run persistence."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_os.core.artifacts import list_artifacts_for_run
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

