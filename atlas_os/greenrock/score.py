"""Preview-only GreenRock Score calculation."""

from __future__ import annotations

import os
import math
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
from atlas_os.greenrock.models import FundamentalSnapshot, StockCandidate
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
class ConfidenceAnalysis:
    score: float
    band: str
    drivers: tuple[str, ...]
    drags: tuple[str, ...]


@dataclass(frozen=True)
class FundamentalGuardrailAnalysis:
    label: str
    net_cash: float | None
    net_cash_per_share: float | None
    quick_ratio: float | None
    shares_outstanding_change_percent: float | None
    confidence_impact: float
    score_adjustment: float
    bullish_evidence: tuple[str, ...]
    bearish_evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    data_source: str


@dataclass(frozen=True)
class EvidenceItem:
    name: str
    category: str
    direction: str
    strength: str
    numeric_contribution: float
    explanation: str


@dataclass(frozen=True)
class ScorePreview:
    candidate: StockCandidate
    data_mode: str
    data_source: str
    selection_mode: str
    confidence_score: float
    confidence_band: str
    confidence_drivers: tuple[str, ...]
    confidence_drags: tuple[str, ...]
    research_priority: str
    analyst_summary: str
    bullish_evidence: tuple[str, ...]
    bearish_evidence: tuple[str, ...]
    neutral_evidence: tuple[str, ...]
    evidence_items: tuple[EvidenceItem, ...]
    evidence_agreement_score: float
    score_confidence_divergence: str
    fundamental_guardrails: FundamentalGuardrailAnalysis
    fundamental_guardrail_adjustment: float
    watch_next: tuple[str, ...]
    component_scores: dict[str, float]
    component_explanations: tuple[ScoreComponentExplanation, ...]
    bonus_penalty_explanations: tuple[str, ...]
    all_time_high: float | None
    price_targets: tuple[PriceTarget, ...]
    price_target_warnings: tuple[str, ...]
    price_target_lookback: str
    price_target_horizon: str
    data_quality_warnings: tuple[str, ...]


def calculate_score_preview(
    ticker: str,
    data_mode: str = "real",
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
    fundamental_guardrails = _fundamental_guardrails(candidate)
    all_time_high, price_targets, price_target_warnings, price_target_lookback = _price_targets(stock.prices, candidate.has_price_history)
    base_evidence_items = build_evidence_items(
        candidate,
        price_targets,
        _data_quality_warnings(candidate) + price_target_warnings + fundamental_guardrails.warnings,
        fundamental_guardrails,
    )
    evidence_agreement_score = evidence_agreement_score_from_items(base_evidence_items)
    evidence_score_adjustment = _evidence_score_adjustment(base_evidence_items, evidence_agreement_score)
    adjusted_score = round(
        max(
            0.0,
            min(100.0, candidate.score + fundamental_guardrails.score_adjustment + evidence_score_adjustment),
        ),
        2,
    )
    candidate = StockCandidate(
        symbol=candidate.symbol,
        company_name=candidate.company_name,
        market_cap_bucket=candidate.market_cap_bucket,
        market_cap=candidate.market_cap,
        score=adjusted_score,
        indicators=candidate.indicators,
        passed_rules=candidate.passed_rules,
        failed_rules=candidate.failed_rules,
        note=candidate.note,
        has_price_history=candidate.has_price_history,
        has_market_cap=candidate.has_market_cap,
        has_volume_data=candidate.has_volume_data,
        has_52_week_low=candidate.has_52_week_low,
        skipped_reason=candidate.skipped_reason,
        selection_label=candidate.selection_label,
        fundamentals=candidate.fundamentals,
    )
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
        fundamentals=candidate.fundamentals,
    )
    component_scores = greenrock_score_breakdown(candidate.indicators, _rule_results(candidate))
    bonus_penalty_explanations = _bonus_penalty_explanations(candidate)
    data_quality_warnings = _data_quality_warnings(candidate) + price_target_warnings + fundamental_guardrails.warnings
    evidence_items = build_evidence_items(candidate, price_targets, data_quality_warnings, fundamental_guardrails)
    evidence_agreement_score = evidence_agreement_score_from_items(evidence_items)
    confidence = _confidence_analysis(
        candidate,
        stock.prices,
        all_time_high,
        price_target_lookback,
        price_target_warnings,
        price_targets,
        fundamental_guardrails,
        evidence_agreement_score,
    )
    research_priority = _research_priority(candidate, confidence.score)
    bullish_evidence = _evidence_lines(evidence_items, "bullish")
    bearish_evidence = _evidence_lines(evidence_items, "bearish")
    neutral_evidence = _evidence_lines(evidence_items, "neutral")
    divergence = _score_confidence_divergence(candidate.score, confidence.score, evidence_items, fundamental_guardrails)
    watch_next = _watch_next(candidate, all_time_high, price_targets)
    analyst_summary = _analyst_summary(
        candidate,
        confidence.score,
        research_priority,
        bullish_evidence,
        bearish_evidence,
        fundamental_guardrails,
        divergence,
    )
    return ScorePreview(
        candidate=candidate,
        data_mode=market_data_provider.data_mode,
        data_source=market_data_provider.source_name,
        selection_mode=resolved_selection_mode,
        confidence_score=confidence.score,
        confidence_band=confidence.band,
        confidence_drivers=confidence.drivers,
        confidence_drags=confidence.drags,
        research_priority=research_priority,
        analyst_summary=analyst_summary,
        bullish_evidence=bullish_evidence,
        bearish_evidence=bearish_evidence,
        neutral_evidence=neutral_evidence,
        evidence_items=evidence_items,
        evidence_agreement_score=evidence_agreement_score,
        score_confidence_divergence=divergence,
        fundamental_guardrails=fundamental_guardrails,
        fundamental_guardrail_adjustment=fundamental_guardrails.score_adjustment,
        watch_next=watch_next,
        component_scores=component_scores,
        component_explanations=_component_explanations(candidate, component_scores, bonus_penalty_explanations),
        bonus_penalty_explanations=bonus_penalty_explanations,
        all_time_high=all_time_high,
        price_targets=price_targets,
        price_target_warnings=price_target_warnings,
        price_target_lookback=price_target_lookback,
        price_target_horizon="1 year",
        data_quality_warnings=data_quality_warnings,
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
            "Configure real data locally with: export ATLAS_MARKET_DATA_PROVIDER=yfinance; "
            "python3 -m pip install -e \".[market-data]\""
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
            name="Bullish / Bearish Evidence",
            key="bonus_penalty_factors",
            raw_metric="; ".join(bonus_penalty_explanations),
            component_score=component_scores.get("bonus_penalty_factors", 0.0),
            weight=10,
            explanation="Shows explicit evidence that supports or cautions against the current research setup.",
        ),
    )


