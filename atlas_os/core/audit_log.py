"""Audit log event persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlite3 import Connection, Row


@dataclass(frozen=True)
class AuditEvent:
    actor: str
    action: str
    detail: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int | None = None
    run_id: str | None = None
    artifact_id: int | None = None
    approval_id: int | None = None


def create_audit_log(
    connection: Connection,
    actor: str,
    action: str,
    detail: str | None = None,
    run_id: str | None = None,
    artifact_id: int | None = None,
    approval_id: int | None = None,
) -> AuditEvent:
    cursor = connection.execute(
        """
        INSERT INTO audit_logs (actor, action, detail, run_id, artifact_id, approval_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (actor, action, detail, run_id, artifact_id, approval_id),
    )
    connection.commit()
    return get_audit_log(connection, cursor.lastrowid)


def list_audit_logs(connection: Connection) -> tuple[AuditEvent, ...]:
    rows = connection.execute(
        "SELECT * FROM audit_logs ORDER BY created_at DESC, id DESC",
    ).fetchall()
    return tuple(_row_to_audit(row) for row in rows)


def get_audit_log(connection: Connection, audit_id: int) -> AuditEvent:
    row = connection.execute(
        "SELECT * FROM audit_logs WHERE id = ?",
        (audit_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown audit log: {audit_id}")
    return _row_to_audit(row)


def _row_to_audit(row: Row) -> AuditEvent:
    created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    return AuditEvent(
        id=row["id"],
        actor=row["actor"],
        action=row["action"],
        detail=row["detail"],
        run_id=row["run_id"],
        artifact_id=row["artifact_id"],
        approval_id=row["approval_id"],
        created_at=created_at,
    )
