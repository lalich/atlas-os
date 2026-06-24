"""Tests for the reusable workflow runner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_os.core.audit_log import list_audit_logs
from atlas_os.core.reports import list_reports_for_run
from atlas_os.core.workflow_steps import StepStatus, list_workflow_steps
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.workflow import run_greenrock_workflow


class WorkflowRunnerTests(unittest.TestCase):
    def test_greenrock_runner_persists_step_states_and_audit_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                execution = run_greenrock_workflow(connection, root / "output")
                steps = list_workflow_steps(connection, execution.run.run_id)
                audit_logs = list_audit_logs(connection)
                reports = list_reports_for_run(connection, execution.run.run_id)

            self.assertEqual(execution.run.status, "awaiting_approval")
            self.assertEqual([step.step_name for step in steps], ["screen_candidates", "draft_report"])
            self.assertEqual(steps[0].status, StepStatus.COMPLETED)
            self.assertEqual(steps[1].status, StepStatus.BLOCKED_FOR_APPROVAL)
            self.assertEqual(reports[0].status, "blocked_for_approval")

            actions = {event.action for event in audit_logs}
            self.assertIn("workflow_run_created", actions)
            self.assertIn("step_started", actions)
            self.assertIn("step_completed", actions)
            self.assertIn("artifact_created", actions)
            self.assertIn("approval_created", actions)

    def test_artifacts_are_recorded_for_runner_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                execution = run_greenrock_workflow(connection, root / "output")

            artifact_types = {artifact.artifact_type for artifact in execution.artifacts}
            self.assertEqual(
                artifact_types,
                {"candidates_csv", "mega_rock_csv", "large_cap_csv", "small_cap_csv", "report_draft_md"},
            )


if __name__ == "__main__":
    unittest.main()
