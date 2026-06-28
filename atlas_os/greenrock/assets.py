"""GreenRock local brand assets."""

from __future__ import annotations

from pathlib import Path


GREENROCK_LOGO_PATH = Path(__file__).resolve().parents[1] / "static" / "greenrock_logo.png"


def greenrock_logo_path() -> Path | None:
    return GREENROCK_LOGO_PATH if GREENROCK_LOGO_PATH.exists() else None
