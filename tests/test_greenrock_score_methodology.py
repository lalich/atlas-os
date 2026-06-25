"""Tests for GreenRock Score methodology documentation."""

from __future__ import annotations

import unittest
from pathlib import Path


class GreenRockScoreMethodologyTests(unittest.TestCase):
    def test_methodology_doc_exists_and_documents_components(self) -> None:
        path = Path("docs/GREENROCK_SCORE_METHODOLOGY.md")
        self.assertTrue(path.exists())
        markdown = path.read_text(encoding="utf-8")

        self.assertIn("# GreenRock Score Methodology", markdown)
        self.assertIn("52-week low proximity", markdown)
        self.assertIn("Bollinger Band setup", markdown)
        self.assertIn("RSI", markdown)
        self.assertIn("Volume acceleration", markdown)
        self.assertIn("Moving average structure", markdown)
        self.assertIn("Bonus / penalty factors", markdown)
        self.assertIn("Future Tuning Notes", markdown)


if __name__ == "__main__":
    unittest.main()
