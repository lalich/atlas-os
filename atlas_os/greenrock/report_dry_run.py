"""Read-only GreenRock report dry-run assembly."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from atlas_os.greenrock.derivatives import latest_derivative_analysis
from atlas_os.greenrock.scanner import latest_scan
from atlas_os.greenrock.staging import STAGING_BUCKET_LABELS, load_staged_candidates


def report_dry_run_dir(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / "report_dry_runs"


def create_report_dry_run(output_dir: Path) -> Path:
    dry_run_id = "report-dry-run-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    markdown = build_report_dry_run_markdown(output_dir, dry_run_id=dry_run_id)
    directory = report_dry_run_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dry_run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def build_report_dry_run_markdown(output_dir: Path, dry_run_id: str = "local-dry-run") -> str:
    scan = latest_scan(output_dir)
    staged = load_staged_candidates(output_dir)
    derivatives = latest_derivative_analysis(output_dir)
    lines = [
        "# GreenRock Report Agent Dry Run",
        "",
        f"**Dry Run ID:** {dry_run_id}",
        f"**Created At:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "**Status:** DRAFT / REVIEW ONLY",
        "",
        (
            "> Dry run only. No email, publishing, brokerage execution, order construction, "
            "client contact, PDF export, approval bypass, or external LLM/API action was performed."
        ),
        "",
        "## Market Scan",
        "",
        _market_scan_section(scan),
        "",
        "## Wall Candidates",
        "",
        _wall_candidates_section(staged),
        "",
        "## Derivative Workbench Top Research",
        "",
        _derivative_top_research_section(derivatives),
        "",
        "## Exclusions / No-Recommendation Explanations",
        "",
        _exclusions_section(derivatives),
        "",
        "## Strategy Intent",
        "",
        _strategy_intent_section(derivatives),
        "",
        "## Risk Notes",
        "",
        (
            "- GreenRock Score, derivative research score, cross-window classification, and strategy intent "
            "are research triage aids only."
        ),
        "- Options research may be stale, illiquid, mispriced, or unavailable when provider data is incomplete.",
        "- Staged equity candidates can remain underfilled, lack analytics, or require additional fundamental review.",
        "- This dry run does not establish suitability, portfolio fit, or any transaction recommendation.",
        "",
        "## Human Review Required",
        "",
        (
            "A human reviewer must decide whether any separate approval-gated draft should be generated. "
            "This dry run is not client-facing and cannot be sent, published, traded, or exported as a final report."
        ),
    ]
    return "\n".join(lines) + "\n"


def _market_scan_section(scan) -> str:
    if scan is None:
        return "No latest market scan was found. Run a local scan before relying on report dry-run context."
    leaders = scan.rows[:8]
    lines = [
        f"- Latest scan: {scan.scan_id}",
        f"- Population: {scan.population}",
        f"- Data source: {scan.data_source}",
        f"- Scored rows: {len(scan.rows)}",
        f"- Provider failures: {scan.provider_failure_count}",
        "",
        "| Rank | Ticker | Score | Confidence | Priority | Guardrail |",
        "|---:|---|---:|---:|---|---|",
    ]
    for row in leaders:
        lines.append(
            "| "
            f"{_cell(row.get('rank', ''))} | "
            f"{_cell(row.get('symbol', ''))} | "
            f"{_cell(row.get('greenrock_score', ''))} | "
            f"{_cell(row.get('greenrock_confidence', ''))} | "
            f"{_cell(row.get('research_priority', ''))} | "
            f"{_cell(row.get('fundamental_guardrail', ''))} |"
        )
    if not leaders:
        lines.append("| - | No ranked scan rows available | - | - | - | - |")
    return "\n".join(lines)


def _wall_candidates_section(staged: tuple[dict[str, str], ...]) -> str:
    if not staged:
        return "No staged Wall/report candidates are available."
    lines = [
        "| Ticker | Bucket | Score | Evidence | Priority | Notes |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in staged[:12]:
        bucket = STAGING_BUCKET_LABELS.get(row.get("staged_bucket", ""), row.get("staged_bucket", ""))
        lines.append(
            "| "
            f"{_cell(row.get('ticker', ''))} | "
            f"{_cell(bucket)} | "
            f"{_cell(row.get('greenrock_score', ''))} | "
            f"{_cell(row.get('evidence_agreement', ''))} | "
            f"{_cell(row.get('research_priority', ''))} | "
            f"{_cell(row.get('notes', ''))} |"
        )
    return "\n".join(lines)


def _derivative_top_research_section(analysis: dict | None) -> str:
    if not analysis:
        return "No Derivative Workbench snapshot is available. Equity report dry run continues without options context."
    rows = _top_derivative_rows(analysis)
    if not rows:
        return "Derivative Workbench snapshot found, but no OTM Top Research contracts are available."
    lines = [
        f"- Snapshot: {analysis.get('snapshot_id', '')}",
        f"- Underlying: {analysis.get('ticker', '')} at {analysis.get('underlying_price', '')}",
        "- Top Research remains OTM-only; raw chain CSVs retain full contract data.",
        "",
        "| Window | Type | Expiration | Strike | Score | Rationale |",
        "|---|---|---|---:|---:|---|",
    ]
    for window, item in rows[:10]:
        contract = item.get("contract", {})
        lines.append(
            "| "
            f"{_cell(str(window) + 'D')} | "
            f"{_cell(contract.get('option_type', ''))} | "
            f"{_cell(contract.get('expiration', ''))} | "
            f"{_cell(str(contract.get('strike', '')))} | "
            f"{_cell(str(item.get('score', '')))} | "
            f"{_cell(item.get('ranking_rationale', ''))} |"
        )
    return "\n".join(lines)


def _exclusions_section(analysis: dict | None) -> str:
    explanations = [
        "This report dry run is not a recommendation and does not ask the operator to buy, sell, hold, hedge, or trade.",
        "Derivative exclusions explain why contracts did not enter OTM Top Research; they are not trading instructions.",
    ]
    if not analysis:
        return "\n".join(f"- {item}" for item in explanations)
    excluded = []
    for group_name in ("excluded_calls", "excluded_puts"):
        for window, rows in analysis.get(group_name, {}).items():
            for item in rows[:4]:
                contract = item.get("contract", {})
                excluded.append(
                    f"{window}D {contract.get('option_type', '')} {contract.get('strike', '')}: "
                    f"{'; '.join(item.get('reasons', ())) or 'excluded'}"
                )
    if excluded:
        explanations.extend(excluded[:12])
    else:
        explanations.append("No contracts were reported as excluded by the latest Derivative Workbench snapshot.")
    return "\n".join(f"- {item}" for item in explanations)


def _strategy_intent_section(analysis: dict | None) -> str:
    if not analysis:
        return "No strategy intent labels are available without a Derivative Workbench snapshot."
    rows = _top_derivative_rows(analysis)
    if not rows:
        return "No Top Research contracts are available for strategy intent mapping."
    lines = [
        "| Window | Contract | Intent | Manifesto | Position Context | Rationale |",
        "|---|---|---|---|---|---|",
    ]
    for window, item in rows[:10]:
        contract = item.get("contract", {})
        contract_label = f"{contract.get('option_type', '')} {contract.get('strike', '')}"
        lines.append(
            "| "
            f"{_cell(str(window) + 'D')} | "
            f"{_cell(contract_label)} | "
            f"{_cell(item.get('strategy_intent', 'research_only'))} | "
            f"{_cell(item.get('manifesto_alignment', ''))} | "
            f"{_cell(item.get('position_context_alignment', ''))} | "
            f"{_cell(item.get('intent_rationale', ''))} |"
        )
    return "\n".join(lines)


def _top_derivative_rows(analysis: dict) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for group_name in ("top_calls", "top_puts"):
        for window, items in analysis.get(group_name, {}).items():
            for item in items:
                rows.append((str(window), item))
    rows.sort(
        key=lambda row: (
            _window_sort_key(row[0]),
            row[1].get("contract", {}).get("option_type", ""),
            -_float(row[1].get("score", 0)),
        )
    )
    return rows


def _window_sort_key(window: str) -> int:
    try:
        return int(window)
    except ValueError:
        return 9999


def _cell(value) -> str:
    return str(value or "-").replace("|", "/").replace("\n", " ").strip()


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
