"""Tests for local GreenRock report-agent orchestration."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main
from atlas_os.core.artifacts import list_artifacts_for_run
from atlas_os.core.audit_log import list_audit_logs
from atlas_os.core.workflow_runs import get_workflow_run
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.report_agents import (
    REPORT_AGENT_ORDER,
    approve_report_agent_workflow,
    distribution_agent_lock_status,
    get_report_agent_workflow,
    reject_report_agent_workflow,
    report_agent_approvals_path,
    run_greenrock_report_agent_workflow,
)


class GreenRockReportAgentTests(unittest.TestCase):
    def test_report_agent_workflow_reaches_awaiting_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, root / "output")
                db_workflow = get_workflow_run(connection, workflow.workflow_id)

            workflow_json = json.loads(workflow.workflow_path.read_text(encoding="utf-8"))
            draft_exists = bool(workflow.final_draft_path and workflow.final_draft_path.exists())

        self.assertEqual(workflow.status, "awaiting_human_approval")
        self.assertEqual(workflow.approval_status, "awaiting_human_approval")
        self.assertEqual(db_workflow.status, "awaiting_human_approval")
        self.assertTrue(draft_exists)
        self.assertTrue(workflow_json["distribution_agent"]["registered"])
        self.assertFalse(workflow_json["distribution_agent"]["enabled"])

    def test_agent_dependency_ordering_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, root / "output")

        self.assertEqual(tuple(task["agent_role"] for task in workflow.tasks), REPORT_AGENT_ORDER)
        self.assertEqual(tuple(task["status"] for task in workflow.tasks), ("completed",) * len(REPORT_AGENT_ORDER))

    def test_structured_handoffs_are_persisted_with_artifact_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, root / "output")
                artifacts = list_artifacts_for_run(connection, workflow.workflow_id)

            handoff = workflow.workflow_path.parent / "handoffs" / "risk_officer.json"
            risk_payload = json.loads(handoff.read_text(encoding="utf-8"))
            handoff_exists = handoff.exists()

        self.assertTrue(handoff_exists)
        self.assertEqual(risk_payload["agent_role"], "risk_officer")
        self.assertTrue(risk_payload["input_artifact_refs"])
        self.assertTrue(risk_payload["output_artifact_refs"])
        self.assertIn("started_at", risk_payload)
        self.assertIn("completed_at", risk_payload)
        self.assertGreaterEqual(len(artifacts), len(REPORT_AGENT_ORDER) + 2)

    def test_risk_and_compliance_warnings_propagate_to_chief_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, root / "output")

            workflow_json = json.loads(workflow.workflow_path.read_text(encoding="utf-8"))
            chief = workflow_json["chief_of_staff_summary"]

        self.assertIn("No successful market scan found", "\n".join(workflow.warnings))
        self.assertIn("No Derivative Workbench analysis found", "\n".join(workflow.warnings))
        self.assertIn("missing_data", chief)
        self.assertIn("final_draft_location", chief)
        self.assertEqual(chief["approval_status"], "awaiting_human_approval")

    def test_approval_creation_is_append_only_and_blocks_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, output_dir)
                approval = approve_report_agent_workflow(
                    connection,
                    output_dir,
                    workflow.workflow_id,
                    approver="managing_director",
                    note="Reviewed.",
                )
                with self.assertRaises(ValueError):
                    approve_report_agent_workflow(connection, output_dir, workflow.workflow_id, approver="managing_director")
                db_workflow = get_workflow_run(connection, workflow.workflow_id)

            ledger_lines = report_agent_approvals_path(output_dir).read_text(encoding="utf-8").splitlines()
            updated = get_report_agent_workflow(output_dir, workflow.workflow_id)

        self.assertEqual(approval.decision, "approved")
        self.assertEqual(approval.report_id, workflow.workflow_id)
        self.assertEqual(updated.status, "approved")
        self.assertEqual(db_workflow.status, "approved")
        self.assertEqual(len(ledger_lines), 1)

    def test_rejection_creation_updates_workflow_without_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, output_dir)
                approval = reject_report_agent_workflow(connection, output_dir, workflow.workflow_id, approver="managing_director")

            lock = distribution_agent_lock_status(output_dir, workflow.workflow_id)
            updated = get_report_agent_workflow(output_dir, workflow.workflow_id)

        self.assertEqual(approval.decision, "rejected")
        self.assertEqual(updated.status, "rejected")
        self.assertEqual(lock["status"], "blocked")
        self.assertEqual(lock["reason"], "missing_explicit_approval_record")

    def test_distribution_blocks_without_approval_and_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "output"
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, output_dir)
                before = distribution_agent_lock_status(output_dir, workflow.workflow_id)
                approve_report_agent_workflow(connection, output_dir, workflow.workflow_id, approver="managing_director")
                after = distribution_agent_lock_status(output_dir, workflow.workflow_id)

        self.assertFalse(before["runnable"])
        self.assertEqual(before["reason"], "missing_explicit_approval_record")
        self.assertFalse(after["runnable"])
        self.assertEqual(after["reason"], "distribution_disabled_in_phase_11c")

    def test_failed_agent_blocks_downstream_dependent_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, root / "output", fail_agent="derivative_analyst")

        by_role = {task["agent_role"]: task for task in workflow.tasks}
        self.assertEqual(workflow.status, "failed")
        self.assertEqual(by_role["derivative_analyst"]["status"], "failed")
        self.assertEqual(by_role["risk_officer"]["status"], "blocked")
        self.assertEqual(by_role["compliance_reviewer"]["status"], "blocked")
        self.assertEqual(by_role["report_writer"]["status"], "blocked")
        self.assertIsNone(workflow.final_draft_path)

    def test_state_transitions_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")
            with connect(db_path) as connection:
                workflow = run_greenrock_report_agent_workflow(connection, root / "output")
                approve_report_agent_workflow(connection, root / "output", workflow.workflow_id, approver="managing_director")
                audit_actions = tuple(event.action for event in list_audit_logs(connection))

            updated = get_report_agent_workflow(root / "output", workflow.workflow_id)
            workflow_json = json.loads(updated.workflow_path.read_text(encoding="utf-8"))

        self.assertEqual(workflow.status, "awaiting_human_approval")
        self.assertEqual(updated.status, "approved")
        self.assertIn("awaiting_human_approval", workflow_json["states"])
        self.assertIn("approved", workflow_json["states"])
        self.assertIn("greenrock_report_agent_approved", audit_actions)

    def test_greenrock_agents_cli_commands_are_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict(os.environ, env, clear=False):
                run_output, run_code = _run_cli_raw(["greenrock", "agents", "run-report"])
                workflow_id = _value_from_cli(run_output, "workflow_id")
                status_output, status_code = _run_cli_raw(["greenrock", "agents", "status", workflow_id])
                approve_output, approve_code = _run_cli_raw(["greenrock", "agents", "approve", workflow_id, "--approver", "md"])

        self.assertEqual(run_code, 0)
        self.assertEqual(status_code, 0)
        self.assertEqual(approve_code, 0)
        self.assertIn("awaiting_human_approval", status_output)
        self.assertIn("Distribution remains disabled", approve_output)

    def test_greenrock_agents_cli_unknown_workflow_is_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict(os.environ, env, clear=False):
                output, code = _run_cli_raw(["greenrock", "agents", "status", "greenrock-missing"])

        self.assertEqual(code, 1)
        self.assertIn("Workflow not found: greenrock-missing", output)
        self.assertIn("atlas greenrock agents status", output)
        self.assertNotIn("Traceback", output)


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code


def _value_from_cli(output: str, key: str) -> str:
    prefix = f"{key}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError(f"{key} not found in CLI output:\n{output}")


if __name__ == "__main__":
    unittest.main()
