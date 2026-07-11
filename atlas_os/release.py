"""Release metadata helpers for Atlas OS."""

from __future__ import annotations

from pathlib import Path


RELEASE_NAME = "Agent Orchestration"
NEXT_RELEASE = "v0.9 — Publishing and Distribution Foundations"
TARGET_RELEASE = "v1.0 — GreenRock Operating System"


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def version_file_path() -> Path:
    return repository_root() / "VERSION"


def load_version() -> str:
    return version_file_path().read_text(encoding="utf-8").strip()


def version_lines() -> tuple[str, str]:
    return (f"Atlas OS v{load_version()}", RELEASE_NAME)


def roadmap_lines() -> tuple[str, ...]:
    return (
        "Atlas OS Roadmap",
        "",
        "Current Release",
        f"v{load_version()} — {RELEASE_NAME}",
        "",
        "Next Release",
        NEXT_RELEASE,
        "",
        "Target",
        TARGET_RELEASE,
    )
