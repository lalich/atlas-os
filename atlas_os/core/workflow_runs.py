"""Persistence helpers for local workflow runs."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection, Row


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    division: str
    workflow_name: str
    status: str
    started_at: str
    completed_at: str | None
    output_paths: dict[str, str]
    mock_data_used: bool


def create_workflow_run(
    connection: Connection,
    division: str,
    workflow_name: str,
    mock_data_used: bool = True,
) -> WorkflowRun:
    run_id = f"{division}-{uuid.uuid4().hex[:12]}"
    started_at = _now()
    connection.execute(
        """
        INSERT INTO workflow_runs (
            run_id, division, workflow_name, status, started_at, output_paths, mock_data_used
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, division, workflow_name, "running", started_at, "{}", int(mock_data_used)),
    )
    connection.commit()
    return WorkflowRun(
        run_id=run_id,
        division=division,
        workflow_name=workflow_name,
        status="running",
        started_at=started_at,
        completed_at=None,
        output_paths={},
        mock_data_used=mock_data_used,
    )


def complete_workflow_run(
    connection: Connection,
    run_id: str,
    output_paths: dict[str, Path],
    status: str = "awaiting_approval",
) -> WorkflowRun:
    completed_at = _now()
    serializable_paths = {key: str(path) for key, path in output_paths.items()}
    connection.execute(
        """
        UPDATE workflow_runs
        SET status = ?, completed_at = ?, output_paths = ?
        WHERE run_id = ?
        """,
        (status, completed_at, json.dumps(serializable_paths, sort_keys=True), run_id),
    )
    connection.commit()
    return get_workflow_run(connection, run_id)


def get_workflow_run(connection: Connection, run_id: str) -> WorkflowRun:
    row = connection.execute(
        "SELECT * FROM workflow_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown workflow run: {run_id}")
    return _row_to_run(row)


def list_workflow_runs(connection: Connection) -> tuple[WorkflowRun, ...]:
    rows = connection.execute(
        "SELECT * FROM workflow_runs ORDER BY started_at DESC, id DESC",
    ).fetchall()
    return tuple(_row_to_run(row) for row in rows)


def _row_to_run(row: Row) -> WorkflowRun:
    return WorkflowRun(
        run_id=row["run_id"],
        division=row["division"],
        workflow_name=row["workflow_name"],
        status=row["status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        output_paths=json.loads(row["output_paths"] or "{}"),
        mock_data_used=bool(row["mock_data_used"]),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