def _bonus_penalty_explanations(candidate: StockCandidate) -> tuple[str, ...]:
    indicators = candidate.indicators
    explanations: list[str] = []
    volume_acceleration = _volume_acceleration_value(candidate)
    if indicators.latest_close < indicators.bollinger_lower:
        explanations.append("Bullish evidence: price below lower 2.5 standard deviation Bollinger Band.")
    if volume_acceleration is not None and volume_acceleration >= 0.25:
        explanations.append("Bullish evidence: strong volume acceleration versus the prior 10-day average.")
    if indicators.low_proximity <= 0.02:
        explanations.append("Bullish evidence: unusually deep dislocation near the 52-week low.")
    if not candidate.has_price_history:
        explanations.append("Bearish evidence: missing or insufficient price history.")
    if not candidate.has_volume_data or indicators.latest_volume <= 0 or indicators.volume_avg_10 <= 0:
        explanations.append("Bearish evidence: missing volume data or extreme illiquidity.")
    if not candidate.has_market_cap or candidate.market_cap <= 0:
        explanations.append("Bearish evidence: weak market-cap data.")
    if not {"ema8_below_sma10", "dma50_below_dma150", "dma50_roc_improving_vs_dma150"}.issubset(candidate.passed_rules):
        explanations.append("Bearish evidence: moving average structure is not fully aligned with GreenRock criteria.")
    if candidate.failed_rules:
        explanations.append(f"Bearish evidence: {len(candidate.failed_rules)} strict GreenRock rule(s) failed.")
    if not explanations:
        explanations.append("No major Bullish or Bearish Evidence item beyond base component scoring.")
    return tuple(explanations)


def build_evidence_items(
    candidate: StockCandidate,
    price_targets: tuple[PriceTarget, ...] = (),
    data_quality_warnings: tuple[str, ...] = (),
    fundamental_guardrails: FundamentalGuardrailAnalysis | None = None,
) -> tuple[EvidenceItem, ...]:
    """Build structured evidence from existing GreenRock signals."""
    guardrails = fundamental_guardrails or _fundamental_guardrails(candidate)
    items = [
        _low_proximity_evidence(candidate),
        _bollinger_evidence(candidate),
        _rsi_evidence(candidate),
        _volume_evidence(candidate),
        _moving_average_evidence(candidate),
        _target_evidence(candidate, price_targets),
        _fundamental_evidence(guardrails),
        _data_quality_evidence(candidate, data_quality_warnings),
    ]
    return tuple(items)


def evidence_agreement_score_from_items(evidence_items: tuple[EvidenceItem, ...]) -> float:
    directional = [item for item in evidence_items if item.direction in {"bullish", "bearish"}]
    if not directional:
        return 50.0
    bullish = sum(_strength_weight(item.strength) for item in directional if item.direction == "bullish")
    bearish = sum(_strength_weight(item.strength) for item in directional if item.direction == "bearish")
    total = bullish + bearish
    if total <= 0:
        return 50.0
    alignment = max(bullish, bearish) / total
    bullish_share = bullish / total
    technical_bullish = sum(
        1 for item in evidence_items if item.category == "technical" and item.direction == "bullish"
    )
    technical_bearish = sum(
        1 for item in evidence_items if item.category == "technical" and item.direction == "bearish"
    )
    agreement = 35 + alignment * 50
    if technical_bullish >= 5 and bullish_share >= 0.70:
        agreement += 10
    if technical_bearish and technical_bullish:
        agreement -= min(15, technical_bearish * 4)
    if any(item.category == "fundamental" and item.direction == "bearish" for item in evidence_items) and technical_bullish >= 4:
        agreement -= 10
    if any(item.category == "data_quality" and item.direction == "bearish" for item in evidence_items):
        agreement -= 8
    return round(max(0.0, min(100.0, agreement)), 2)


