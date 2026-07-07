"""Local manual task board persistence."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection, Row


TASK_STATUSES = ("pending", "in_progress", "awaiting_review", "done")
PROJECT_STAGES = ("pitched", "evaluating", "planned", "in_progress", "testing", "active", "funding", "launch", "operating", "paused", "archived")
DEFAULT_PROJECT_NAME = "Atlas OS / General Operations"


@dataclass(frozen=True)
class ManualTask:
    id: int
    project_id: int | None
    name: str
    division: str
    status: str
    notes: str | None
    assigned_agent: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    division: str
    status: str
    created_at: str


def ensure_default_project(connection: Connection) -> Project:
    return create_project(connection, DEFAULT_PROJECT_NAME, "atlas-core", "operating")


def create_project(connection: Connection, name: str, division: str = "atlas-core", status: str = "planned") -> Project:
    normalized_status = status if status in PROJECT_STAGES else "planned"
    connection.execute(
        """
        INSERT OR IGNORE INTO projects (name, division, status)
        VALUES (?, ?, ?)
        """,
        (name.strip() or DEFAULT_PROJECT_NAME, division.strip() or "atlas-core", normalized_status),
    )
    connection.commit()
    project = get_project_by_name(connection, name.strip() or DEFAULT_PROJECT_NAME)
    if project.status != normalized_status and project.name == (name.strip() or DEFAULT_PROJECT_NAME):
        update_project_status(connection, project.id, normalized_status)
        project = get_project(connection, project.id)
    return project


def list_projects(connection: Connection) -> tuple[Project, ...]:
    ensure_default_project(connection)
    rows = connection.execute("SELECT * FROM projects ORDER BY status, name").fetchall()
    return tuple(_row_to_project(row) for row in rows)


def get_project(connection: Connection, project_id: int) -> Project:
    row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown project: {project_id}")
    return _row_to_project(row)


def get_project_by_name(connection: Connection, name: str) -> Project:
    row = connection.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown project: {name}")
    return _row_to_project(row)


def update_project_status(connection: Connection, project_id: int, status: str) -> Project:
    if status not in PROJECT_STAGES:
        raise ValueError(f"Unsupported project stage: {status}")
    connection.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
    connection.commit()
    return get_project(connection, project_id)


def create_manual_task(
    connection: Connection,
    name: str,
    division: str,
    notes: str | None = None,
    assigned_agent: str | None = None,
    project_id: int | None = None,
) -> ManualTask:
    if project_id is None:
        project_id = ensure_default_project(connection).id
    cursor = connection.execute(
        """
        INSERT INTO tasks (project_id, name, division, status, notes, assigned_agent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, name.strip(), division.strip() or "general", "pending", notes, assigned_agent),
    )
    connection.commit()
    return get_manual_task(connection, cursor.lastrowid)


def list_manual_tasks(connection: Connection) -> tuple[ManualTask, ...]:
    _backfill_task_projects(connection)
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


def move_manual_task_project(connection: Connection, task_id: int, project_id: int) -> ManualTask:
    get_project(connection, project_id)
    connection.execute(
        "UPDATE tasks SET project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (project_id, task_id),
    )
    connection.commit()
    return get_manual_task(connection, task_id)


def _backfill_task_projects(connection: Connection) -> None:
    default = ensure_default_project(connection)
    connection.execute("UPDATE tasks SET project_id = ? WHERE project_id IS NULL", (default.id,))
    connection.commit()


def _row_to_task(row: Row) -> ManualTask:
    return ManualTask(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        division=row["division"],
        status=row["status"],
        notes=row["notes"],
        assigned_agent=row["assigned_agent"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_project(row: Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        division=row["division"],
        status=row["status"],
        created_at=row["created_at"],
    )
