"""GreenRock sample data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockCandidate:
    symbol: str
    company_name: str
    market_cap_bucket: str
    mock_score: float
    note: str


@dataclass(frozen=True)
class ScreeningResult:
    selected: tuple[StockCandidate, ...]


@dataclass(frozen=True)
class GreenRockReport:
    title: str
    markdown: str
    source: str = "mock_sample_data"

