"""Tests for GreenRock Score and signal labels."""

from __future__ import annotations

import unittest

from atlas_os.greenrock.models import IndicatorSnapshot
from atlas_os.greenrock.scoring import greenrock_score, signal_label


class GreenRockScoringTests(unittest.TestCase):
    def test_greenrock_score_is_capped_at_100(self) -> None:
        indicators = _snapshot(
            latest_close=90,
            bollinger_lower=95,
            bollinger_upper=130,
            low_proximity=0,
            rsi_14=0,
            volume_avg_10=150,
            previous_volume_avg_10=100,
            ma_roc_50=-0.01,
            ma_roc_150=-0.04,
        )
        rules = {
            "ema8_below_sma10": True,
            "dma50_below_dma150": True,
        }

        self.assertEqual(greenrock_score(indicators, rules), 100)

    def test_greenrock_score_rewards_dislocation_and_volume(self) -> None:
        strong = _snapshot(
            latest_close=98,
            bollinger_lower=95,
            bollinger_upper=130,
            low_proximity=0.02,
            rsi_14=35,
            volume_avg_10=130,
            previous_volume_avg_10=100,
            ma_roc_50=-0.01,
            ma_roc_150=-0.03,
        )
        weak = _snapshot(
            latest_close=128,
            bollinger_lower=95,
            bollinger_upper=130,
            low_proximity=0.20,
            rsi_14=62,
            volume_avg_10=95,
            previous_volume_avg_10=100,
            ma_roc_50=-0.05,
            ma_roc_150=-0.03,
        )

        self.assertGreater(
            greenrock_score(strong, {"ema8_below_sma10": True, "dma50_below_dma150": True}),
            greenrock_score(weak, {"ema8_below_sma10": False, "dma50_below_dma150": False}),
        )

    def test_signal_label_mapping(self) -> None:
        self.assertEqual(signal_label(100), "Exceptional")
        self.assertEqual(signal_label(85), "Exceptional")
        self.assertEqual(signal_label(84), "Strong")
        self.assertEqual(signal_label(70), "Strong")
        self.assertEqual(signal_label(69), "Watchlist")
        self.assertEqual(signal_label(55), "Watchlist")
        self.assertEqual(signal_label(54), "Excluded or Low Priority")


def _snapshot(**overrides) -> IndicatorSnapshot:
    values = {
        "latest_close": 100,
        "latest_volume": 1000,
        "sma_10": 101,
        "ema_8": 100,
        "sma_50": 95,
        "sma_150": 105,
        "rsi_14": 40,
        "bollinger_lower": 90,
        "bollinger_middle": 110,
        "bollinger_upper": 130,
        "week_52_low": 95,
        "low_proximity": 0.05,
        "volume_avg_10": 120,
        "previous_volume_avg_10": 100,
        "ma_roc_50": -0.01,
        "ma_roc_150": -0.02,
    }
    values.update(overrides)
    return IndicatorSnapshot(**values)


if __name__ == "__main__":
    unittest.main()
