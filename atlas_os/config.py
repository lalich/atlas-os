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


def load_env_file(path: Path = Path(".env")) -> None:
    """Load simple local .env values without overriding the shell."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_settings() -> Settings:
    """Load settings from environment variables with safe local defaults."""
    load_env_file()
    return Settings(
        env=os.getenv("ATLAS_ENV", "development"),
        db_path=Path(os.getenv("ATLAS_DB_PATH", ".atlas/atlas.db")),
        log_level=os.getenv("ATLAS_LOG_LEVEL", "INFO"),
        output_dir=Path(os.getenv("ATLAS_OUTPUT_DIR", ".atlas/output")),
    )
