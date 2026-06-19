"""Tests for approval workflow persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_os.core.approvals import (
    ApprovalStatus,
    approve_approval,
    get_approval,
    list_approvals,
    reject_approval,
)
from atlas_os.core.workflow_runs import get_workflow_run
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow


class ApprovalWorkflowTests(unittest.TestCase):
    def test_report_draft_creates_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                _, _, approval = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )
                approvals = list_approvals(connection)

            self.assertIsNotNone(approval.id)
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0].status, ApprovalStatus.PENDING)
            self.assertEqual(approvals[0].artifact_type, "report_draft_md")

    def test_approval_marks_linked_run_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                workflow_run, _, approval = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )
                approved = approve_approval(connection, approval.id)
                stored_run = get_workflow_run(connection, workflow_run.run_id)

            self.assertEqual(approved.status, ApprovalStatus.APPROVED)
            self.assertEqual(stored_run.status, "approved")
            self.assertIsNotNone(approved.decided_at)

    def test_rejection_marks_linked_run_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                workflow_run, _, approval = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )
                rejected = reject_approval(connection, approval.id)
                stored_run = get_workflow_run(connection, workflow_run.run_id)

            self.assertEqual(rejected.status, ApprovalStatus.REJECTED)
            self.assertEqual(stored_run.status, "rejected")

    def test_decided_approval_cannot_be_decided_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = initialize_database(root / "atlas.db")

            with connect(db_path) as connection:
                _, _, approval = run_greenrock_screening_workflow(
                    connection,
                    root / "output",
                    include_report_draft=True,
                )
                approve_approval(connection, approval.id)

                with self.assertRaises(ValueError):
                    reject_approval(connection, approval.id)

                stored = get_approval(connection, approval.id)

            self.assertEqual(stored.status, ApprovalStatus.APPROVED)


if __name__ == "__main__":
    unittest.main()