def top_evidence_signal(
    candidate: StockCandidate,
    direction: str,
    price_targets: tuple[PriceTarget, ...] = (),
    data_quality_warnings: tuple[str, ...] = (),
) -> str:
    items = build_evidence_items(candidate, price_targets, data_quality_warnings)
    matching = [item for item in items if item.direction == direction]
    if not matching:
        return "none"
    top = sorted(matching, key=lambda item: abs(item.numeric_contribution), reverse=True)[0]
    return f"{top.name}: {top.explanation}"


def candidate_evidence_agreement(candidate: StockCandidate) -> float:
    return evidence_agreement_score_from_items(build_evidence_items(candidate))


def _low_proximity_evidence(candidate: StockCandidate) -> EvidenceItem:
    proximity = candidate.indicators.low_proximity
    if not candidate.has_52_week_low:
        return EvidenceItem("52-week low proximity", "data_quality", "bearish", "moderate", -4.0, "52-week low is unavailable.")
    if proximity <= 0.02:
        return EvidenceItem("52-week low proximity", "technical", "bullish", "exceptional", 20.0, f"Price is only {proximity:.1%} above the 52-week low.")
    if proximity <= 0.10:
        return EvidenceItem("52-week low proximity", "technical", "bullish", "strong", 15.0, f"Price is within {proximity:.1%} of the 52-week low.")
    if proximity <= 0.20:
        return EvidenceItem("52-week low proximity", "technical", "neutral", "weak", 2.0, f"Price is {proximity:.1%} above the 52-week low.")
    return EvidenceItem("52-week low proximity", "technical", "bearish", "moderate", -6.0, f"Price is {proximity:.1%} above the 52-week low.")


def _bollinger_evidence(candidate: StockCandidate) -> EvidenceItem:
    indicators = candidate.indicators
    if indicators.latest_close <= indicators.bollinger_lower:
        return EvidenceItem("Bollinger Band dislocation", "technical", "bullish", "exceptional", 18.0, "Price is below the lower 2.5 standard deviation Bollinger Band.")
    if _score_bollinger_supports_setup(indicators):
        return EvidenceItem("Bollinger Band dislocation", "technical", "bullish", "strong", 14.0, "Price is closer to the lower Bollinger Band than the upper band.")
    return EvidenceItem("Bollinger Band dislocation", "technical", "bearish", "moderate", -6.0, "Price is not positioned near the lower Bollinger Band.")


def _rsi_evidence(candidate: StockCandidate) -> EvidenceItem:
    rsi = candidate.indicators.rsi_14
    if rsi < 30:
        return EvidenceItem("RSI", "technical", "bullish", "strong", 12.0, f"RSI is deeply dislocated at {rsi:.1f}.")
    if rsi < 50:
        return EvidenceItem("RSI", "technical", "bullish", "moderate", 8.0, f"RSI remains below neutral at {rsi:.1f}.")
    if rsi <= 60:
        return EvidenceItem("RSI", "technical", "neutral", "weak", 1.0, f"RSI is near neutral at {rsi:.1f}.")
    return EvidenceItem("RSI", "technical", "bearish", "moderate", -5.0, f"RSI is not dislocated at {rsi:.1f}.")


def _volume_evidence(candidate: StockCandidate) -> EvidenceItem:
    acceleration = _volume_acceleration_value(candidate)
    if not candidate.has_volume_data or acceleration is None:
        return EvidenceItem("Volume acceleration", "data_quality", "bearish", "moderate", -5.0, "Volume data is missing or insufficient.")
    if acceleration >= 0.25:
        return EvidenceItem("Volume acceleration", "technical", "bullish", "strong", 12.0, f"Recent volume is accelerating by {acceleration:.1%}.")
    if acceleration > 0:
        return EvidenceItem("Volume acceleration", "technical", "bullish", "moderate", 7.0, f"Recent volume is improving by {acceleration:.1%}.")
    return EvidenceItem("Volume acceleration", "technical", "bearish", "moderate", -6.0, f"Recent volume is not confirming the setup ({acceleration:.1%}).")


def _moving_average_evidence(candidate: StockCandidate) -> EvidenceItem:
    alignment = _moving_average_alignment_count(candidate)
    if alignment == 3:
        return EvidenceItem("Moving average structure", "technical", "bullish", "strong", 16.0, "Moving average structure aligns with GreenRock dislocation criteria.")
    if alignment == 2:
        return EvidenceItem("Moving average structure", "technical", "bullish", "moderate", 9.0, "Most moving-average checks support the setup.")
    if alignment == 1:
        return EvidenceItem("Moving average structure", "technical", "neutral", "weak", 1.0, "Moving-average evidence is mixed.")
    return EvidenceItem("Moving average structure", "technical", "bearish", "strong", -10.0, "Moving-average structure does not support the setup.")


def _target_evidence(candidate: StockCandidate, price_targets: tuple[PriceTarget, ...]) -> EvidenceItem:
    available = [target for target in price_targets if target.price is not None]
    if not available:
        return EvidenceItem("Statistical target setup", "data_quality", "bearish", "moderate", -5.0, "Statistical targets are unavailable or unreliable.")
    if any(target.price and target.price > candidate.indicators.latest_close for target in available):
        above_ath = sum(1 for target in available if target.relation_to_ath == "above-ath")
        strength = "strong" if above_ath >= 2 else "moderate"
        contribution = 10.0 if above_ath >= 2 else 6.0
        return EvidenceItem("Statistical target setup", "technical", "bullish", strength, contribution, "Statistical upside targets sit above the current price.")
    return EvidenceItem("Statistical target setup", "technical", "neutral", "weak", 0.0, "Statistical targets do not add clear upside evidence.")


