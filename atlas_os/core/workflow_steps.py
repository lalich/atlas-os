"""Workflow step state persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from sqlite3 import Connection, Row


class StepStatus(StrEnum):
    INITIALIZED = "initialized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED_FOR_APPROVAL = "blocked_for_approval"


@dataclass(frozen=True)
class WorkflowStepRecord:
    id: int
    run_id: str
    step_name: str
    status: StepStatus
    started_at: str | None
    completed_at: str | None
    error: str | None


def create_workflow_step(
    connection: Connection,
    run_id: str,
    step_name: str,
) -> WorkflowStepRecord:
    cursor = connection.execute(
        """
        INSERT INTO workflow_steps (run_id, step_name, status)
        VALUES (?, ?, ?)
        """,
        (run_id, step_name, StepStatus.INITIALIZED.value),
    )
    connection.commit()
    return get_workflow_step(connection, cursor.lastrowid)


def update_workflow_step(
    connection: Connection,
    step_id: int,
    status: StepStatus,
    error: str | None = None,
) -> WorkflowStepRecord:
    started_at = _now() if status == StepStatus.RUNNING else None
    completed_at = _now() if status in {
        StepStatus.COMPLETED,
        StepStatus.FAILED,
        StepStatus.BLOCKED_FOR_APPROVAL,
    } else None
    connection.execute(
        """
        UPDATE workflow_steps
        SET status = ?,
            started_at = COALESCE(?, started_at),
            completed_at = COALESCE(?, completed_at),
            error = COALESCE(?, error)
        WHERE id = ?
        """,
        (status.value, started_at, completed_at, error, step_id),
    )
    connection.commit()
    return get_workflow_step(connection, step_id)


def get_workflow_step(connection: Connection, step_id: int) -> WorkflowStepRecord:
    row = connection.execute(
        "SELECT * FROM workflow_steps WHERE id = ?",
        (step_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown workflow step: {step_id}")
    return _row_to_step(row)


def list_workflow_steps(connection: Connection, run_id: str) -> tuple[WorkflowStepRecord, ...]:
    rows = connection.execute(
        "SELECT * FROM workflow_steps WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return tuple(_row_to_step(row) for row in rows)


def _row_to_step(row: Row) -> WorkflowStepRecord:
    return WorkflowStepRecord(
        id=row["id"],
        run_id=row["run_id"],
        step_name=row["step_name"],
        status=StepStatus(row["status"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        error=row["error"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

