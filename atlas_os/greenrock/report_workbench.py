"""GreenRock report workbench readiness and agent task flow."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import json
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.agents.tasks import AgentTask, list_agent_tasks, upsert_agent_task
from atlas_os.core.approvals import ApprovalStatus, list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.reports import list_reports
from atlas_os.daily import latest_daily_brief
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.analyst import analyst_candidates, archetype_leaders, remaining_candidates
from atlas_os.greenrock.market_engine import MARKET_ARCHETYPES
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
CANDIDATE_DECISIONS = ("accepted", "deferred", "research", "excluded")


@dataclass(frozen=True)
class CandidateDecision:
    ticker: str
    decision: str
    timestamp: str
    note: str = ""
    related_scan_id: str = ""
    related_daily_id: str = ""
    related_report_run_id: str = ""


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
    timeline = report_production_timeline(readiness)
    candidate_review = report_candidate_review(output_dir, readiness)
    return {
        "readiness": readiness,
        "tasks": [asdict(task) for task in tasks],
        "material_inbox_items": [item.__dict__ for item in material_items],
        "timeline": timeline,
        "candidate_review": candidate_review,
        "candidate_decisions": [asdict(item) for item in list_candidate_decisions(output_dir)],
    }


def report_production_timeline(readiness: dict) -> list[dict[str, str]]:
    scan_ready = readiness["market_pulse_status"] == "available"
    slate_ready = readiness["staged_count"] > 0
    analytics_ready = readiness["analytics_complete"] and slate_ready
    qa_blocked = bool(_qa_blockers(readiness))
    draft_ready = readiness["latest_report_run_id"] is not None
    approval_ready = readiness["pending_approvals"] > 0 or readiness["approved_approval_id"] is not None
    pdf_ready = readiness["final_pdf_complete"] or readiness["approved_pdf_ready"]
    return [
        _stage("Market Data", "Market Agent", _stage_status(scan_ready, blocked="no latest successful scan" in readiness["reasons"]), readiness["latest_scan_id"], _timestamp_or_age(readiness), _stage_blocker(readiness, ("no latest successful scan", "scan stale", "provider failures")), "Run Daily Intelligence Cycle" if not scan_ready else "Review Market Pulse", "/greenrock/market-pulse"),
        _stage("Analyst Slate", "Evidence Agent", _stage_status(slate_ready, blocked=scan_ready and not slate_ready), readiness["latest_scan_id"], "", "staging underfilled" if "staging underfilled" in readiness["reasons"] else "", "Stage Analyst Slate", "/greenrock/market-pulse/stage/confirm?slate=analyst"),
        _stage("Candidate Review", "Report Agent", "Needs Review" if slate_ready else "Waiting", str(readiness["staged_count"]), "", "Human candidate decisions are local editorial notes only.", "Review Candidate Decisions", "/greenrock/report-workbench#candidate-review"),
        _stage("Analytics Readiness", "Fundamental Agent", "Ready" if analytics_ready else ("Blocked" if slate_ready else "Waiting"), str(readiness["missing_analytics"]), "", "analytics missing" if readiness["missing_analytics"] else "", "Enrich Missing Analytics", "/greenrock/staging"),
        _stage("Draft Report", "Report Agent", "Complete" if draft_ready else ("Ready" if readiness["state"] == "Ready to Draft" else "Waiting"), readiness.get("latest_report_run_id") or "none", "", "", "Generate Draft", "/greenrock/staging/generate/confirm"),
        _stage("QA Review", "QA Agent", "Blocked" if qa_blocked else ("Ready" if slate_ready else "Waiting"), "; ".join(readiness["reasons"]) or "clear", "", "; ".join(_qa_reason_list(readiness)), "Clear QA blockers" if qa_blocked else "Open Workbench", "/greenrock/report-workbench"),
        _stage("Human Approval", "Inbox Agent", "Awaiting Approval" if readiness["pending_approvals"] else ("Approved" if readiness["approved_approval_id"] else "Waiting"), str(readiness.get("pending_approval_id") or readiness.get("approved_approval_id") or "none"), "", "approval gate intact", "Review Approval", _approval_target(readiness)),
        _stage("PDF Export", "Report Agent", "Complete" if readiness["final_pdf_complete"] else ("Ready" if pdf_ready else "Waiting"), readiness["pdf_status"], "", "PDF export blocked before approval" if not pdf_ready else "", "Export Approved PDF", "/greenrock/final-reports"),
        _stage("Final Report", "QA Agent", "Complete" if readiness["final_pdf_complete"] else "Waiting", readiness["pdf_status"], "", "", "Open Final Reports", "/greenrock/final-reports"),
    ]


def report_candidate_review(output_dir: Path, readiness: dict | None = None) -> dict:
    readiness = readiness or report_readiness(output_dir, Path(""))
    staged = load_staged_candidates(output_dir)
    decisions = {item.ticker: item for item in list_candidate_decisions(output_dir)}
    candidates = analyst_candidates(output_dir, staged)
    leaders = archetype_leaders(candidates)
    remaining = remaining_candidates(candidates, leaders)
    by_archetype = {leader.archetype: leader for leader in leaders}
    featured = []
    for archetype in MARKET_ARCHETYPES:
        candidate = by_archetype.get(archetype)
        featured.append(_candidate_payload(candidate, archetype, decisions, staged) if candidate else {"archetype": archetype, "ticker": "", "status": "missing"})
    return {
        "featured": featured,
        "remaining": [_candidate_payload(candidate, candidate.archetype, decisions, staged) for candidate in remaining],
        "staged_count": len(staged),
        "latest_scan_id": readiness.get("latest_scan_id", "none"),
    }


def record_candidate_decision(
    output_dir: Path,
    ticker: str,
    decision: str,
    note: str = "",
    related_scan_id: str = "",
    related_daily_id: str = "",
    related_report_run_id: str = "",
) -> CandidateDecision:
    normalized = ticker.strip().upper()
    normalized_decision = decision.strip().lower()
    if not normalized:
        raise ValueError("ticker is required")
    if normalized_decision not in CANDIDATE_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(CANDIDATE_DECISIONS)}")
    record = CandidateDecision(
        ticker=normalized,
        decision=normalized_decision,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        note=note.strip(),
        related_scan_id=related_scan_id,
        related_daily_id=related_daily_id,
        related_report_run_id=related_report_run_id,
    )
    rows = [asdict(item) for item in list_candidate_decisions(output_dir) if item.ticker != normalized]
    rows.insert(0, asdict(record))
    _candidate_decisions_path(output_dir).parent.mkdir(parents=True, exist_ok=True)
    _candidate_decisions_path(output_dir).write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def list_candidate_decisions(output_dir: Path) -> tuple[CandidateDecision, ...]:
    path = _candidate_decisions_path(output_dir)
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ()
    rows = []
    for item in payload:
        rows.append(
            CandidateDecision(
                ticker=str(item.get("ticker", "")).upper(),
                decision=str(item.get("decision", "")),
                timestamp=str(item.get("timestamp", "")),
                note=str(item.get("note", "")),
                related_scan_id=str(item.get("related_scan_id", "")),
                related_daily_id=str(item.get("related_daily_id", "")),
                related_report_run_id=str(item.get("related_report_run_id", "")),
            )
        )
    return tuple(rows)


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


def _stage(name: str, agent: str, status: str, source: str, timestamp: str, blocker: str, action: str, target_url: str) -> dict[str, str]:
    return {
        "name": name,
        "agent": agent,
        "status": status,
        "source": source or "local records",
        "timestamp": timestamp or "",
        "blocking_reason": blocker or "",
        "next_action": action,
        "target_url": target_url,
    }


def _stage_status(ready: bool, blocked: bool = False) -> str:
    if ready:
        return "Complete"
    if blocked:
        return "Blocked"
    return "Waiting"


def _timestamp_or_age(readiness: dict) -> str:
    age = readiness.get("scan_age_hours")
    if age is None:
        return ""
    return f"age {age}h"


def _stage_blocker(readiness: dict, keys: tuple[str, ...]) -> str:
    return "; ".join(reason for reason in readiness["reasons"] if reason in keys)


def _qa_reason_list(readiness: dict) -> list[str]:
    blockers = {"scan stale", "analytics missing", "provider failures", "duplicate tickers", "missing logos"}
    return [reason for reason in readiness["reasons"] if reason in blockers]


def _approval_target(readiness: dict) -> str:
    if readiness.get("pending_approval_id"):
        return f"/approvals/{readiness['pending_approval_id']}"
    return "/greenrock"


def _candidate_payload(candidate, archetype: str, decisions: dict[str, CandidateDecision], staged: tuple[dict[str, str], ...]) -> dict[str, str]:
    decision = decisions.get(candidate.ticker)
    staged_row = next((row for row in staged if row.get("ticker", "").upper() == candidate.ticker), {})
    prior = candidate.prior
    return {
        "archetype": archetype,
        "ticker": candidate.ticker,
        "rank": candidate.rank,
        "score": candidate.greenrock_score,
        "confidence": candidate.confidence,
        "evidence_agreement": candidate.evidence_agreement,
        "research_priority": candidate.research_priority,
        "rank_movement": _movement_value(prior.rank_change if prior else None),
        "score_movement": _movement_value(prior.score_change if prior else None),
        "confidence_movement": _movement_value(prior.confidence_change if prior else None),
        "primary_bullish_evidence": candidate.top_bullish_signal or candidate.bullish_evidence,
        "primary_caution": candidate.top_caution_signal or candidate.bearish_evidence,
        "guardrail": candidate.guardrail,
        "staging_status": staged_row.get("staged_bucket", candidate.staged_bucket or "staged"),
        "decision": decision.decision if decision else "",
        "decision_timestamp": decision.timestamp if decision else "",
        "decision_note": decision.note if decision else "",
        "source_scan_id": candidate.source_scan_id,
        "status": "available",
    }


def _movement_value(value) -> str:
    if value is None:
        return "n/a"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric == 0:
        return "0"
    prefix = "+" if numeric > 0 else ""
    return f"{prefix}{numeric:g}"


def _candidate_decisions_path(output_dir: Path) -> Path:
    return output_dir / "greenrock" / "candidate_decisions.json"


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