def _fundamental_evidence(guardrails: FundamentalGuardrailAnalysis) -> EvidenceItem:
    if guardrails.label == "Strong Balance Sheet":
        return EvidenceItem("Fundamental guardrails", "fundamental", "bullish", "strong", 6.0, "Strong Balance Sheet guardrail supports survivability evidence.")
    if guardrails.label == "Acceptable":
        return EvidenceItem("Fundamental guardrails", "fundamental", "bullish", "moderate", 3.0, "Acceptable guardrail supports baseline recovery evidence.")
    if guardrails.label == "Caution":
        return EvidenceItem("Fundamental guardrails", "fundamental", "bearish", "moderate", -5.0, "Fundamental guardrail is Caution.")
    if guardrails.label == "Red Flag":
        return EvidenceItem("Fundamental guardrails", "fundamental", "bearish", "strong", -9.0, "Fundamental guardrail is Red Flag.")
    return EvidenceItem("Fundamental guardrails", "fundamental", "neutral", "weak", 0.0, "Fundamental guardrail data is insufficient.")


def _data_quality_evidence(candidate: StockCandidate, data_quality_warnings: tuple[str, ...]) -> EvidenceItem:
    market_warnings = _data_quality_warnings(candidate) + data_quality_warnings
    warning_count = len(tuple(dict.fromkeys(market_warnings)))
    if warning_count == 0:
        return EvidenceItem("Data quality", "data_quality", "bullish", "moderate", 4.0, "Core data quality is clean for this preview.")
    if warning_count <= 2:
        return EvidenceItem("Data quality", "data_quality", "bearish", "moderate", -4.0, f"{warning_count} data quality warning(s) require review.")
    return EvidenceItem("Data quality", "data_quality", "bearish", "strong", -8.0, f"{warning_count} data quality warning(s) materially reduce reliability.")


def _evidence_score_adjustment(evidence_items: tuple[EvidenceItem, ...], agreement_score: float) -> float:
    technical_net = sum(
        item.numeric_contribution for item in evidence_items if item.category == "technical"
    )
    technical_adjustment = max(-5.0, min(6.0, technical_net / 20))
    if agreement_score >= 80:
        technical_adjustment += 2.0
    elif agreement_score < 45:
        technical_adjustment -= 4.0
    elif agreement_score < 60:
        technical_adjustment -= 2.0
    return round(max(-8.0, min(8.0, technical_adjustment)), 2)


def _evidence_lines(evidence_items: tuple[EvidenceItem, ...], direction: str) -> tuple[str, ...]:
    matching = [item for item in evidence_items if item.direction == direction]
    if not matching:
        labels = {"bullish": "bullish", "bearish": "bearish", "neutral": "neutral"}
        return (f"No major {labels.get(direction, direction)} evidence item is active.",)
    ordered = sorted(matching, key=lambda item: abs(item.numeric_contribution), reverse=True)
    return tuple(
        f"{item.name} ({item.strength}): {item.explanation}"
        for item in ordered
    )


