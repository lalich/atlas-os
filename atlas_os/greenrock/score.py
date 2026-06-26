"""Preview-only GreenRock Score calculation."""

from __future__ import annotations

import os
import statistics
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
class ScoreComponentExplanation:
    name: str
    key: str
    raw_metric: str
    component_score: float
    weight: int
    explanation: str


@dataclass(frozen=True)
class PriceTarget:
    label: str
    price: float | None
    relation_to_ath: str


@dataclass(frozen=True)
class ScorePreview:
    candidate: StockCandidate
    data_mode: str
    data_source: str
    selection_mode: str
    component_scores: dict[str, float]
    component_explanations: tuple[ScoreComponentExplanation, ...]
    bonus_penalty_explanations: tuple[str, ...]
    all_time_high: float | None
    price_targets: tuple[PriceTarget, ...]
    price_target_warnings: tuple[str, ...]
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
    component_scores = greenrock_score_breakdown(candidate.indicators, _rule_results(candidate))
    all_time_high, price_targets, price_target_warnings = _price_targets(stock.prices, candidate.has_price_history)
    bonus_penalty_explanations = _bonus_penalty_explanations(candidate)
    return ScorePreview(
        candidate=candidate,
        data_mode=market_data_provider.data_mode,
        data_source=market_data_provider.source_name,
        selection_mode=resolved_selection_mode,
        component_scores=component_scores,
        component_explanations=_component_explanations(candidate, component_scores, bonus_penalty_explanations),
        bonus_penalty_explanations=bonus_penalty_explanations,
        all_time_high=all_time_high,
        price_targets=price_targets,
        price_target_warnings=price_target_warnings,
        data_quality_warnings=_data_quality_warnings(candidate) + price_target_warnings,
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


def _component_explanations(
    candidate: StockCandidate,
    component_scores: dict[str, float],
    bonus_penalty_explanations: tuple[str, ...],
) -> tuple[ScoreComponentExplanation, ...]:
    indicators = candidate.indicators
    return (
        ScoreComponentExplanation(
            name="52-week low proximity",
            key="52_week_low_proximity",
            raw_metric=f"{indicators.low_proximity:.1%} above 52-week low (${indicators.week_52_low:,.2f})",
            component_score=component_scores.get("52_week_low_proximity", 0.0),
            weight=20,
            explanation="Scores higher when the current price is close to the 52-week low, highlighting technical dislocation.",
        ),
        ScoreComponentExplanation(
            name="Bollinger Band setup",
            key="bollinger_band_setup",
            raw_metric=(
                f"price ${indicators.latest_close:,.2f}; lower band ${indicators.bollinger_lower:,.2f}; "
                f"upper band ${indicators.bollinger_upper:,.2f}"
            ),
            component_score=component_scores.get("bollinger_band_setup", 0.0),
            weight=20,
            explanation="Scores higher when price is nearer the lower 2.5 standard deviation Bollinger Band.",
        ),
        ScoreComponentExplanation(
            name="RSI",
            key="rsi",
            raw_metric=f"14-day RSI {indicators.rsi_14:.2f}",
            component_score=component_scores.get("rsi", 0.0),
            weight=15,
            explanation="Scores higher when RSI is below the neutral 50 level, signaling weaker momentum.",
        ),
        ScoreComponentExplanation(
            name="Volume acceleration",
            key="volume_acceleration",
            raw_metric=f"10-day average volume change {_volume_acceleration(candidate)}",
            component_score=component_scores.get("volume_acceleration", 0.0),
            weight=15,
            explanation="Scores higher when recent 10-day average volume is rising versus the prior 10-day period.",
        ),
        ScoreComponentExplanation(
            name="Moving average structure",
            key="moving_average_structure",
            raw_metric=_moving_average_structure(candidate),
            component_score=component_scores.get("moving_average_structure", 0.0),
            weight=20,
            explanation="Scores dislocated trend structure and early repair across 8/10 and 50/150 moving-average tests.",
        ),
        ScoreComponentExplanation(
            name="Bonus / penalty factors",
            key="bonus_penalty_factors",
            raw_metric="; ".join(bonus_penalty_explanations),
            component_score=component_scores.get("bonus_penalty_factors", 0.0),
            weight=10,
            explanation="Shows explicit score adjustments for unusually strong setups or data-quality and criteria risks.",
        ),
    )


def _bonus_penalty_explanations(candidate: StockCandidate) -> tuple[str, ...]:
    indicators = candidate.indicators
    explanations: list[str] = []
    volume_acceleration = _volume_acceleration_value(candidate)
    if indicators.latest_close < indicators.bollinger_lower:
        explanations.append("Bonus: price below lower 2.5 standard deviation Bollinger Band.")
    if volume_acceleration is not None and volume_acceleration >= 0.25:
        explanations.append("Bonus: strong volume acceleration versus the prior 10-day average.")
    if indicators.low_proximity <= 0.02:
        explanations.append("Bonus: unusually deep dislocation near the 52-week low.")
    if not candidate.has_price_history:
        explanations.append("Penalty risk: missing or insufficient price history.")
    if not candidate.has_volume_data or indicators.latest_volume <= 0 or indicators.volume_avg_10 <= 0:
        explanations.append("Penalty risk: missing volume data or extreme illiquidity.")
    if not candidate.has_market_cap or candidate.market_cap <= 0:
        explanations.append("Penalty risk: weak market-cap data.")
    if not {"ema8_below_sma10", "dma50_below_dma150", "dma50_roc_improving_vs_dma150"}.issubset(candidate.passed_rules):
        explanations.append("Penalty risk: moving average structure is not fully aligned with GreenRock criteria.")
    if candidate.failed_rules:
        explanations.append(f"Penalty risk: {len(candidate.failed_rules)} strict GreenRock rule(s) failed.")
    if not explanations:
        explanations.append("No active bonus or penalty factor beyond base component scoring.")
    return tuple(explanations)


def _price_targets(prices, has_price_history: bool) -> tuple[float | None, tuple[PriceTarget, ...], tuple[str, ...]]:
    closes = [price.close for price in prices if price.close > 0] if has_price_history else []
    labels = ("+2 SD", "+3 SD", "+5 SD", "+7 SD")
    empty_targets = tuple(PriceTarget(label, None, "unavailable") for label in labels)
    if len(closes) < 2:
        return None, empty_targets, ("Standard deviation price targets unavailable: insufficient price history.",)

    current_price = closes[-1]
    all_time_high = max(closes)
    deviation = statistics.pstdev(closes)
    if deviation <= 0:
        return all_time_high, empty_targets, ("Standard deviation price targets unavailable: price history has no usable variation.",)

    targets = tuple(
        PriceTarget(
            label=label,
            price=round(current_price + multiple * deviation, 2),
            relation_to_ath="above-ath" if current_price + multiple * deviation >= all_time_high else "below-ath",
        )
        for label, multiple in (("+2 SD", 2), ("+3 SD", 3), ("+5 SD", 5), ("+7 SD", 7))
    )
    return round(all_time_high, 2), targets, ()


def _volume_acceleration(candidate: StockCandidate) -> str:
    value = _volume_acceleration_value(candidate)
    if value is None:
        return "unavailable"
    return f"{value:.1%}"


def _volume_acceleration_value(candidate: StockCandidate) -> float | None:
    indicators = candidate.indicators
    if indicators.previous_volume_avg_10 <= 0:
        return None
    return (indicators.volume_avg_10 - indicators.previous_volume_avg_10) / indicators.previous_volume_avg_10


def _moving_average_structure(candidate: StockCandidate) -> str:
    return (
        f"8 EMA {'below' if 'ema8_below_sma10' in candidate.passed_rules else 'not below'} 10 SMA; "
        f"50 DMA {'below' if 'dma50_below_dma150' in candidate.passed_rules else 'not below'} 150 DMA; "
        f"50 DMA ROC {'improving' if 'dma50_roc_improving_vs_dma150' in candidate.passed_rules else 'not improving'} vs 150 DMA"
    )


def score_signal(candidate: StockCandidate) -> str:
    return signal_label(candidate.score)
