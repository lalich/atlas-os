"""GreenRock local screening criteria."""

from __future__ import annotations

from atlas_os.greenrock.indicators import (
    average_volume_trend,
    bollinger_bands,
    exponential_moving_average,
    moving_average_rate_of_change,
    relative_strength_index,
    simple_moving_average,
    week_52_low_proximity,
)
from atlas_os.greenrock.models import IndicatorSnapshot, MockStock, StockCandidate
from atlas_os.greenrock.scoring import greenrock_score

MEGA_ROCK_THRESHOLD = 1_000_000_000_000
LARGE_CAP_THRESHOLD = 10_000_000_000


def evaluate_stock(stock: MockStock) -> StockCandidate:
    closes = tuple(price.close for price in stock.prices)
    volumes = tuple(price.volume for price in stock.prices)
    lower_band, middle_band, upper_band = bollinger_bands(closes, standard_deviations=2.5)
    week_52_low, low_proximity = week_52_low_proximity(closes)
    volume_avg_10, previous_volume_avg_10 = average_volume_trend(volumes)

    indicators = IndicatorSnapshot(
        latest_close=closes[-1],
        latest_volume=volumes[-1],
        sma_10=simple_moving_average(closes, 10),
        ema_8=exponential_moving_average(closes, 8),
        sma_50=simple_moving_average(closes, 50),
        sma_150=simple_moving_average(closes, 150),
        rsi_14=relative_strength_index(closes, 14),
        bollinger_lower=lower_band,
        bollinger_middle=middle_band,
        bollinger_upper=upper_band,
        week_52_low=week_52_low,
        low_proximity=low_proximity,
        volume_avg_10=volume_avg_10,
        previous_volume_avg_10=previous_volume_avg_10,
        ma_roc_50=moving_average_rate_of_change(closes, 50),
        ma_roc_150=moving_average_rate_of_change(closes, 150),
    )

    rule_results = {
        "within_10pct_52w_low": indicators.low_proximity <= 0.10,
        "near_low_30_days": _near_low_region(closes, indicators.week_52_low, days=30),
        "rsi_below_50": indicators.rsi_14 < 50,
        "increasing_10d_avg_volume": indicators.volume_avg_10 > indicators.previous_volume_avg_10,
        "ema8_below_sma10": indicators.ema_8 < indicators.sma_10,
        "dma50_below_dma150": indicators.sma_50 < indicators.sma_150,
        "dma50_roc_improving_vs_dma150": indicators.ma_roc_50 >= indicators.ma_roc_150,
        "closer_to_lower_bollinger": _is_closer_to_lower_band(
            indicators.latest_close,
            indicators.bollinger_lower,
            indicators.bollinger_upper,
        ),
    }

    score = greenrock_score(indicators, rule_results)
    bucket = _market_cap_bucket(stock.market_cap)
    passed = tuple(name for name, passed_rule in rule_results.items() if passed_rule)
    failed = tuple(name for name, passed_rule in rule_results.items() if not passed_rule)

    return StockCandidate(
        symbol=stock.symbol,
        company_name=stock.company_name,
        market_cap_bucket=bucket,
        market_cap=stock.market_cap,
        score=score,
        indicators=indicators,
        passed_rules=passed,
        failed_rules=failed,
        note=_candidate_note(indicators, failed),
        has_price_history=stock.has_price_history,
        has_market_cap=stock.has_market_cap,
        has_volume_data=stock.has_volume_data,
        has_52_week_low=stock.has_52_week_low,
        skipped_reason=stock.skipped_reason,
        selection_label="Strict Pass" if not failed else "Watchlist",
        fundamentals=stock.fundamentals,
    )


def passes_core_criteria(candidate: StockCandidate) -> bool:
    return not candidate.failed_rules


def _market_cap_bucket(market_cap: float) -> str:
    if market_cap >= MEGA_ROCK_THRESHOLD:
        return "mega_rock"
    if market_cap >= LARGE_CAP_THRESHOLD:
        return "large_cap"
    return "small_cap"


def _near_low_region(closes: tuple[float, ...], week_52_low: float, days: int = 30) -> bool:
    if len(closes) < days:
        return False
    return all(close <= week_52_low * 1.10 for close in closes[-days:])


def _is_closer_to_lower_band(price: float, lower: float, upper: float) -> bool:
    return abs(price - lower) < abs(upper - price)


def _candidate_note(indicators: IndicatorSnapshot, failed_rules: tuple[str, ...]) -> str:
    if failed_rules:
        return f"Candidate missed {len(failed_rules)} local screening rule(s)."
    if indicators.latest_close < indicators.bollinger_lower:
        return "Candidate passes all rules with bonus below lower Bollinger Band."
    return "Candidate passes all local GreenRock screening rules."