def _fundamental_guardrails(candidate: StockCandidate) -> FundamentalGuardrailAnalysis:
    fundamentals = candidate.fundamentals
    if fundamentals is None:
        return FundamentalGuardrailAnalysis(
            label="Insufficient Data",
            net_cash=None,
            net_cash_per_share=None,
            quick_ratio=None,
            shares_outstanding_change_percent=None,
            confidence_impact=-8.0,
            score_adjustment=0.0,
            bullish_evidence=(),
            bearish_evidence=("Fundamental data is incomplete or unavailable.",),
            warnings=("Fundamental guardrails unavailable: missing fundamental data.",),
            data_source="unavailable",
        )

    net_cash = fundamentals.net_cash
    quick_ratio = fundamentals.quick_ratio
    share_change = fundamentals.shares_outstanding_change_percent
    warnings = list(fundamentals.fundamental_data_warnings)
    missing_keys = sum(
        1
        for value in (
            fundamentals.cash_and_equivalents,
            fundamentals.total_debt,
            quick_ratio,
            fundamentals.shares_outstanding_current,
            fundamentals.shares_outstanding_prior,
        )
        if value is None
    )
    if missing_keys:
        warnings.append(f"Fundamental data incomplete: {missing_keys} key input(s) missing.")

    market_cap = candidate.market_cap if candidate.has_market_cap and candidate.market_cap > 0 else None
    leverage_ratio = abs(net_cash) / market_cap if net_cash is not None and net_cash < 0 and market_cap else None
    severe_leverage = leverage_ratio is not None and leverage_ratio >= 0.50
    meaningful_debt = leverage_ratio is not None and leverage_ratio >= 0.25
    major_dilution = share_change is not None and share_change >= 0.20
    meaningful_dilution = share_change is not None and share_change >= 0.10
    stable_shares = share_change is not None and share_change <= 0.02

    bullish: list[str] = []
    bearish: list[str] = []
    if net_cash is not None and net_cash > 0:
        bullish.append("Balance sheet appears net cash positive.")
    elif net_cash is not None and net_cash < 0:
        bearish.append("Net debt is present and should be reviewed for recovery support.")
    if quick_ratio is not None and quick_ratio >= 1.5:
        bullish.append("Quick ratio supports near-term liquidity.")
    elif quick_ratio is not None and quick_ratio < 1.0:
        bearish.append("Quick ratio below 1.0 signals liquidity caution.")
    if stable_shares:
        bullish.append("Share count appears stable or declining.")
    elif meaningful_dilution:
        bearish.append("Share count appears to be expanding.")

    if missing_keys >= 3:
        label = "Insufficient Data"
        confidence_impact = -8.0
        score_adjustment = 0.0
    elif (quick_ratio is not None and quick_ratio < 0.75) or severe_leverage or major_dilution:
        label = "Red Flag"
        confidence_impact = -15.0
        score_adjustment = -5.0
    elif (quick_ratio is not None and quick_ratio < 1.0) or meaningful_debt or meaningful_dilution:
        label = "Caution"
        confidence_impact = -8.0
        score_adjustment = -2.0
    elif net_cash is not None and net_cash > 0 and quick_ratio is not None and quick_ratio >= 1.5 and stable_shares:
        label = "Strong Balance Sheet"
        confidence_impact = 10.0
        score_adjustment = 2.0
    elif quick_ratio is not None and quick_ratio >= 1.0 and not major_dilution:
        label = "Acceptable"
        confidence_impact = 5.0
        score_adjustment = 1.0
    else:
        label = "Insufficient Data"
        confidence_impact = -6.0
        score_adjustment = 0.0

    if label in {"Strong Balance Sheet", "Acceptable"} and missing_keys == 0:
        bullish.append("Fundamental data is complete enough for guardrail review.")
    if missing_keys:
        bearish.append("Fundamental data is incomplete.")
    if not bullish:
        bullish.append("No bullish fundamental guardrail is confirmed from available data.")
    if not bearish:
        bearish.append("No major bearish fundamental guardrail is confirmed from available data.")

    return FundamentalGuardrailAnalysis(
        label=label,
        net_cash=net_cash,
        net_cash_per_share=fundamentals.net_cash_per_share,
        quick_ratio=quick_ratio,
        shares_outstanding_change_percent=share_change,
        confidence_impact=confidence_impact,
        score_adjustment=score_adjustment,
        bullish_evidence=tuple(dict.fromkeys(bullish)),
        bearish_evidence=tuple(dict.fromkeys(bearish)),
        warnings=tuple(dict.fromkeys(warnings)),
        data_source=fundamentals.fundamental_data_source or "unavailable",
    )


def _price_targets(
    prices,
    has_price_history: bool,
) -> tuple[float | None, tuple[PriceTarget, ...], tuple[str, ...], str]:
    closes = [price.close for price in prices if price.close > 0] if has_price_history else []
    labels = ("+2 SD", "+3 SD", "+5 SD", "+7 SD")
    empty_targets = tuple(PriceTarget(label, None, "unavailable") for label in labels)
    if len(closes) < 2:
        return None, empty_targets, ("1-year statistical price targets unavailable: insufficient price history.",), "unavailable"

    all_time_high = max(closes)
    lookback_closes = closes[-1260:]
    lookback_label = "5 years" if len(closes) >= 1260 else f"{len(lookback_closes)} trading days available"
    returns = [
        (lookback_closes[index] - lookback_closes[index - 1]) / lookback_closes[index - 1]
        for index in range(1, len(lookback_closes))
        if lookback_closes[index - 1] > 0
    ]
    warnings: list[str] = []
    if len(closes) < 1260:
        warnings.append("ATH based on available provider history, not guaranteed full exchange history.")
    if len(returns) < 2:
        return all_time_high, empty_targets, tuple(warnings + ["1-year statistical price targets unavailable: insufficient return history."]), lookback_label

    current_price = closes[-1]
    annualized_deviation = statistics.pstdev(returns) * math.sqrt(252)
    if annualized_deviation <= 0:
        return all_time_high, empty_targets, tuple(warnings + ["1-year statistical price targets unavailable: price history has no usable variation."]), lookback_label

    targets = tuple(
        PriceTarget(
            label=label,
            price=round(current_price * (1 + multiple * annualized_deviation), 2),
            relation_to_ath="above-ath" if current_price * (1 + multiple * annualized_deviation) >= all_time_high else "below-ath",
        )
        for label, multiple in (("+2 SD", 2), ("+3 SD", 3), ("+5 SD", 5), ("+7 SD", 7))
    )
    return round(all_time_high, 2), targets, tuple(warnings), lookback_label


