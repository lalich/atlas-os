"""Generate approval-gated GreenRock reports from staged candidates."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from sqlite3 import Connection

from atlas_os.core.approvals import ApprovalRequest, create_approval_request, get_approval
from atlas_os.core.artifacts import Artifact
from atlas_os.core.reports import create_report_record
from atlas_os.core.workflow_runner import WorkflowContext, WorkflowRunner, WorkflowStep
from atlas_os.core.workflow_runs import WorkflowRun
from atlas_os.greenrock.assets import greenrock_logo_path
from atlas_os.greenrock.screener import CSV_HEADERS
from atlas_os.greenrock.staging import (
    LARGE_BUCKET,
    MEGA_BUCKET,
    SMALL_MID_BUCKET,
    STAGING_ANALYTIC_FIELDS,
    STAGING_BUCKET_LABELS,
    enrich_staged_candidates,
    load_staged_candidates,
    row_missing_analytics,
    staging_analytics_status,
    staging_readiness,
)


STAGING_REPORT_HEADERS = CSV_HEADERS + [
    "confidence",
    "research_priority",
    "source_list",
    "source_scan_id",
    "staging_notes",
    "staged_bucket",
]


@dataclass(frozen=True)
class StagingReportReadiness:
    can_generate: bool
    warnings: tuple[str, ...]


def staging_report_readiness(output_dir: Path, allow_underfilled: bool = False) -> StagingReportReadiness:
    warnings = tuple(
        f"{item.label} is {item.status.lower()} ({item.count}/{item.target if item.target is not None else 'review'})."
        for item in staging_readiness(output_dir)
        if item.status in {"Underfilled", "Overfilled"}
    )
    blocking = any("underfilled" in warning for warning in warnings) and not allow_underfilled
    return StagingReportReadiness(can_generate=not blocking, warnings=warnings)


def staging_report_analytics_readiness(output_dir: Path) -> StagingReportReadiness:
    status = staging_analytics_status(output_dir)
    warnings = tuple(f"{ticker} is missing analytics." for ticker in status.missing_tickers)
    return StagingReportReadiness(can_generate=status.complete, warnings=warnings)


def run_greenrock_staging_report_workflow(
    connection: Connection,
    output_dir: Path,
    allow_underfilled: bool = False,
    allow_missing_analytics: bool = False,
) -> tuple[WorkflowRun, tuple[Artifact, ...], ApprovalRequest | None]:
    readiness = staging_report_readiness(output_dir, allow_underfilled=allow_underfilled)
    if not readiness.can_generate:
        raise ValueError("Staging report is underfilled. Re-run with allow_underfilled=True to draft anyway.")
    analytics_readiness = staging_report_analytics_readiness(output_dir)
    analytics_warnings = analytics_readiness.warnings
    if not analytics_readiness.can_generate and not allow_missing_analytics:
        enrichment = enrich_staged_candidates(output_dir)
        analytics_readiness = staging_report_analytics_readiness(output_dir)
        analytics_warnings = analytics_readiness.warnings + enrichment.errors
        if not analytics_readiness.can_generate:
            raise ValueError(
                "Staging candidates need enrichment. Run atlas greenrock staging enrich or configure market data provider."
            )
    runner = WorkflowRunner(
        connection=connection,
        division="greenrock",
        workflow_name="greenrock.staging-report",
        steps=(
            WorkflowStep("stage_candidates", _stage_candidates_step(output_dir, readiness.warnings + analytics_warnings)),
            WorkflowStep("draft_report", _draft_staging_report),
        ),
        output_dir=output_dir,
        mock_data_used=False,
        data_mode="real",
    )
    execution = runner.run()
    approval = get_approval(connection, execution.approval_id) if execution.approval_id else None
    return execution.run, execution.artifacts, approval


def _stage_candidates_step(source_output_dir: Path, readiness_warnings: tuple[str, ...]):
    def _stage_candidates(context: WorkflowContext) -> None:
        rows = load_staged_candidates(source_output_dir)
        context.greenrock_staging_rows = rows
        context.greenrock_staging_warnings = readiness_warnings
        paths = _write_staging_outputs(rows, context.output_dir)
        context.record_artifact("candidates_csv", paths["all"], output_key="all")
        context.record_artifact("mega_rock_csv", paths["mega_rock"], output_key="mega_rock")
        context.record_artifact("large_cap_csv", paths["large_cap"], output_key="large_cap")
        context.record_artifact("small_cap_csv", paths["small_cap"], output_key="small_cap")

    return _stage_candidates


def _write_staging_outputs(rows: tuple[dict[str, str], ...], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "all": output_dir / "greenrock_staging_candidates.csv",
        "mega_rock": output_dir / "greenrock_staging_mega_rock.csv",
        "large_cap": output_dir / "greenrock_staging_large_cap.csv",
        "small_cap": output_dir / "greenrock_staging_small_mid.csv",
    }
    _write_rows(rows, paths["all"])
    _write_rows(tuple(row for row in rows if row.get("staged_bucket") == MEGA_BUCKET), paths["mega_rock"])
    _write_rows(tuple(row for row in rows if row.get("staged_bucket") == LARGE_BUCKET), paths["large_cap"])
    _write_rows(tuple(row for row in rows if row.get("staged_bucket") == SMALL_MID_BUCKET), paths["small_cap"])
    return paths


def _write_rows(rows: tuple[dict[str, str], ...], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=STAGING_REPORT_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_staging_csv_row(row))


def _staging_csv_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "symbol": row.get("ticker", ""),
        "company_name": row.get("ticker", ""),
        "market_cap_bucket": _market_bucket(row.get("staged_bucket", "")),
        "market_cap": "",
        "score": row.get("greenrock_score", ""),
        "latest_close": "",
        "rsi_14": "",
        "low_proximity": "",
        "volume_avg_10": "",
        "previous_volume_avg_10": "",
        "ema_8": "",
        "sma_10": "",
        "sma_50": "",
        "sma_150": "",
        "ma_roc_50": "",
        "ma_roc_150": "",
        "bollinger_lower": "",
        "bollinger_upper": "",
        "passed_rules": "",
        "failed_rules": "",
        "has_price_history": "",
        "has_market_cap": "",
        "has_volume_data": "",
        "has_52_week_low": "",
        "skipped_reason": "",
        "selection_label": "Staged Candidate",
        "guardrail": row.get("guardrail", ""),
        "quick_ratio": "",
        "net_cash_debt": "",
        "share_change_percent": "",
        "evidence_agreement": row.get("evidence_agreement", ""),
        "top_bullish_signal": row.get("top_bullish_signal", ""),
        "top_caution_signal": row.get("top_caution_signal", ""),
        "note": row.get("notes", ""),
        "confidence": row.get("confidence", ""),
        "research_priority": row.get("research_priority", ""),
        "source_list": row.get("source_list", ""),
        "source_scan_id": row.get("source_scan_id", ""),
        "staging_notes": row.get("notes", ""),
        "staged_bucket": row.get("staged_bucket", ""),
    }


def _draft_staging_report(context: WorkflowContext) -> None:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    rows = getattr(context, "greenrock_staging_rows", ())
    warnings = getattr(context, "greenrock_staging_warnings", ())
    report_markdown = build_staging_report_markdown(context.run.run_id, rows, warnings)
    report_path = context.output_dir / "greenrock_report_draft.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    report_artifact = context.record_artifact("report_draft_md", report_path, output_key="report_draft")
    approval = create_approval_request(
        context.connection,
        artifact_type="report_draft_md",
        artifact_path=str(report_path),
        run_id=context.run.run_id,
        artifact_id=report_artifact.id,
        notes="Staging-sourced draft blocked until explicitly approved by a human.",
    )
    create_report_record(
        context.connection,
        title="GreenRock Analysts Staging-Sourced Monthly Opportunity Report",
        report_type="greenrock_monthly_draft",
        content_path=str(report_path),
        run_id=context.run.run_id,
        artifact_id=report_artifact.id,
        approval_id=approval.id,
        status="blocked_for_approval",
    )
    context.approval_id = approval.id
    context.blocked_for_approval = True


def build_staging_report_markdown(run_id: str, rows: tuple[dict[str, str], ...], warnings: tuple[str, ...] = ()) -> str:
    source_lists = sorted({row.get("source_list", "") for row in rows if row.get("source_list", "")})
    scan_ids = sorted({row.get("source_scan_id", "") for row in rows if row.get("source_scan_id", "")})
    lines = _logo_lines() + [
        "# GreenRock Analysts Monthly Opportunity Report",
        "",
        "## Technical Dislocation Screen",
        "",
        f"**Date:** {date.today().isoformat()}",
        f"**Run ID:** {run_id}",
        "**Data Mode:** REAL",
        "**Selection Mode:** STAGING",
        "**Candidate Source:** Staging-sourced",
        "",
        "> Draft only. This staging-sourced report requires human approval before any client-facing use.",
        "",
        "## Candidate Source Disclosure",
        "",
        (
            "This draft is sourced from GreenRock Report Candidate Staging. Scanner populations do not "
            "automatically feed reports; promoted and staged candidates are the curated bridge into the "
            "approval-gated report workflow."
        ),
        "",
        "- Data mode: REAL",
        "- Selection mode: STAGING",
        "- Source type: Staging-sourced",
    ]
    if source_lists:
        lines.append(f"- Source lists: {', '.join(source_lists)}")
    if scan_ids:
        lines.append(f"- Scan IDs: {', '.join(scan_ids)}")
    lines.extend(
        [
            "",
            "## Readiness",
            "",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- Staging targets are ready.")
    data_warnings = _staging_data_warnings(rows)
    if data_warnings:
        lines.extend(["", "## Staging Data Warnings", ""])
        lines.extend(f"- {warning}" for warning in data_warnings)
    lines.extend(
        [
            "",
            "## Mega Rock Candidate",
            "",
            _staging_table(tuple(row for row in rows if row.get("staged_bucket") == MEGA_BUCKET)),
            _candidate_signal_summaries(tuple(row for row in rows if row.get("staged_bucket") == MEGA_BUCKET)),
            "",
            "## Large Cap Candidates",
            "",
            _staging_table(tuple(row for row in rows if row.get("staged_bucket") == LARGE_BUCKET)),
            _candidate_signal_summaries(tuple(row for row in rows if row.get("staged_bucket") == LARGE_BUCKET)),
            "",
            "## Small/Mid Candidates",
            "",
            _staging_table(tuple(row for row in rows if row.get("staged_bucket") == SMALL_MID_BUCKET)),
            _candidate_signal_summaries(tuple(row for row in rows if row.get("staged_bucket") == SMALL_MID_BUCKET)),
            "",
            "## Research Only / Excluded",
            "",
            _staging_table(tuple(row for row in rows if row.get("staged_bucket") not in {MEGA_BUCKET, LARGE_BUCKET, SMALL_MID_BUCKET})),
            _candidate_signal_summaries(tuple(row for row in rows if row.get("staged_bucket") not in {MEGA_BUCKET, LARGE_BUCKET, SMALL_MID_BUCKET})),
            "",
            "## Human Approval Disclaimer",
            "",
            (
                "This draft is blocked from publication, email distribution, PDF export, or any client-facing "
                "use until a human approver explicitly approves the linked Atlas OS approval record."
            ),
            "",
            "## Compliance Notes",
            "",
            (
                "This material is for internal workflow testing only. It does not provide personalized "
                "investment advice, guarantee outcomes, or recommend that any person buy, sell, or hold a security."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _staging_table(rows: tuple[dict[str, str], ...]) -> str:
    if not rows:
        return "No staged candidates in this bucket."
    lines = [
        "| Ticker | Score | Confidence | Evidence | Guardrail | Priority | Source | Notes |",
        "|---|---:|---:|---:|---|---|---|---|",
    ]
    for row in rows:
        source = row.get("source_list", "") or "local staging"
        if row.get("source_scan_id"):
            source = f"{source} / {row.get('source_scan_id')}"
        lines.append(
            "| "
            f"{row.get('ticker', '')} | "
            f"{row.get('greenrock_score', '') or '-'} | "
            f"{row.get('confidence', '') or '-'} | "
            f"{row.get('evidence_agreement', '') or '-'} | "
            f"{row.get('guardrail', '') or '-'} | "
            f"{row.get('research_priority', '') or '-'} | "
            f"{source} | "
            f"{row.get('notes', '') or '-'} |"
        )
    return "\n".join(lines)


def _candidate_signal_summaries(rows: tuple[dict[str, str], ...]) -> str:
    if not rows:
        return ""
    lines = ["", "### Candidate Evidence Notes", ""]
    for row in rows:
        bullish = row.get("top_bullish_signal", "") or "No top bullish signal recorded."
        caution = row.get("top_caution_signal", "") or "No top caution signal recorded."
        lines.extend(
            [
                f"**{row.get('ticker', '')}**",
                f"- Top bullish signal: {bullish}",
                f"- Top caution signal: {caution}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def _staging_data_warnings(rows: tuple[dict[str, str], ...]) -> tuple[str, ...]:
    warnings = []
    for row in rows:
        missing = [field.replace("_", " ") for field in STAGING_ANALYTIC_FIELDS if not row.get(field, "").strip()]
        if row_missing_analytics(row):
            warnings.append(f"{row.get('ticker', '')}: missing {', '.join(missing)}.")
    return tuple(warnings)


def _logo_lines() -> list[str]:
    logo_path = greenrock_logo_path()
    return [f"![GreenRock Analysts Logo]({logo_path})", ""] if logo_path else []


def _market_bucket(staged_bucket: str) -> str:
    return {
        MEGA_BUCKET: "mega_rock",
        LARGE_BUCKET: "large_cap",
        SMALL_MID_BUCKET: "small_cap",
    }.get(staged_bucket, "research")
