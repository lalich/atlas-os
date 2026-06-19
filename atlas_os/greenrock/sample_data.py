"""Mock GreenRock sample data for Phase 0."""

from __future__ import annotations

from atlas_os.greenrock.models import StockCandidate


SAMPLE_CANDIDATES: tuple[StockCandidate, ...] = (
    StockCandidate(
        symbol="ALFA",
        company_name="Alfa Manufacturing",
        market_cap_bucket="large_cap",
        mock_score=91.4,
        note="Strong mock trend profile with stable liquidity.",
    ),
    StockCandidate(
        symbol="BRVO",
        company_name="Bravo Systems",
        market_cap_bucket="large_cap",
        mock_score=88.7,
        note="Sample relative strength leader in the large-cap bucket.",
    ),
    StockCandidate(
        symbol="CRWN",
        company_name="Crown Medical",
        market_cap_bucket="small_cap",
        mock_score=86.2,
        note="Mock small-cap candidate with improving momentum.",
    ),
)
