"""GreenRock report generation."""

from __future__ import annotations

from datetime import date

from atlas_os.greenrock.models import GreenRockReport, StockCandidate
from atlas_os.greenrock.scoring import signal_label
from atlas_os.greenrock.models import ScreeningResult
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


def build_report_draft(
    run_id: str | None = None,
    screening: ScreeningResult | None = None,
    data_mode: str = "mock",
    data_source: str = "mock_sample_data",
) -> GreenRockReport:
    screening = screening or run_screen()
    resolved_data_mode = (screening.data_mode or data_mode).upper()
    resolved_data_source = screening.data_source or data_source
    is_mock = resolved_data_mode == "MOCK"
    lines = [
        "# GreenRock Analysts Monthly Opportunity Report",
        "",
        "## Technical Dislocation Screen",
        "",
        f"**Date:** {date.today().isoformat()}",
        f"**Run ID:** {run_id or 'local-preview'}",
        f"**Data Mode:** {resolved_data_mode}",
        f"**Data Source:** {resolved_data_source}",
        f"**Selection Mode:** {screening.selection_mode.upper()}",
        "",
        (
            "> Draft only. This report requires human approval before any client-facing use. "
            f"Data mode for this run: {resolved_data_mode}."
        ),
        "",
        "## How to Read This Report",
        "",
        (
            "This monthly note is designed as a focused review queue for technical dislocation setups. "
            "The tables highlight securities that meet GreenRock's local screening framework, while "
            "the rationale sections explain why each name surfaced and what would weaken the setup. "
            "Signal labels are prioritization aids for internal review only, not recommendations."
        ),
        "",
        "## Executive Summary",
        "",
        (
            f"The latest {resolved_data_mode.lower()} screen found a review set of technical-dislocation candidates across both "
            "market-cap groups. Large-cap names generally represent higher-liquidity review candidates, "
            "while the small/mid-cap group may offer sharper dislocation signals with higher volatility "
            "and liquidity sensitivity. The report is intentionally written as a research starting point: "
            "it identifies setups for further review, not conclusions or personalized investment actions."
        ),
        "",
        "## Market Setup / Regime Placeholder",
        "",
        (
            "This section is a placeholder for broader market regime, volatility backdrop, sector "
            "participation, and liquidity conditions that frame the opportunity set. It is not a "
            "recommendation and remains subject to human review before any client-facing use."
        ),
        "",
        "## Mega Rock Universe",
        "",
        _mega_rock_section(resolved_data_mode, resolved_data_source, len(screening.all_candidates)),
        "",
        "## Mega Rock Pick",
        "",
        _candidate_table(screening.mega_rock),
        "",
        _data_quality_note(screening.data_quality_warnings, screening.selection_mode, resolved_data_mode),
        "",
        "## Top Large-Cap Candidates",
        "",
        _candidate_table(screening.large_cap),
        "",
        "## Large-Cap Setup Notes",
        "",
        _screening_rationale(screening.large_cap),
        "",
        "## Top Small/Mid-Cap Candidates",
        "",
        _candidate_table(screening.small_cap),
        "",
        "## Small/Mid-Cap Setup Notes",
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
        "## Methodology Appendix",
        "",
        (
            "GreenRock Score is a 0-100 technical score built from 52-week low proximity, Bollinger "
            "Band location, RSI, 10-day volume acceleration, moving average structure, and a bonus for "
            "trading below the lower 2.5 standard deviation Bollinger Band. Signal labels map as follows: "
            "85-100 Exceptional, 70-84 Strong, 55-69 Watchlist, and below 55 Excluded or Low Priority. "
            "Large-cap and small/mid-cap groups are separated at a $5B mock market-cap threshold."
        ),
        "",
        "## Data Mode Disclaimer",
        "",
        _data_mode_disclaimer(is_mock, resolved_data_source),
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
        title="GreenRock Analysts Monthly Opportunity Report",
        markdown="\n".join(lines) + "\n",
        source=resolved_data_source,
    )


