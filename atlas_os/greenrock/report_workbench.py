"""GreenRock report workbench readiness and agent task flow."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.agents.tasks import AgentTask, list_agent_tasks, upsert_agent_task
from atlas_os.core.approvals import ApprovalStatus, list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.reports import list_reports
from atlas_os.daily import latest_daily_brief
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.memory import memory_movers
from atlas_os.greenrock.scanner import latest_scan
from atlas_os.greenrock.staging import load_staged_candidates, staging_analytics_status, staging_readiness
from atlas_os.greenrock.staging_report import staging_report_readiness
from atlas_os.inbox import create_inbox_item


READINESS_STATES = (
    "Not Ready",
    "Needs Review",
    "Ready to Draft",
    "Draft Awaiting Approval",
    "Approved, PDF Ready",
    "Final PDF Complete",
)
DEFAULT_STALE_HOURS = 24.0


def report_readiness(output_dir: Path, db_path: Path, stale_hours: float = DEFAULT_STALE_HOURS) -> dict:
    db = initialize_database(db_path)
    scan = latest_scan(output_dir)
    daily = latest_daily_brief(output_dir)
    staged = load_staged_candidates(output_dir)
    readiness = staging_readiness(output_dir)
    report_ready = staging_report_readiness(output_dir, allow_underfilled=False)
    analytics = staging_analytics_status(output_dir)
    with connect(db) as connection:
        approvals = list_approvals(connection)
        reports = list_reports(connection)
        artifacts = list_artifacts(connection)
    latest_report = reports[0] if reports else None
    pending = tuple(approval for approval in approvals if approval.status == ApprovalStatus.PENDING)
    approved = tuple(approval for approval in approvals if approval.status == ApprovalStatus.APPROVED)
    exported_run_ids = {artifact.run_id for artifact in artifacts if artifact.artifact_type == "report_final_pdf"}
    latest_pdf_exported = bool(latest_report and latest_report.run_id in exported_run_ids)
    latest_approved = next((approval for approval in approved if latest_report and approval.run_id == latest_report.run_id), None)
    approved_pdf_ready = latest_approved is not None and not latest_pdf_exported
    scan_age = _scan_age_hours(scan)
    stale = scan is not None and scan_age is not None and scan_age > stale_hours
    duplicate_count = _duplicate_count(staged)
    reasons = []
    if not scan:
        reasons.append("no latest successful scan")
    if stale:
        reasons.append("scan stale")
    if scan and scan.provider_failure_count:
        reasons.append("provider failures")
    underfilled = [item for item in readiness if item.status == "Underfilled"]
    if underfilled:
        reasons.append("staging underfilled")
    if analytics.missing_count:
        reasons.append("analytics missing")
    if duplicate_count:
        reasons.append("duplicate tickers")
    if pending:
        reasons.append("pending approval")
    if approved_pdf_ready:
        reasons.append("approved but PDF missing")
    if not _logos_present():
        reasons.append("missing logos")
    state = "Needs Review"
    if latest_pdf_exported:
        state = "Final PDF Complete"
    elif approved_pdf_ready:
        state = "Approved, PDF Ready"
    elif pending or (latest_report and latest_report.status in {"draft", "pending", "awaiting_approval"}):
        state = "Draft Awaiting Approval"
    elif scan and report_ready.can_generate and analytics.complete and not scan.provider_failure_count and not stale and not duplicate_count:
        state = "Ready to Draft"
    elif not scan or not staged:
        state = "Not Ready"
    next_action = _next_action(state, reasons, analytics.missing_count)
    return {
        "state": state,
        "reasons": reasons,
        "next_operator_action": next_action,
        "latest_scan_id": scan.scan_id if scan else "none",
        "scan_age_hours": None if scan_age is None else round(scan_age, 2),
        "scan_stale": stale,
        "stale_threshold_hours": stale_hours,
        "market_pulse_status": "available" if scan and scan.rows else "missing",
        "daily_id": daily.get("daily_id", "none") if daily else "none",
        "daily_status": "available" if daily else "missing",
        "staged_count": len(staged),
        "readiness_buckets": [asdict(item) for item in readiness],
        "analytics_complete": analytics.complete,
        "missing_analytics": analytics.missing_count,
        "provider_failures": scan.provider_failure_count if scan else 0,
        "duplicate_tickers": duplicate_count,
        "pending_approval_id": pending[0].id if pending else None,
        "pending_approvals": len(pending),
        "approved_approval_id": latest_approved.id if latest_approved else None,
        "latest_report_run_id": latest_report.run_id if latest_report else None,
        "latest_report_status": latest_report.status if latest_report else "none",
        "approved_pdf_ready": approved_pdf_ready,
        "final_pdf_complete": latest_pdf_exported,
        "pdf_status": "exported" if latest_pdf_exported else ("approved_pdf_ready" if approved_pdf_ready else "not_ready"),
        "logos_present": _logos_present(),
        "source_disclosure": "approval-gated local GreenRock report workflow",
    }


def report_workbench_summary(output_dir: Path, db_path: Path, create_tasks: bool = True) -> dict:
    readiness = report_readiness(output_dir, db_path)
    tasks = create_report_task_chain(output_dir, db_path, readiness) if create_tasks else list_agent_tasks(output_dir)
    material_items = _create_material_inbox_items(output_dir, readiness, tasks) if create_tasks else ()
    return {
        "readiness": readiness,
        "tasks": [asdict(task) for task in tasks],
        "material_inbox_items": [item.__dict__ for item in material_items],
    }


def create_report_task_chain(output_dir: Path, db_path: Path, readiness: dict | None = None) -> tuple[AgentTask, ...]:
    readiness = readiness or report_readiness(output_dir, db_path)
    scan = latest_scan(output_dir)
    staged = load_staged_candidates(output_dir)
    movers = memory_movers(output_dir)
    tasks = [
        upsert_agent_task(
            output_dir,
            "market",
            "Verify latest Market Pulse scan",
            "completed" if scan else "blocked",
            "Check latest successful scan and stale threshold.",
            f"Scan {readiness['latest_scan_id']} status {readiness['market_pulse_status']}; stale={readiness['scan_stale']}.",
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            target_url="/greenrock/market-pulse",
            operator_action_required="Run fresh scan" if readiness["scan_stale"] or not scan else "",
        ),
        upsert_agent_task(
            output_dir,
            "evidence",
            "Review Market Pulse evidence",
            "completed" if scan else "blocked",
            "Identify archetype leaders and weak evidence candidates.",
            _evidence_summary(scan),
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            target_url="/greenrock/market-pulse",
            operator_action_required="Review Market Pulse leaders" if scan else "Run Market Pulse scan",
        ),
        upsert_agent_task(
            output_dir,
            "memory",
            "Identify report-relevant movers",
            "completed" if any(movers[key] for key in movers) else "blocked",
            "Review rank, score, confidence, and evidence movers relevant to report candidates.",
            _memory_summary(movers),
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            target_url="/greenrock/memory/movers",
            operator_action_required="Review Memory movers" if any(movers[key] for key in movers) else "",
        ),
        upsert_agent_task(
            output_dir,
            "fundamental",
            "Review report candidate guardrails",
            "completed" if staged else "blocked",
            "Flag Red Flag guardrails and strongest balance-sheet support among report candidates.",
            _fundamental_summary(staged),
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            target_url="/greenrock/staging",
            operator_action_required="Resolve Red Flag candidates" if "Red Flag" in _fundamental_summary(staged) else "",
        ),
        upsert_agent_task(
            output_dir,
            "report",
            "Prepare approval-gated report workflow",
            "completed" if readiness["state"] in {"Ready to Draft", "Draft Awaiting Approval", "Approved, PDF Ready", "Final PDF Complete"} else "blocked",
            "Create recommended Analyst Slate, check staging readiness, and recommend draft generation only when ready.",
            f"Readiness: {readiness['state']}; staged {readiness['staged_count']}; missing analytics {readiness['missing_analytics']}.",
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            related_report_run_id=readiness["latest_report_run_id"],
            related_approval_id=readiness["pending_approval_id"],
            target_url="/greenrock/report-workbench",
            operator_action_required=readiness["next_operator_action"],
        ),
        upsert_agent_task(
            output_dir,
            "qa",
            "Run pre-report quality checklist",
            "completed" if not _qa_blockers(readiness) else "blocked",
            "Check missing analytics, underfilled buckets, provider failures, stale scan, duplicates, logos, source disclosure, and approval state.",
            "; ".join(readiness["reasons"]) or "No QA blockers detected.",
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            related_report_run_id=readiness["latest_report_run_id"],
            related_approval_id=readiness["pending_approval_id"],
            target_url="/greenrock/report-workbench",
            operator_action_required="Clear QA blockers" if _qa_blockers(readiness) else "",
        ),
        upsert_agent_task(
            output_dir,
            "inbox",
            "Create material report operator actions",
            "completed",
            "Create only material Inbox actions for the report workflow.",
            readiness["next_operator_action"],
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_daily_id=None if readiness["daily_id"] == "none" else readiness["daily_id"],
            related_report_run_id=readiness["latest_report_run_id"],
            related_approval_id=readiness["pending_approval_id"],
            target_url="/atlas/inbox",
            operator_action_required=readiness["next_operator_action"],
        ),
    ]
    return tuple(tasks)


def get_report_task(output_dir: Path, task_id: str) -> AgentTask:
    from atlas_os.agents.tasks import get_agent_task

    return get_agent_task(output_dir, task_id)


def list_report_tasks(output_dir: Path) -> tuple[AgentTask, ...]:
    return tuple(task for task in list_agent_tasks(output_dir) if task.agent_id in {"market", "evidence", "memory", "fundamental", "report", "qa", "inbox"})


def _create_material_inbox_items(output_dir: Path, readiness: dict, tasks: tuple[AgentTask, ...]):
    task = next((item for item in tasks if item.agent_id == "inbox"), None)
    action = readiness["next_operator_action"]
    severity = "action" if readiness["state"] in {"Ready to Draft", "Draft Awaiting Approval", "Approved, PDF Ready"} else "warning"
    if readiness["state"] == "Final PDF Complete":
        return ()
    return (
        create_inbox_item(
            output_dir,
            "report-workbench",
            severity,
            action,
            f"GreenRock report readiness is {readiness['state']}: {', '.join(readiness['reasons']) or 'ready'}.",
            _target_for_action(action, readiness),
            related_agent_run_id=task.task_id if task else None,
            related_cycle_id=readiness["daily_id"] if readiness["daily_id"] != "none" else None,
            related_scan_id=None if readiness["latest_scan_id"] == "none" else readiness["latest_scan_id"],
            related_report_run_id=readiness["latest_report_run_id"],
            related_approval_id=readiness["pending_approval_id"],
            created_reason="GreenRock Report Workbench material readiness action.",
        ),
    )


def _next_action(state: str, reasons: list[str], missing_analytics: int) -> str:
    if state == "Not Ready":
        return "Stage analyst slate" if "no latest successful scan" not in reasons else "Run Daily Intelligence Cycle"
    if state == "Ready to Draft":
        return "Generate draft"
    if state == "Draft Awaiting Approval":
        return "Review draft"
    if state == "Approved, PDF Ready":
        return "Export approved PDF"
    if state == "Final PDF Complete":
        return "Open final reports"
    if missing_analytics:
        return "Enrich staged candidates"
    if "staging underfilled" in reasons:
        return "Stage analyst slate"
    if "pending approval" in reasons:
        return "Review pending approvals"
    return "Run Daily Intelligence Cycle"


def _target_for_action(action: str, readiness: dict) -> str:
    if action == "Generate draft":
        return "/greenrock/staging/generate/confirm"
    if action == "Review draft" and readiness["latest_report_run_id"]:
        return f"/greenrock/reports/{readiness['latest_report_run_id']}/review"
    if action == "Review pending approvals":
        return "/greenrock"
    if action == "Export approved PDF":
        return "/greenrock/final-reports"
    if action == "Open final reports":
        return "/greenrock/final-reports"
    if action == "Enrich staged candidates":
        return "/greenrock/staging"
    if action == "Stage analyst slate":
        return "/greenrock/market-pulse/stage/confirm?slate=analyst"
    return "/greenrock/report-workbench"


def _scan_age_hours(scan) -> float | None:
    if not scan:
        return None
    try:
        modified = datetime.fromtimestamp(Path(scan.results_path).stat().st_mtime, timezone.utc)
    except OSError:
        return None
    return (datetime.now(timezone.utc) - modified).total_seconds() / 3600


def _duplicate_count(rows: tuple[dict[str, str], ...]) -> int:
    seen = set()
    count = 0
    for row in rows:
        ticker = row.get("ticker", row.get("symbol", ""))
        if ticker in seen:
            count += 1
        seen.add(ticker)
    return count


def _logos_present() -> bool:
    static_dir = Path(__file__).resolve().parents[1] / "static"
    return (static_dir / "atlas_logo.png").exists() and (static_dir / "greenrock_logo.png").exists()


def _qa_blockers(readiness: dict) -> bool:
    blockers = {"scan stale", "analytics missing", "provider failures", "duplicate tickers", "missing logos"}
    return bool(blockers & set(readiness["reasons"]))


def _evidence_summary(scan) -> str:
    if not scan:
        return "No Market Pulse evidence available."
    leaders = []
    seen = set()
    weak = []
    for row in scan.rows:
        archetype = row.get("market_archetype", "")
        if archetype and archetype not in seen:
            seen.add(archetype)
            leaders.append(f"{archetype}: {row.get('symbol', '')}")
        try:
            evidence = float(row.get("evidence_agreement", 0))
        except ValueError:
            evidence = 0.0
        if evidence < 50:
            weak.append(row.get("symbol", ""))
    return f"Leaders: {', '.join(leaders[:5]) or 'none'}. Weak evidence: {', '.join(weak[:5]) or 'none'}."


def _memory_summary(movers) -> str:
    parts = []
    for key, label in (("rank_improvers", "rank"), ("score_improvers", "score"), ("confidence_improvers", "confidence")):
        if movers[key]:
            parts.append(f"{label}: {movers[key][0].ticker}")
    return "; ".join(parts) or "No report-relevant movers found."


def _fundamental_summary(staged: tuple[dict[str, str], ...]) -> str:
    red = [row.get("ticker", "") for row in staged if row.get("guardrail") == "Red Flag"]
    strong = [row.get("ticker", "") for row in staged if row.get("guardrail") == "Strong Balance Sheet"]
    return f"Red Flag: {', '.join(red[:5]) or 'none'}. Strong Balance Sheet: {', '.join(strong[:5]) or 'none'}."
