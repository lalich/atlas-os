"""Mock GreenRock screener for Phase 0."""

from __future__ import annotations

from atlas_os.greenrock.models import ScreeningResult
from atlas_os.greenrock.sample_data import SAMPLE_CANDIDATES


def run_sample_screen() -> ScreeningResult:
    selected = tuple(sorted(SAMPLE_CANDIDATES, key=lambda item: item.mock_score, reverse=True))
    return ScreeningResult(selected=selected)

