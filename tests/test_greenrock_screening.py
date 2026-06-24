"""Tests for GreenRock local screening."""

from __future__ import annotations

import unittest

from atlas_os.greenrock.criteria import evaluate_stock, passes_core_criteria
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.screener import run_screen


class GreenRockScreeningTests(unittest.TestCase):
    def test_screen_selects_11_large_and_11_small_cap_candidates(self) -> None:
        result = run_screen()

        self.assertEqual(len(result.mega_rock), 1)
        self.assertEqual(len(result.large_cap), 11)
        self.assertEqual(len(result.small_cap), 11)
        self.assertEqual(len(result.selected), 23)

    def test_selected_candidates_pass_all_core_rules(self) -> None:
        result = run_screen()

        for candidate in result.selected:
            self.assertEqual(candidate.failed_rules, ())
            self.assertGreaterEqual(candidate.score, 80)

    def test_market_cap_split_uses_10b_and_1t_thresholds(self) -> None:
        result = run_screen()

        self.assertTrue(all(10_000_000_000 <= candidate.market_cap < 1_000_000_000_000 for candidate in result.large_cap))
        self.assertTrue(all(candidate.market_cap < 10_000_000_000 for candidate in result.small_cap))

    def test_noise_stock_fails_screen(self) -> None:
        noise = next(stock for stock in load_mock_stocks() if stock.symbol == "NOISE")
        candidate = evaluate_stock(noise)

        self.assertFalse(passes_core_criteria(candidate))
        self.assertGreater(len(candidate.failed_rules), 0)


if __name__ == "__main__":
    unittest.main()
