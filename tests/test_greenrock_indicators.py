"""Tests for GreenRock technical indicators."""

from __future__ import annotations

import unittest

from atlas_os.greenrock.indicators import (
    average_volume_trend,
    bollinger_bands,
    exponential_moving_average,
    moving_average_rate_of_change,
    relative_strength_index,
    simple_moving_average,
    week_52_low_proximity,
)


class IndicatorTests(unittest.TestCase):
    def test_simple_moving_average(self) -> None:
        self.assertEqual(simple_moving_average([1, 2, 3, 4, 5], 3), 4)

    def test_exponential_moving_average_flat_series(self) -> None:
        self.assertEqual(exponential_moving_average([10, 10, 10, 10, 10], 3), 10)

    def test_rsi_downtrend_is_below_50(self) -> None:
        values = [30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16]
        self.assertEqual(relative_strength_index(values), 0)

    def test_bollinger_bands_use_2_5_standard_deviations(self) -> None:
        values = [10, 10, 10, 10, 20]
        lower, middle, upper = bollinger_bands(values, period=5, standard_deviations=2.5)
        self.assertAlmostEqual(middle, 12)
        self.assertAlmostEqual(lower, 2)
        self.assertAlmostEqual(upper, 22)

    def test_week_52_low_proximity(self) -> None:
        low, proximity = week_52_low_proximity([12, 10, 11])
        self.assertEqual(low, 10)
        self.assertAlmostEqual(proximity, 0.1)

    def test_average_volume_trend(self) -> None:
        current, previous = average_volume_trend([100] * 10 + [120] * 10)
        self.assertEqual(previous, 100)
        self.assertEqual(current, 120)

    def test_moving_average_rate_of_change(self) -> None:
        values = list(range(1, 81))
        rate = moving_average_rate_of_change(values, period=10, lookback=20)
        self.assertGreater(rate, 0)


if __name__ == "__main__":
    unittest.main()

