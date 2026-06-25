"""Preview-only GreenRock Score calculation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from atlas_os.greenrock.criteria import evaluate_stock, passes_core_criteria
from atlas_os.greenrock.market_data import (
    MarketDataConfigurationError,
    MarketDataProvider,
    MockMarketDataProvider,
    YFinanceMarketDataProvider,
)
from atlas_os.greenrock.models import StockCandidate
from atlas_os.greenrock.scoring import greenrock_score_breakdown, signal_label


@dataclass(frozen=True)
class ScorePreview:
    candidate: StockCandidate
    data_mode: str
    data_source: str
    selection_mode: str
    component_scores: dict[str, float]
    data_quality_warnings: tuple[str, ...]


def calculate_score_preview(
    ticker: str,
    data_mode: str = "mock",
    selection_mode: str | None = None,
    output_dir: Path | None = None,
    provider: MarketDataProvider | None = None,
) -> ScorePreview:
    """Calculate a single-ticker score without creating workflow records or artifacts."""
    symbol = normalize_ticker(ticker)
    if not symbol:
        raise ValueError("Ticker is required.")

    market_data_provider = provider or _provider_for_score(symbol, data_mode, output_dir)
    resolved_selection_mode = selection_mode if selection_mode in {"strict", "ranked"} else _default_selection_mode(market_data_provider.data_mode)
    stocks = market_data_provider.fetch_stocks()
    stock = next((item for item in stocks if item.symbol.upper() == symbol), None)
    if stock is None:
        raise ValueError(f"Ticker {symbol} was not found in {market_data_provider.source_name}.")

    candidate = evaluate_stock(stock)
    if passes_core_criteria(candidate):
        selection_label = "Strict Pass"
    elif resolved_selection_mode == "ranked" and candidate.score >= 55:
        selection_label = "Ranked Candidate"
    else:
        selection_label = "Watchlist"
    candidate = StockCandidate(
        symbol=candidate.symbol,
        company_name=candidate.company_name,
        market_cap_bucket=candidate.market_cap_bucket,
        market_cap=candidate.market_cap,
        score=candidate.score,
        indicators=candidate.indicators,
        passed_rules=candidate.passed_rules,
        failed_rules=candidate.failed_rules,
        note=candidate.note,
        has_price_history=candidate.has_price_history,
        has_market_cap=candidate.has_market_cap,
        has_volume_data=candidate.has_volume_data,
        has_52_week_low=candidate.has_52_week_low,
        skipped_reason=candidate.skipped_reason,
        selection_label=selection_label,
    )
    return ScorePreview(
        candidate=candidate,
        data_mode=market_data_provider.data_mode,
        data_source=market_data_provider.source_name,
        selection_mode=resolved_selection_mode,
        component_scores=greenrock_score_breakdown(candidate.indicators, _rule_results(candidate)),
        data_quality_warnings=_data_quality_warnings(candidate),
    )


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _provider_for_score(symbol: str, data_mode: str, output_dir: Path | None) -> MarketDataProvider:
    normalized_mode = data_mode.strip().lower()
    if normalized_mode == "mock":
        return MockMarketDataProvider()
    if normalized_mode != "real":
        raise MarketDataConfigurationError("Data mode must be 'mock' or 'real'.")
    provider_name = os.getenv("ATLAS_MARKET_DATA_PROVIDER", "").strip().lower()
    if provider_name != "yfinance":
        raise MarketDataConfigurationError(
            "Real score preview requires ATLAS_MARKET_DATA_PROVIDER=yfinance. "
            "Use --data mock or configure the real provider locally."
        )
    return YFinanceMarketDataProvider((symbol,), universe_name="score_preview")


def _default_selection_mode(data_mode: str) -> str:
    return "ranked" if data_mode == "real" else "strict"


def _rule_results(candidate: StockCandidate) -> dict[str, bool]:
    return {rule: True for rule in candidate.passed_rules} | {rule: False for rule in candidate.failed_rules}


def _data_quality_warnings(candidate: StockCandidate) -> tuple[str, ...]:
    warnings = []
    if not candidate.has_price_history:
        warnings.append("Missing usable price history.")
    if not candidate.has_market_cap:
        warnings.append("Missing market cap.")
    if not candidate.has_volume_data:
        warnings.append("Missing volume data.")
    if not candidate.has_52_week_low:
        warnings.append("Missing 52-week low.")
    if candidate.skipped_reason:
        warnings.append(candidate.skipped_reason.replace("_", " "))
    if candidate.failed_rules:
        warnings.append(f"Strict criteria failed: {len(candidate.failed_rules)} rule(s).")
    return tuple(dict.fromkeys(warnings))


def score_signal(candidate: StockCandidate) -> str:
    return signal_label(candidate.score)
