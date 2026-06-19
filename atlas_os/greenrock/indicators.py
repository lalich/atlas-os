"""Technical indicators used by the local GreenRock screener."""

from __future__ import annotations

import statistics


def simple_moving_average(values: list[float] | tuple[float, ...], period: int) -> float:
    _require_period(values, period)
    return sum(values[-period:]) / period


def exponential_moving_average(values: list[float] | tuple[float, ...], period: int) -> float:
    _require_period(values, period)
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return ema


def relative_strength_index(values: list[float] | tuple[float, ...], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError(f"Need at least {period + 1} values")

    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent_changes = changes[-period:]
    gains = [max(change, 0) for change in recent_changes]
    losses = [abs(min(change, 0)) for change in recent_changes]
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def bollinger_bands(
    values: list[float] | tuple[float, ...],
    period: int = 20,
    standard_deviations: float = 2.5,
) -> tuple[float, float, float]:
    _require_period(values, period)
    window = values[-period:]
    middle = sum(window) / period
    deviation = statistics.pstdev(window)
    lower = middle - (standard_deviations * deviation)
    upper = middle + (standard_deviations * deviation)
    return lower, middle, upper


def week_52_low_proximity(values: list[float] | tuple[float, ...]) -> tuple[float, float]:
    if not values:
        raise ValueError("Need at least one value")
    low = min(values[-252:])
    latest = values[-1]
    return low, (latest - low) / low


def average_volume_trend(volumes: list[int] | tuple[int, ...], period: int = 10) -> tuple[float, float]:
    if len(volumes) < period * 2:
        raise ValueError(f"Need at least {period * 2} volume values")
    previous = sum(volumes[-period * 2 : -period]) / period
    current = sum(volumes[-period:]) / period
    return current, previous


def moving_average_rate_of_change(
    values: list[float] | tuple[float, ...],
    period: int,
    lookback: int = 20,
) -> float:
    if len(values) < period + lookback:
        raise ValueError(f"Need at least {period + lookback} values")
    current = simple_moving_average(values, period)
    prior = simple_moving_average(values[:-lookback], period)
    return (current - prior) / prior


def _require_period(values: list[float] | tuple[float, ...], period: int) -> None:
    if period <= 0:
        raise ValueError("Period must be positive")
    if len(values) < period:
        raise ValueError(f"Need at least {period} values")