def _confidence_analysis(
    candidate: StockCandidate,
    prices,
    all_time_high: float | None,
    price_target_lookback: str,
    data_quality_warnings: tuple[str, ...],
    price_targets: tuple[PriceTarget, ...],
    fundamental_guardrails: FundamentalGuardrailAnalysis,
    evidence_agreement_score: float,
) -> ConfidenceAnalysis:
    score = 0.0
    drivers: list[str] = []
    drags: list[str] = []
    valid_prices = [price.close for price in prices if price.close > 0]
    valid_volumes = [price.volume for price in prices if price.volume >= 0]
    indicators = candidate.indicators

    if candidate.has_price_history and len(valid_prices) >= 170:
        score += 8
        drivers.append("Usable price history available.")
    else:
        drags.append("Weak or insufficient price history.")
    if candidate.has_volume_data and any(volume > 0 for volume in valid_volumes):
        score += 6
        drivers.append("Complete volume history is available.")
    else:
        drags.append("Missing or weak volume history.")
    if candidate.has_market_cap and candidate.market_cap > 0:
        score += 6
        drivers.append("Market cap is available.")
    else:
        drags.append("Missing market cap.")
    if all_time_high is None:
        drags.append("All-Time High is unavailable.")
    else:
        score += 5
        drivers.append("All-Time High is available.")
    if candidate.has_52_week_low and indicators.week_52_low > 0:
        score += 5
        drivers.append("52-week low is available.")
    else:
        drags.append("52-week low is unavailable.")

    if price_target_lookback == "5 years":
        score += 25
        drivers.append("Full 5-year price history available for statistical targets.")
    elif len(valid_prices) >= 756:
        score += 20
        drivers.append("3-5 years of price history available.")
        drags.append("Less than 5 years of history for statistical targets.")
    elif len(valid_prices) >= 252:
        score += 13
        drivers.append("1-3 years of price history available.")
        drags.append("Less than 3 years of history for statistical targets.")
    elif valid_prices:
        score += 5
        drags.append("Less than 1 year of price history.")
    else:
        drags.append("No usable price history depth.")

    indicator_ratio = _indicator_agreement_ratio(candidate, price_targets)
    score += round(indicator_ratio * 20, 2)
    if indicator_ratio >= 0.75:
        drivers.append("Indicators broadly agree.")
    elif indicator_ratio >= 0.50:
        drivers.append("Some GreenRock indicators agree.")
        drags.append("Mixed technical signals.")
        score -= 3
    else:
        drags.append("GreenRock indicators conflict.")
        score -= 8

    annualized_volatility = _annualized_return_volatility(valid_prices)
    if annualized_volatility is None:
        drags.append("Price volatility could not be measured.")
    elif annualized_volatility <= 0.35:
        score += 8
        drivers.append("Price action is relatively stable for confidence scoring.")
    elif annualized_volatility <= 0.70:
        score += 5
        drags.append("Moderately noisy price action.")
    else:
        score += 2
        drags.append("Volatile/noisy price action lowers reliability.")

    volume_cv = _coefficient_of_variation([float(volume) for volume in valid_volumes if volume > 0][-252:])
    if volume_cv is None:
        drags.append("Volume consistency could not be measured.")
    elif volume_cv <= 0.60:
        score += 7
        drivers.append("Volume history is reasonably consistent.")
    elif volume_cv <= 1.20:
        score += 4
        drags.append("Volume data is somewhat erratic.")
    else:
        score += 1
        drags.append("Erratic volume data lowers reliability.")

    if candidate.has_market_cap and candidate.market_cap > 0:
        if _market_cap_is_borderline(candidate.market_cap):
            score += 2
            drags.append("Market cap bucket is borderline.")
        else:
            score += 5
            drivers.append("Market cap bucket appears reliable.")

    if _targets_are_available(price_targets) and price_target_lookback == "5 years" and not data_quality_warnings:
        score += 5
        drivers.append("Statistical targets are based on full 5-year history.")
    elif _targets_are_available(price_targets):
        score += 2
        drags.append("Statistical target reliability is limited by available history.")
    else:
        drags.append("Statistical targets are unavailable or unreliable.")

    score += round((evidence_agreement_score - 50) * 0.22, 2)
    if evidence_agreement_score >= 75:
        drivers.append("Evidence Agreement is strong.")
    elif evidence_agreement_score < 50:
        drags.append("Evidence Agreement is weak or conflicted.")
    else:
        drags.append("Evidence Agreement is mixed.")

    if any(not _is_finite(value) for value in (
        indicators.latest_close,
        indicators.sma_10,
        indicators.ema_8,
        indicators.sma_50,
        indicators.sma_150,
        indicators.rsi_14,
        indicators.bollinger_lower,
        indicators.bollinger_upper,
        indicators.week_52_low,
        indicators.volume_avg_10,
        indicators.previous_volume_avg_10,
    )):
        score -= 10
        drags.append("One or more indicator fields is missing or invalid.")

    if _has_market_data_warning(data_quality_warnings):
        score -= min(10, len(data_quality_warnings) * 2)

    score += fundamental_guardrails.confidence_impact
    if fundamental_guardrails.label == "Strong Balance Sheet":
        drivers.append("Strong Balance Sheet guardrail supports survivability and recovery evidence.")
    elif fundamental_guardrails.label == "Acceptable":
        drivers.append("Acceptable fundamental guardrail supports baseline recovery evidence.")
    elif fundamental_guardrails.label == "Caution":
        drags.append("Fundamental guardrail is Caution.")
    elif fundamental_guardrails.label == "Red Flag":
        drags.append("Fundamental guardrail is Red Flag.")
    else:
        drags.append("Insufficient fundamental data lowers evidence confidence.")
    for item in fundamental_guardrails.bullish_evidence:
        if "No bullish" not in item:
            drivers.append(item)
    for item in fundamental_guardrails.bearish_evidence:
        if "No major bearish" not in item:
            drags.append(item)

    score_cap = 100.0
    if len(valid_prices) < 252:
        score_cap = min(score_cap, 55.0)
    elif len(valid_prices) < 756:
        score_cap = min(score_cap, 74.0)
    elif len(valid_prices) < 1260:
        score_cap = min(score_cap, 89.0)
    if not candidate.has_market_cap or candidate.market_cap <= 0:
        score_cap = min(score_cap, 74.0)
    if not _targets_are_available(price_targets):
        score_cap = min(score_cap, 74.0)

    final_score = round(max(0.0, min(score, score_cap)), 2)
    return ConfidenceAnalysis(
        score=final_score,
        band=confidence_band(final_score),
        drivers=tuple(dict.fromkeys(drivers)) or ("No major positive confidence driver identified.",),
        drags=tuple(dict.fromkeys(drags)) or ("No major confidence drag identified.",),
    )


