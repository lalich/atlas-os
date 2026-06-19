"""Human approval records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from sqlite3 import Connection, Row

from atlas_os.core.audit_log import create_audit_log
from atlas_os.core.reports import update_report_status_for_approval


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ApprovalRequest:
    artifact_type: str
    artifact_path: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    id: int | None = None
    run_id: str | None = None
    artifact_id: int | None = None
    requested_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    notes: str | None = None


def require_human_approval(artifact_type: str, artifact_path: str | None = None) -> ApprovalRequest:
    return ApprovalRequest(artifact_type=artifact_type, artifact_path=artifact_path)


def create_approval_request(
    connection: Connection,
    artifact_type: str,
    artifact_path: str,
    run_id: str | None = None,
    artifact_id: int | None = None,
    notes: str | None = None,
) -> ApprovalRequest:
    cursor = connection.execute(
        """
        INSERT INTO approvals (
            run_id, artifact_id, artifact_type, artifact_path, status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, artifact_id, artifact_type, artifact_path, ApprovalStatus.PENDING.value, notes),
    )
    connection.commit()
    approval = get_approval(connection, cursor.lastrowid)
    create_audit_log(
        connection,
        actor="approval_queue",
        action="approval_created",
        detail=f"{artifact_type}: {artifact_path}",
        run_id=run_id,
        artifact_id=artifact_id,
        approval_id=approval.id,
    )
    return approval


def list_approvals(connection: Connection) -> tuple[ApprovalRequest, ...]:
    rows = connection.execute(
        "SELECT * FROM approvals ORDER BY requested_at DESC, id DESC",
    ).fetchall()
    return tuple(_row_to_approval(row) for row in rows)


def get_approval(connection: Connection, approval_id: int) -> ApprovalRequest:
    row = connection.execute(
        "SELECT * FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown approval: {approval_id}")
    return _row_to_approval(row)


def approve_approval(
    connection: Connection,
    approval_id: int,
    decided_by: str = "local_user",
    notes: str | None = None,
) -> ApprovalRequest:
    return _decide_approval(
        connection,
        approval_id,
        status=ApprovalStatus.APPROVED,
        decided_by=decided_by,
        notes=notes,
    )


def reject_approval(
    connection: Connection,
    approval_id: int,
    decided_by: str = "local_user",
    notes: str | None = None,
) -> ApprovalRequest:
    return _decide_approval(
        connection,
        approval_id,
        status=ApprovalStatus.REJECTED,
        decided_by=decided_by,
        notes=notes,
    )


def _decide_approval(
    connection: Connection,
    approval_id: int,
    status: ApprovalStatus,
    decided_by: str,
    notes: str | None,
) -> ApprovalRequest:
    approval = get_approval(connection, approval_id)
    if approval.status != ApprovalStatus.PENDING:
        raise ValueError(f"Approval {approval_id} is already {approval.status.value}")

    connection.execute(
        """
        UPDATE approvals
        SET status = ?, decided_at = ?, decided_by = ?, notes = COALESCE(?, notes)
        WHERE id = ?
        """,
        (status.value, _now(), decided_by, notes, approval_id),
    )
    if approval.run_id:
        run_status = "approved" if status == ApprovalStatus.APPROVED else "rejected"
        connection.execute(
            "UPDATE workflow_runs SET status = ? WHERE run_id = ?",
            (run_status, approval.run_id),
        )
    connection.commit()
    update_report_status_for_approval(connection, approval_id, status.value)
    decided = get_approval(connection, approval_id)
    create_audit_log(
        connection,
        actor="approval_queue",
        action=f"approval_{status.value}",
        detail=f"Approval {approval_id} {status.value}",
        run_id=approval.run_id,
        artifact_id=approval.artifact_id,
        approval_id=approval_id,
    )
    return decided


def _row_to_approval(row: Row) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        run_id=row["run_id"],
        artifact_id=row["artifact_id"],
        artifact_type=row["artifact_type"],
        artifact_path=row["artifact_path"],
        status=ApprovalStatus(row["status"]),
        requested_at=row["requested_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        notes=row["notes"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
