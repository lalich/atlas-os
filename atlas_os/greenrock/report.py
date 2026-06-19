"""GreenRock report generation."""

from __future__ import annotations

from datetime import date

from atlas_os.greenrock.models import GreenRockReport, StockCandidate
from atlas_os.greenrock.screener import run_sample_screen, run_screen


def build_sample_report() -> GreenRockReport:
    screening = run_sample_screen()
    lines = [
        "# GreenRock Analysts Sample Monthly Report",
        "",
        f"Generated: {date.today().isoformat()}",
        "",
        "> Phase 0 sample only. This report uses mock data and is not approved for publication.",
        "",
        "## Selected Mock Candidates",
        "",
        "| Symbol | Company | Bucket | Mock Score | Note |",
        "|---|---|---|---:|---|",
    ]

    for candidate in screening.selected:
        lines.append(
            "| "
            f"{candidate.symbol} | "
            f"{candidate.company_name} | "
            f"{candidate.market_cap_bucket} | "
            f"{candidate.mock_score:.1f} | "
            f"{candidate.note} |"
        )

    lines.extend(
        [
            "",
            "## Approval Status",
            "",
            "Human approval is required before any client-facing use.",
        ]
    )

    return GreenRockReport(
        title="GreenRock Analysts Sample Monthly Report",
        markdown="\n".join(lines) + "\n",
    )


def build_report_draft(run_id: str | None = None) -> GreenRockReport:
    screening = run_screen()
    lines = [
        "# GreenRock Analysts Monthly Report",
        "",
        f"**Date:** {date.today().isoformat()}",
        f"**Run ID:** {run_id or 'local-preview'}",
        "",
        "> Draft only. This report uses mock data and requires human approval before any client-facing use.",
        "",
        "## Executive Summary",
        "",
        (
            "This local draft highlights mock equity candidates that screened favorably under the "
            "GreenRock Analysts mean-reversion and technical-condition framework. The screen selected "
            f"{len(screening.large_cap)} large-cap names and {len(screening.small_cap)} small-cap names "
            "for review. These names are not recommendations, forecasts, or instructions to buy or sell "
            "securities; they are sample outputs intended to validate the Atlas OS workflow."
        ),
        "",
        "## Methodology",
        "",
        (
            "The local GreenRock screen uses mock historical price and volume data only. Candidates are "
            "ranked by proximity to the 52-week low, time spent near the low region, RSI below 50, "
            "rising 10-day average volume, short-term moving average positioning, 50-day versus "
            "150-day moving average structure, improving relative moving average rate of change, and "
            "positioning versus 2.5 standard deviation Bollinger Bands. Large-cap and small-cap groups "
            "are separated at a $5B mock market-cap threshold, with up to 11 names selected from each group."
        ),
        "",
        "## Large Cap Candidates",
        "",
        _candidate_table(screening.large_cap),
        "",
        "## Large Cap Screening Rationale",
        "",
        _screening_rationale(screening.large_cap),
        "",
        "## Small Cap Candidates",
        "",
        _candidate_table(screening.small_cap),
        "",
        "## Small Cap Screening Rationale",
        "",
        _screening_rationale(screening.small_cap),
        "",
        "## Risk Notes",
        "",
        (
            "Technical screens can identify conditions that may warrant further research, but they do not "
            "establish valuation, business quality, liquidity suitability, timing, or future performance. "
            "Candidates near 52-week lows may continue to decline, remain range-bound, or fail to recover. "
            "Small-cap names may carry additional volatility, liquidity, financing, and execution risks. "
            "Any future production workflow should be reviewed alongside fundamental research, portfolio "
            "context, and documented human judgment."
        ),
        "",
        "## Human Approval Disclaimer",
        "",
        (
            "This draft is blocked from publication, email distribution, or any client-facing use until "
            "a human approver explicitly approves the linked Atlas OS approval record."
        ),
        "",
        "## Mock-Data Disclaimer",
        "",
        (
            "All securities, prices, volumes, market capitalizations, scores, and company names in this "
            "draft are mock sample data. No external APIs, live market data, client files, credentials, "
            "brokerage accounts, email systems, or distribution services were used."
        ),
        "",
        "## Compliance Notes",
        "",
        (
            "This material is for internal workflow testing only. It does not provide personalized "
            "investment advice, guarantee outcomes, or recommend that any person buy, sell, or hold a "
            "security. Any client-facing version would require appropriate review, substantiation, and "
            "approval under the applicable GreenRock process."
        ),
    ]
    return GreenRockReport(
        title="GreenRock Analysts Monthly Report",
        markdown="\n".join(lines) + "\n",
    )


def _candidate_table(candidates: tuple[StockCandidate, ...]) -> str:
    lines = [
        "| Symbol | Company | Market Cap | Score | RSI | 52w Low Proximity | Note |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for candidate in candidates:
        indicators = candidate.indicators
        lines.append(
            "| "
            f"{candidate.symbol} | "
            f"{candidate.company_name} | "
            f"${candidate.market_cap / 1_000_000_000:.2f}B | "
            f"{candidate.score:.2f} | "
            f"{indicators.rsi_14:.1f} | "
            f"{indicators.low_proximity:.1%} | "
            f"{candidate.note} |"
        )
    return "\n".join(lines)


def _screening_rationale(candidates: tuple[StockCandidate, ...]) -> str:
    lines: list[str] = []
    for candidate in candidates:
        indicators = candidate.indicators
        lines.extend(
            [
                f"### {candidate.symbol} - {candidate.company_name}",
                "",
                (
                    f"{candidate.symbol} screened in because the mock price is within "
                    f"{indicators.low_proximity:.1%} of its mock 52-week low, RSI is "
                    f"{indicators.rsi_14:.1f}, 10-day average volume is rising, the 8 EMA "
                    "is below the 10 SMA, and the 50 DMA remains below the 150 DMA. The "
                    "latest mock price is closer to the lower 2.5 standard deviation "
                    "Bollinger Band than the upper band, supporting further internal review "
                    "under the GreenRock screening framework."
                ),
                "",
            ]
        )
    return "\n".join(lines).strip()
