"""Report status persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection, Row


@dataclass(frozen=True)
class ReportRecord:
    id: int
    title: str
    report_type: str
    status: str
    content_path: str | None
    run_id: str | None
    artifact_id: int | None
    approval_id: int | None
    created_at: str
    approved_at: str | None


def create_report_record(
    connection: Connection,
    title: str,
    report_type: str,
    content_path: str,
    run_id: str | None = None,
    artifact_id: int | None = None,
    approval_id: int | None = None,
    status: str = "blocked_for_approval",
) -> ReportRecord:
    cursor = connection.execute(
        """
        INSERT INTO reports (
            title, report_type, content_path, run_id, artifact_id, approval_id, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (title, report_type, content_path, run_id, artifact_id, approval_id, status),
    )
    connection.commit()
    return get_report(connection, cursor.lastrowid)


def update_report_status_for_approval(
    connection: Connection,
    approval_id: int,
    status: str,
) -> None:
    approved_at = _now() if status == "approved" else None
    connection.execute(
        """
        UPDATE reports
        SET status = ?, approved_at = COALESCE(?, approved_at)
        WHERE approval_id = ?
        """,
        (status, approved_at, approval_id),
    )
    connection.commit()


def get_report(connection: Connection, report_id: int) -> ReportRecord:
    row = connection.execute(
        "SELECT * FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown report: {report_id}")
    return _row_to_report(row)


def list_reports_for_run(connection: Connection, run_id: str) -> tuple[ReportRecord, ...]:
    rows = connection.execute(
        "SELECT * FROM reports WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return tuple(_row_to_report(row) for row in rows)


def _row_to_report(row: Row) -> ReportRecord:
    return ReportRecord(
        id=row["id"],
        title=row["title"],
        report_type=row["report_type"],
        status=row["status"],
        content_path=row["content_path"],
        run_id=row["run_id"],
        artifact_id=row["artifact_id"],
        approval_id=row["approval_id"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

