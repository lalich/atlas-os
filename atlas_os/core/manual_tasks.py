"""Local manual task board persistence."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection, Row


TASK_STATUSES = ("pending", "in_progress", "awaiting_review", "done")


@dataclass(frozen=True)
class ManualTask:
    id: int
    name: str
    division: str
    status: str
    notes: str | None
    assigned_agent: str | None
    created_at: str
    updated_at: str


def create_manual_task(
    connection: Connection,
    name: str,
    division: str,
    notes: str | None = None,
    assigned_agent: str | None = None,
) -> ManualTask:
    cursor = connection.execute(
        """
        INSERT INTO tasks (name, division, status, notes, assigned_agent)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name.strip(), division.strip() or "general", "pending", notes, assigned_agent),
    )
    connection.commit()
    return get_manual_task(connection, cursor.lastrowid)


def list_manual_tasks(connection: Connection) -> tuple[ManualTask, ...]:
    rows = connection.execute(
        "SELECT * FROM tasks ORDER BY updated_at DESC, id DESC",
    ).fetchall()
    return tuple(_row_to_task(row) for row in rows)


def get_manual_task(connection: Connection, task_id: int) -> ManualTask:
    row = connection.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown task: {task_id}")
    return _row_to_task(row)


def update_manual_task_status(
    connection: Connection,
    task_id: int,
    status: str,
) -> ManualTask:
    if status not in TASK_STATUSES:
        raise ValueError(f"Unsupported task status: {status}")
    connection.execute(
        """
        UPDATE tasks
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, task_id),
    )
    connection.commit()
    return get_manual_task(connection, task_id)


def _row_to_task(row: Row) -> ManualTask:
    return ManualTask(
        id=row["id"],
        name=row["name"],
        division=row["division"],
        status=row["status"],
        notes=row["notes"],
        assigned_agent=row["assigned_agent"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
