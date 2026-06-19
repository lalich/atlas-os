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


def build_report_draft() -> GreenRockReport:
    screening = run_screen()
    lines = [
        "# GreenRock Analysts Local Screening Draft",
        "",
        f"Generated: {date.today().isoformat()}",
        "",
        "> Draft only. This report uses mock data and requires human approval before any client-facing use.",
        "",
        "## Screening Summary",
        "",
        f"- Large-cap selected: {len(screening.large_cap)}",
        f"- Small-cap selected: {len(screening.small_cap)}",
        f"- Total selected: {len(screening.selected)}",
        "",
        "## Large Cap Candidates",
        "",
        _candidate_table(screening.large_cap),
        "",
        "## Small Cap Candidates",
        "",
        _candidate_table(screening.small_cap),
        "",
        "## Approval Status",
        "",
        "Human approval is required before any publication, email, or client-facing distribution.",
    ]
    return GreenRockReport(
        title="GreenRock Analysts Local Screening Draft",
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
