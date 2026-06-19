"""Sample GreenRock report generation."""

from __future__ import annotations

from datetime import date

from atlas_os.greenrock.models import GreenRockReport
from atlas_os.greenrock.screener import run_sample_screen


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

