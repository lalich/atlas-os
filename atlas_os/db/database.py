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
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(approvals)").fetchall()
    }
    migrations = {
        "run_id": "ALTER TABLE approvals ADD COLUMN run_id TEXT",
        "artifact_id": "ALTER TABLE approvals ADD COLUMN artifact_id INTEGER",
    }
    for column_name, statement in migrations.items():
        if column_name not in columns:
            connection.execute(statement)