def _research_priority(candidate: StockCandidate, confidence_score: float) -> str:
    score = candidate.score
    if confidence_score < 35 or not candidate.has_price_history:
        return "Ignore"
    if score >= 85 and confidence_score >= 75 and candidate.selection_label == "Strict Pass":
        return "Immediate Review"
    if score >= 70 and confidence_score >= 65:
        return "This Week"
    if score >= 55 and confidence_score >= 50:
        return "Interesting"
    if score >= 45 and confidence_score >= 45:
        return "Monitor"
    return "Ignore"


def _bullish_evidence(candidate: StockCandidate, price_targets: tuple[PriceTarget, ...]) -> tuple[str, ...]:
    indicators = candidate.indicators
    evidence: list[str] = []
    if indicators.low_proximity <= 0.10:
        evidence.append("Trading near the 52-week low, which supports the dislocation setup.")
    if indicators.latest_close <= indicators.bollinger_lower:
        evidence.append("Price is below the lower Bollinger Band.")
    elif abs(indicators.latest_close - indicators.bollinger_lower) < abs(indicators.bollinger_upper - indicators.latest_close):
        evidence.append("Price is closer to the lower Bollinger Band than the upper band.")
    if indicators.rsi_14 < 50:
        evidence.append("RSI shows oversold or dislocation characteristics.")
    volume_acceleration = _volume_acceleration_value(candidate)
    if volume_acceleration is not None and volume_acceleration > 0:
        evidence.append("Volume acceleration supports renewed attention.")
    if {"ema8_below_sma10", "dma50_below_dma150"}.issubset(candidate.passed_rules):
        evidence.append("Moving average structure aligns with GreenRock dislocation criteria.")
    if any(target.price is not None and target.price > indicators.latest_close for target in price_targets):
        evidence.append("Statistical upside targets sit above the current price.")
    if not evidence:
        evidence.append("No major GreenRock evidence item is active yet.")
    return tuple(evidence)


def _bearish_evidence(candidate: StockCandidate, data_quality_warnings: tuple[str, ...]) -> tuple[str, ...]:
    indicators = candidate.indicators
    evidence: list[str] = []
    if _has_market_data_warning(data_quality_warnings):
        evidence.append("Missing or incomplete market data reduces score reliability.")
    if not candidate.has_price_history:
        evidence.append("Weak or insufficient price history.")
    volume_acceleration = _volume_acceleration_value(candidate)
    if volume_acceleration is None or volume_acceleration <= 0:
        evidence.append("No clear volume confirmation yet.")
    if indicators.rsi_14 >= 50:
        evidence.append("RSI is not yet supportive of the dislocation setup.")
    if indicators.low_proximity > 0.10:
        evidence.append("Price is not close enough to the preferred dislocation zone.")
    if not {"ema8_below_sma10", "dma50_below_dma150", "dma50_roc_improving_vs_dma150"}.issubset(candidate.passed_rules):
        evidence.append("Moving average structure does not yet fully support the setup.")
    if not candidate.has_market_cap or candidate.market_cap <= 0:
        evidence.append("Market cap data is weak or unavailable.")
    if not evidence:
        evidence.append("No major GreenRock caution item is active beyond normal research review.")
    return tuple(evidence)


def _watch_next(candidate: StockCandidate, all_time_high: float | None, price_targets: tuple[PriceTarget, ...]) -> tuple[str, ...]:
    indicators = candidate.indicators
    items = [
        f"Watch for price reclaiming the 10 SMA near ${indicators.sma_10:,.2f}.",
        "Watch whether RSI improves while staying consistent with the dislocation setup.",
        "Watch for continuation of volume acceleration versus the prior 10-day average.",
        f"Watch whether price holds above the recent low near ${indicators.week_52_low:,.2f}.",
    ]
    first_target = next((target for target in price_targets if target.price is not None), None)
    if first_target:
        items.append(f"Watch movement toward or reclaim of the {first_target.label} statistical target near ${first_target.price:,.2f}.")
    if all_time_high is not None:
        items.append(f"Watch resistance context versus the All-Time High near ${all_time_high:,.2f}.")
    return tuple(items)


