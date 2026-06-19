"""Artifact persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection, Row


@dataclass(frozen=True)
class Artifact:
    id: int
    run_id: str
    artifact_type: str
    path: str
    created_at: str


def create_artifact(
    connection: Connection,
    run_id: str,
    artifact_type: str,
    path: Path,
) -> Artifact:
    cursor = connection.execute(
        """
        INSERT INTO artifacts (run_id, artifact_type, path)
        VALUES (?, ?, ?)
        """,
        (run_id, artifact_type, str(path)),
    )
    connection.commit()
    return get_artifact(connection, cursor.lastrowid)


def get_artifact(connection: Connection, artifact_id: int) -> Artifact:
    row = connection.execute(
        "SELECT * FROM artifacts WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown artifact: {artifact_id}")
    return _row_to_artifact(row)


def list_artifacts_for_run(connection: Connection, run_id: str) -> tuple[Artifact, ...]:
    rows = connection.execute(
        "SELECT * FROM artifacts WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return tuple(_row_to_artifact(row) for row in rows)


def _row_to_artifact(row: Row) -> Artifact:
    return Artifact(
        id=row["id"],
        run_id=row["run_id"],
        artifact_type=row["artifact_type"],
        path=row["path"],
        created_at=row["created_at"],
    )
