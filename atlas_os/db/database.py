"""SQLite database initialization for Atlas OS."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def initialize_database(db_path: Path) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        _migrate_existing_database(connection)

    return db_path


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _migrate_existing_database(connection: sqlite3.Connection) -> None:
    _add_missing_columns(
        connection,
        "tasks",
        {
            "division": "ALTER TABLE tasks ADD COLUMN division TEXT NOT NULL DEFAULT 'general'",
        },
    )
    _add_missing_columns(
        connection,
        "approvals",
        {
            "run_id": "ALTER TABLE approvals ADD COLUMN run_id TEXT",
            "artifact_id": "ALTER TABLE approvals ADD COLUMN artifact_id INTEGER",
        },
    )
    _add_missing_columns(
        connection,
        "audit_logs",
        {
            "run_id": "ALTER TABLE audit_logs ADD COLUMN run_id TEXT",
            "artifact_id": "ALTER TABLE audit_logs ADD COLUMN artifact_id INTEGER",
            "approval_id": "ALTER TABLE audit_logs ADD COLUMN approval_id INTEGER",
        },
    )
    _add_missing_columns(
        connection,
        "reports",
        {
            "run_id": "ALTER TABLE reports ADD COLUMN run_id TEXT",
            "artifact_id": "ALTER TABLE reports ADD COLUMN artifact_id INTEGER",
            "approval_id": "ALTER TABLE reports ADD COLUMN approval_id INTEGER",
        },
    )


def _add_missing_columns(
    connection: sqlite3.Connection,
    table_name: str,
    migrations: dict[str, str],
) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, statement in migrations.items():
        if column_name not in columns:
            connection.execute(statement)
