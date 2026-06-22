"""GreenRock scoring helpers."""

from __future__ import annotations

from atlas_os.greenrock.models import IndicatorSnapshot


def greenrock_score(indicators: IndicatorSnapshot, rule_results: dict[str, bool]) -> float:
    """Calculate a 0-100 GreenRock technical dislocation score."""
    score = 0.0

    score += _bounded((0.10 - indicators.low_proximity) / 0.10, 0, 1) * 20
    score += _bollinger_location_score(indicators) * 20
    score += _bounded((50 - indicators.rsi_14) / 50, 0, 1) * 15
    score += _volume_acceleration_score(indicators) * 15
    score += _moving_average_structure_score(rule_results, indicators) * 20

    if indicators.latest_close < indicators.bollinger_lower:
        score += 10

    return round(min(score, 100.0), 2)


def signal_label(score: float) -> str:
    if score >= 85:
        return "Exceptional"
    if score >= 70:
        return "Strong"
    if score >= 55:
        return "Watchlist"
    return "Excluded or Low Priority"


def _bollinger_location_score(indicators: IndicatorSnapshot) -> float:
    band_width = indicators.bollinger_upper - indicators.bollinger_lower
    if band_width <= 0:
        return 0
    upper_distance = indicators.bollinger_upper - indicators.latest_close
    return _bounded(upper_distance / band_width, 0, 1)


def _volume_acceleration_score(indicators: IndicatorSnapshot) -> float:
    if indicators.previous_volume_avg_10 <= 0:
        return 0
    acceleration = (indicators.volume_avg_10 - indicators.previous_volume_avg_10) / indicators.previous_volume_avg_10
    return _bounded(acceleration / 0.25, 0, 1)


def _moving_average_structure_score(
    rule_results: dict[str, bool],
    indicators: IndicatorSnapshot,
) -> float:
    score = 0.0
    if rule_results.get("ema8_below_sma10"):
        score += 0.25
    if rule_results.get("dma50_below_dma150"):
        score += 0.35
    if indicators.ma_roc_50 >= indicators.ma_roc_150:
        score += 0.40
    return _bounded(score, 0, 1)


def _bounded(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))