def _analyst_summary(
    candidate: StockCandidate,
    confidence_score: float,
    research_priority: str,
    bullish_evidence: tuple[str, ...],
    bearish_evidence: tuple[str, ...],
    fundamental_guardrails: FundamentalGuardrailAnalysis,
    divergence: str,
) -> str:
    confidence_label = _confidence_label(confidence_score)
    signal = signal_label(candidate.score)
    primary_driver = bullish_evidence[0].removesuffix(".").lower() if bullish_evidence else "the current technical setup"
    primary_caution = bearish_evidence[0].removesuffix(".").lower() if bearish_evidence else "normal research review"
    fundamental_clause = ""
    if fundamental_guardrails.label in {"Strong Balance Sheet", "Acceptable"}:
        fundamental_clause = f" Fundamental guardrails are {fundamental_guardrails.label}, supporting survivability evidence."
    elif fundamental_guardrails.label in {"Caution", "Red Flag"}:
        fundamental_clause = f" Fundamental guardrails are {fundamental_guardrails.label}, so recovery support needs extra review."
    elif fundamental_guardrails.warnings:
        fundamental_clause = " Fundamental data is incomplete, which lowers evidence confidence without automatically implying weakness."
    return (
        f"Atlas flags {candidate.symbol} as {signal} / {candidate.selection_label} with "
        f"{confidence_label} confidence and a {research_priority} research priority. "
        f"The setup is driven primarily by {primary_driver}, while the primary caution is {primary_caution}."
        f"{fundamental_clause} {divergence}".strip()
    )


def _confidence_label(confidence_score: float) -> str:
    if confidence_score >= 80:
        return "high"
    if confidence_score >= 60:
        return "moderate"
    if confidence_score >= 40:
        return "low"
    return "very low"


def _score_confidence_divergence(
    score: float,
    confidence_score: float,
    evidence_items: tuple[EvidenceItem, ...],
    fundamental_guardrails: FundamentalGuardrailAnalysis,
) -> str:
    if abs(score - confidence_score) < 15:
        return "Score and Confidence are broadly aligned."
    bearish_fundamental = any(item.category == "fundamental" and item.direction == "bearish" for item in evidence_items)
    bearish_data = any(item.category == "data_quality" and item.direction == "bearish" for item in evidence_items)
    bullish_technical_count = sum(
        1 for item in evidence_items if item.category == "technical" and item.direction == "bullish"
    )
    if score > confidence_score:
        if bearish_fundamental:
            return (
                "Score is higher than Confidence because the technical setup is stronger than the "
                f"{fundamental_guardrails.label} fundamental guardrail."
            )
        if bearish_data:
            return "Score is higher than Confidence because technical evidence is present while data quality is incomplete."
        return "Score is higher than Confidence because the setup has technical support but evidence reliability is mixed."
    if bullish_technical_count < 3 and fundamental_guardrails.label in {"Strong Balance Sheet", "Acceptable"}:
        return "Confidence is higher than Score because guardrails and data quality are cleaner than the current technical setup."
    return "Confidence is higher than Score because evidence reliability is stronger than the current dislocation setup."


def confidence_band(confidence_score: float) -> str:
    if confidence_score >= 90:
        return "Very High Confidence"
    if confidence_score >= 75:
        return "High Confidence"
    if confidence_score >= 60:
        return "Moderate Confidence"
    if confidence_score >= 40:
        return "Low Confidence"
    return "Very Low Confidence"


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _has_market_data_warning(warnings: tuple[str, ...]) -> bool:
    terms = ("missing", "unavailable", "insufficient", "ATH based", "no usable")
    return any(any(term.lower() in warning.lower() for term in terms) for warning in warnings)


def _strength_weight(strength: str) -> float:
    return {
        "weak": 1.0,
        "moderate": 2.0,
        "strong": 3.0,
        "exceptional": 4.0,
    }.get(strength, 1.0)


def _indicator_agreement_ratio(candidate: StockCandidate, price_targets: tuple[PriceTarget, ...]) -> float:
    indicators = candidate.indicators
    checks = (
        indicators.low_proximity <= 0.10,
        _score_bollinger_supports_setup(indicators),
        indicators.rsi_14 < 50,
        (_volume_acceleration_value(candidate) or 0) > 0,
        _moving_average_alignment_count(candidate) >= 2,
        any(target.price is not None and target.price > indicators.latest_close for target in price_targets),
    )
    return sum(1 for item in checks if item) / len(checks)


def _score_bollinger_supports_setup(indicators) -> bool:
    if indicators.latest_close <= indicators.bollinger_lower:
        return True
    return abs(indicators.latest_close - indicators.bollinger_lower) < abs(indicators.bollinger_upper - indicators.latest_close)


def _moving_average_alignment_count(candidate: StockCandidate) -> int:
    rules = ("ema8_below_sma10", "dma50_below_dma150", "dma50_roc_improving_vs_dma150")
    return sum(1 for rule in rules if rule in candidate.passed_rules)


def _annualized_return_volatility(closes: list[float]) -> float | None:
    values = closes[-252:]
    returns = [
        (values[index] - values[index - 1]) / values[index - 1]
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252)


def _coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    if mean <= 0:
        return None
    return statistics.pstdev(values) / mean


def _market_cap_is_borderline(market_cap: float) -> bool:
    thresholds = (10_000_000_000, 1_000_000_000_000)
    return any(abs(market_cap - threshold) / threshold <= 0.15 for threshold in thresholds)


def _targets_are_available(price_targets: tuple[PriceTarget, ...]) -> bool:
    return any(target.price is not None for target in price_targets)


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