def _candidate_table(candidates: tuple[StockCandidate, ...]) -> str:
    lines = [
        "| Symbol | Company | Market Cap | GreenRock Score | Signal | Selection | RSI | 52w Low Proximity |",
        "|---|---|---:|---:|---|---|---:|---:|",
    ]
    for candidate in candidates:
        indicators = candidate.indicators
        lines.append(
            "| "
            f"{candidate.symbol} | "
            f"{candidate.company_name} | "
            f"${candidate.market_cap / 1_000_000_000:.2f}B | "
            f"{candidate.score:.2f} | "
            f"{signal_label(candidate.score)} | "
            f"{candidate.selection_label} | "
            f"{indicators.rsi_14:.1f} | "
            f"{indicators.low_proximity:.1%} |"
        )
    if not candidates:
        lines.append("| No candidates available | - | - | - | - | - | - | - |")
    return "\n".join(lines)


def _screening_rationale(candidates: tuple[StockCandidate, ...]) -> str:
    lines: list[str] = []
    for candidate in candidates:
        indicators = candidate.indicators
        lines.extend(
            [
                f"### {candidate.symbol} - {candidate.company_name}",
                "",
                f"**Signal:** {signal_label(candidate.score)} | **GreenRock Score:** {candidate.score:.2f}",
                "",
                "**Why It Screened In**",
                "",
                (
                    f"- Price is within {indicators.low_proximity:.1%} of the 52-week low, "
                    "placing it in the screen's technical dislocation zone."
                ),
                (
                    f"- RSI is {indicators.rsi_14:.1f}, which keeps the name below the screen's "
                    "neutral momentum threshold."
                ),
                (
                    "- 10-day average volume is rising, suggesting improving attention in the mock "
                    "or configured market data set."
                ),
                (
                    "- Moving average structure remains dislocated, with the 8 EMA below the 10 SMA "
                    "and the 50 DMA below the 150 DMA."
                ),
                "",
                "**What Would Invalidate the Setup**",
                "",
                (
                    "- A sustained move away from the lower Bollinger Band without improving trend "
                    "quality would reduce the technical dislocation signal."
                ),
                (
                    "- Weakening volume acceleration, a deteriorating RSI profile, or a continued "
                    "break below the low region would move the name lower in the review queue."
                ),
                "",
            ]
        )
    return "\n".join(lines).strip()


def _data_mode_disclaimer(is_mock: bool, data_source: str) -> str:
    if is_mock:
        return (
            "This draft uses mock sample data. No external APIs, live market data, client files, "
            "credentials, brokerage accounts, email systems, or distribution services were used."
        )
    return (
        f"This draft uses real-market-data mode from {data_source}. It remains draft-only, "
        "approval-gated, and not approved for publication, email distribution, or client-facing use. "
        "No client files, brokerage trading systems, email systems, or publishing services were used."
    )


def _mega_rock_section(data_mode: str, data_source: str, candidate_count: int) -> str:
    if data_source in {"yfinance:mega_rock", "yfinance:greenrock_universes"}:
        return (
            f"This {data_mode} run screened {candidate_count} securities from local GreenRock ticker "
            "universes using the configured yfinance provider. The Mega Rock, large-cap, and small/mid-cap "
            "universes are operator-managed starting points and do not imply that any security is suitable "
            "for any person or portfolio."
        )
    return (
        f"This {data_mode} run screened {candidate_count} securities from data source `{data_source}`. "
        "Ticker universe composition is an operator input and remains subject to review."
    )


def _data_quality_note(warnings: tuple[str, ...], selection_mode: str, data_mode: str) -> str:
    selection_note = ""
    if data_mode == "REAL" and selection_mode == "ranked":
        selection_note = (
            "\n\nReal-data mode uses ranked selection when strict criteria produce fewer than "
            "the target number of candidates."
        )
    if not warnings:
        return (
            "## Data Quality Note\n\n"
            "All GreenRock Picks Board sections filled their target slot counts for this run."
            f"{selection_note}"
        )
    lines = [
        "## Data Quality Note",
        "",
        "This run did not fill every Picks Board section target. Review the underlying universe, "
        "market data availability, and screening criteria before relying on the draft.",
        selection_note.strip(),
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)
