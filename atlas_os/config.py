"""Configuration helpers for local Atlas OS development."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    env: str
    db_path: Path
    log_level: str
    output_dir: Path


def get_settings() -> Settings:
    """Load settings from environment variables with safe local defaults."""
    return Settings(
        env=os.getenv("ATLAS_ENV", "development"),
        db_path=Path(os.getenv("ATLAS_DB_PATH", ".atlas/atlas.db")),
        log_level=os.getenv("ATLAS_LOG_LEVEL", "INFO"),
        output_dir=Path(os.getenv("ATLAS_OUTPUT_DIR", ".atlas/output")),
    )

