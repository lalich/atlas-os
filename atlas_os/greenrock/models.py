"""GreenRock data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PriceBar:
    date: date
    close: float
    volume: int


@dataclass(frozen=True)
class FundamentalSnapshot:
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    net_cash: float | None = None
    net_cash_per_share: float | None = None
    quick_ratio: float | None = None
    current_assets: float | None = None
    inventory: float | None = None
    current_liabilities: float | None = None
    shares_outstanding_current: float | None = None
    shares_outstanding_prior: float | None = None
    shares_outstanding_change_percent: float | None = None
    fundamental_data_source: str = ""
    fundamental_data_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MockStock:
    symbol: str
    company_name: str
    market_cap: float
    prices: tuple[PriceBar, ...]
    has_price_history: bool = True
    has_market_cap: bool = True
    has_volume_data: bool = True
    has_52_week_low: bool = True
    skipped_reason: str = ""
    fundamentals: FundamentalSnapshot | None = None


@dataclass(frozen=True)
class IndicatorSnapshot:
    latest_close: float
    latest_volume: int
    sma_10: float
    ema_8: float
    sma_50: float
    sma_150: float
    rsi_14: float
    bollinger_lower: float
    bollinger_middle: float
    bollinger_upper: float
    week_52_low: float
    low_proximity: float
    volume_avg_10: float
    previous_volume_avg_10: float
    ma_roc_50: float
    ma_roc_150: float


@dataclass(frozen=True)
class StockCandidate:
    symbol: str
    company_name: str
    market_cap_bucket: str
    market_cap: float
    score: float
    indicators: IndicatorSnapshot
    passed_rules: tuple[str, ...]
    failed_rules: tuple[str, ...]
    note: str
    has_price_history: bool = True
    has_market_cap: bool = True
    has_volume_data: bool = True
    has_52_week_low: bool = True
    skipped_reason: str = ""
    selection_label: str = "Strict Pass"
    fundamentals: FundamentalSnapshot | None = None

    @property
    def mock_score(self) -> float:
        return self.score


@dataclass(frozen=True)
class ScreeningResult:
    selected: tuple[StockCandidate, ...]
    all_candidates: tuple[StockCandidate, ...] = ()
    mega_rock: tuple[StockCandidate, ...] = ()
    large_cap: tuple[StockCandidate, ...] = ()
    small_cap: tuple[StockCandidate, ...] = ()
    data_mode: str = "mock"
    data_source: str = "mock_sample_data"
    selection_mode: str = "strict"
    data_quality_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class GreenRockReport:
    title: str
    markdown: str
    source: str = "mock_sample_data"
