"""Safe local Atlas agent orchestration."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.agents.base import Agent, AgentRun
from atlas_os.agents.registry import get_agent, list_agents
from atlas_os.core.approvals import ApprovalStatus, list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.reports import list_reports
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.memory import load_memory_rows, memory_movers
from atlas_os.greenrock.population import ALL_POPULATION
from atlas_os.greenrock.scanner import latest_scan, run_population_scan
from atlas_os.greenrock.staging import staging_analytics_status, staging_readiness
from atlas_os.greenrock.staging_report import staging_report_readiness
from atlas_os.greenrock.universe_manager import default_universe_manager
from atlas_os.inbox import InboxItem, create_inbox_item, list_inbox_items


AGENT_ROOT = Path("agents")
RUNS_ROOT = AGENT_ROOT / "runs"
CYCLES_ROOT = AGENT_ROOT / "cycles"
STATE_FILE = "agent_state.json"
ORDERED_AGENT_IDS = ("market", "evidence", "fundamental", "memory", "report", "qa", "inbox")
MARKET_SCAN_POLICIES = {"use_latest_scan", "run_fresh_scan", "run_if_stale"}
DEFAULT_MARKET_SCAN_POLICY = "use_latest_scan"
DEFAULT_STALE_HOURS = 24.0


def agent_output_dir(output_dir: Path) -> Path:
    return Path(output_dir) / AGENT_ROOT


def run_agent_cycle(
    output_dir: Path,
    db_path: Path,
    market_scan_policy: str = DEFAULT_MARKET_SCAN_POLICY,
    stale_hours: float = DEFAULT_STALE_HOURS,
) -> tuple[AgentRun, ...]:
    """Run all local agents sequentially, producing local records only."""
    initialize_database(db_path)
    policy = _normalize_market_scan_policy(market_scan_policy)
    threshold = _normalize_stale_hours(stale_hours)
    cycle_context = {"market_scan_policy": policy, "stale_hours": threshold}
    prior_outputs: dict[str, dict] = {}
    prior_run_refs: dict[str, AgentRun] = {}
    runs: list[AgentRun] = []
    cycle_id = _now().replace("+00:00", "Z").replace(":", "").replace("-", "")
    started_at = _now()
    before_items = list_inbox_items(output_dir, include_closed=True)
    prior_cycle = latest_agent_cycle_summary(output_dir)
    for agent_id in ORDERED_AGENT_IDS:
        run = _run_one_agent(output_dir, db_path, agent_id, prior_outputs, prior_run_refs, cycle_id, cycle_context)
        prior_outputs[agent_id] = run.outputs
        prior_run_refs[agent_id] = run
        runs.append(run)
    _write_agent_state(output_dir, tuple(runs))
    _write_cycle_summary(output_dir, cycle_id, started_at, _now(), tuple(runs), before_items, prior_cycle, cycle_context)
    return tuple(runs)


def list_agent_states(output_dir: Path) -> tuple[Agent, ...]:
    state_path = agent_output_dir(output_dir) / STATE_FILE
    if not state_path.exists():
        return list_agents()
    try:
        raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list_agents()
    by_id = {item.get("agent_id"): item for item in raw_state if isinstance(item, dict)}
    states = []
    for agent in list_agents():
        item = by_id.get(agent.agent_id)
        states.append(_agent_from_state(agent, item) if item else agent)
    return tuple(states)


def list_agent_runs(output_dir: Path) -> tuple[AgentRun, ...]:
    directory = agent_output_dir(output_dir) / "runs"
    if not directory.exists():
        return ()
    runs = []
    for path in directory.glob("*.json"):
        try:
            runs.append(_run_from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return tuple(sorted(runs, key=lambda run: run.started_at, reverse=True))


def latest_agent_cycle(output_dir: Path) -> tuple[AgentRun, ...]:
    runs = list_agent_runs(output_dir)
    if not runs:
        return ()
    cycle_prefix = runs[0].run_id.rsplit("-", 1)[0]
    return tuple(sorted((run for run in runs if run.run_id.startswith(cycle_prefix)), key=lambda run: ORDERED_AGENT_IDS.index(run.agent_id)))


def list_agent_cycle_summaries(output_dir: Path) -> tuple[dict, ...]:
    directory = agent_output_dir(output_dir) / "cycles"
    if not directory.exists():
        return ()
    summaries = []
    for path in directory.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        item["path"] = str(path)
        summaries.append(item)
    return tuple(sorted(summaries, key=lambda item: item.get("started_at", ""), reverse=True))


def latest_agent_cycle_summary(output_dir: Path) -> dict | None:
    summaries = list_agent_cycle_summaries(output_dir)
    return summaries[0] if summaries else None


def get_agent_cycle_summary(output_dir: Path, cycle_id: str) -> dict:
    clean = cycle_id.strip().removesuffix(".json")
    if not clean or "/" in clean or ".." in clean:
        raise KeyError(cycle_id)
    path = agent_output_dir(output_dir) / "cycles" / f"{clean}.json"
    if not path.exists():
        raise KeyError(cycle_id)
    item = json.loads(path.read_text(encoding="utf-8"))
    item["path"] = str(path)
    return item


def get_agent_run(output_dir: Path, run_id: str) -> AgentRun:
    clean = run_id.strip().removesuffix(".json")
    if not clean or "/" in clean or ".." in clean:
        raise KeyError(run_id)
    path = agent_output_dir(output_dir) / "runs" / f"{clean}.json"
    if not path.exists():
        raise KeyError(run_id)
    return _run_from_dict(json.loads(path.read_text(encoding="utf-8")))


def agent_cycle_summary(output_dir: Path) -> dict:
    summary = latest_agent_cycle_summary(output_dir)
    if summary:
        summary = dict(summary)
        summary["runs"] = latest_agent_cycle(output_dir)
        return summary
    cycle = latest_agent_cycle(output_dir)
    inbox_items = list_inbox_items(output_dir)
    return {
        "cycle_id": "none",
        "last_run": max((run.completed_at or run.started_at for run in cycle), default="none"),
        "started_at": "none",
        "completed_at": "none",
        "completed": sum(1 for run in cycle if run.status == "completed"),
        "failed": sum(1 for run in cycle if run.status == "failed"),
        "blocked": sum(1 for run in cycle if run.status == "blocked"),
        "inbox_items_generated": sum(int(run.outputs.get("items_created", 0)) for run in cycle if run.agent_id == "inbox"),
        "open_inbox_items": len(inbox_items),
        "warnings": [],
        "top_operator_actions": [item.title for item in inbox_items[:5]],
        "diff": {},
        "runs": cycle,
    }


def _run_one_agent(
    output_dir: Path,
    db_path: Path,
    agent_id: str,
    prior_outputs: dict[str, dict],
    prior_run_refs: dict[str, AgentRun],
    cycle_id: str,
    cycle_context: dict,
) -> AgentRun:
    started_at = _now()
    run_id = f"agent-cycle-{cycle_id}-{agent_id}"
    inputs = {"safe_local_mode": True, "prior_agents": tuple(prior_outputs.keys()), **cycle_context}
    try:
        outputs, warnings, related = _agent_outputs(output_dir, db_path, agent_id, prior_outputs, prior_run_refs, run_id, cycle_id, cycle_context)
        status = "completed" if not outputs.get("blocked") else "blocked"
        errors: tuple[str, ...] = ()
    except Exception as error:
        outputs = {"summary": f"{get_agent(agent_id).name} failed.", "blocked": True}
        warnings = ()
        related = {}
        status = "failed"
        errors = (str(error),)
    run = AgentRun(
        run_id=run_id,
        agent_id=agent_id,
        started_at=started_at,
        completed_at=_now(),
        status=status,
        inputs=inputs,
        outputs=outputs,
        warnings=tuple(warnings),
        errors=tuple(errors),
        related_scan_id=related.get("related_scan_id"),
        related_report_run_id=related.get("related_report_run_id"),
        related_approval_id=related.get("related_approval_id"),
    )
    _write_run(output_dir, run)
    return run


def _agent_outputs(
    output_dir: Path,
    db_path: Path,
    agent_id: str,
    prior_outputs: dict[str, dict],
    prior_run_refs: dict[str, AgentRun],
    run_id: str,
    cycle_id: str,
    cycle_context: dict,
) -> tuple[dict, tuple[str, ...], dict]:
    if agent_id == "market":
        return _market_agent(output_dir, cycle_context)
    if agent_id == "evidence":
        return _evidence_agent(output_dir)
    if agent_id == "fundamental":
        return _fundamental_agent(output_dir)
    if agent_id == "memory":
        return _memory_agent(output_dir)
    if agent_id == "report":
        return _report_agent(output_dir, db_path)
    if agent_id == "qa":
        return _qa_agent(output_dir, db_path)
    if agent_id == "inbox":
        return _inbox_agent(output_dir, prior_outputs, prior_run_refs, run_id, cycle_id)
    raise KeyError(agent_id)


def _market_agent(output_dir: Path, cycle_context: dict) -> tuple[dict, tuple[str, ...], dict]:
    policy = _normalize_market_scan_policy(str(cycle_context.get("market_scan_policy", DEFAULT_MARKET_SCAN_POLICY)))
    stale_hours = _normalize_stale_hours(float(cycle_context.get("stale_hours", DEFAULT_STALE_HOURS)))
    scan = latest_scan(output_dir)
    scan_age_hours = _scan_age_hours(scan)
    fresh_data_pulled = False
    reason = _market_policy_reason(policy, scan, scan_age_hours, stale_hours)
    warnings: tuple[str, ...] = ()
    if policy == "run_fresh_scan" or (policy == "run_if_stale" and _scan_is_stale(scan, scan_age_hours, stale_hours)):
        scan = run_population_scan(output_dir, ALL_POPULATION)
        scan_age_hours = _scan_age_hours(scan)
        fresh_data_pulled = True
        reason = "fresh scan completed by policy"
    master = default_universe_manager(output_dir).master_universe()
    provider_status = "ready" if scan and scan.rows else "no latest scan"
    outputs = {
        "market_scan_policy": policy,
        "fresh_data_pulled": fresh_data_pulled,
        "scan_age_hours": None if scan_age_hours is None else round(scan_age_hours, 2),
        "stale_threshold_hours": stale_hours,
        "reason": reason,
        "provider_status": provider_status,
        "latest_scan_id": scan.scan_id if scan else "none",
        "universe_size": master.size,
        "scored_count": len(scan.rows) if scan else 0,
        "skipped_count": scan.skipped_ticker_count if scan else 0,
        "provider_failures": scan.provider_failure_count if scan else 0,
        "summary": f"{len(scan.rows) if scan else 0} scored across {master.size} universe names.",
    }
    warnings = tuple(scan.warnings if scan else ("No latest Market Pulse scan found.",))
    return outputs, warnings, {"related_scan_id": scan.scan_id if scan else None}


def _evidence_agent(output_dir: Path) -> tuple[dict, tuple[str, ...], dict]:
    movers = memory_movers(output_dir)
    scan = latest_scan(output_dir)
    outputs = {
        "latest_scan_id": scan.scan_id if scan else "none",
        "top_movers": _mover_summaries(movers["rank_improvers"]),
        "top_score_improvers": _mover_summaries(movers["score_improvers"]),
        "top_confidence_improvers": _mover_summaries(movers["confidence_improvers"]),
        "top_evidence_improvers": _mover_summaries(movers["evidence_improvers"]),
        "summary": "Latest Market Pulse evidence summarized." if scan else "No Market Pulse evidence available yet.",
    }
    return outputs, () if scan else ("No latest Market Pulse scan found.",), {"related_scan_id": scan.scan_id if scan else None}


def _fundamental_agent(output_dir: Path) -> tuple[dict, tuple[str, ...], dict]:
    scan = latest_scan(output_dir)
    rows = scan.rows if scan else ()
    strong = [row.get("symbol", "") for row in rows if row.get("fundamental_guardrail") == "Strong Balance Sheet"][:5]
    red_flags = [
        row.get("symbol", "")
        for row in rows
        if row.get("fundamental_guardrail") == "Red Flag" and _safe_float(row.get("greenrock_score", "")) >= 70
    ][:5]
    missing = [row.get("symbol", "") for row in rows if row.get("fundamental_guardrail") == "Insufficient Data"][:5]
    outputs = {
        "latest_scan_id": scan.scan_id if scan else "none",
        "strong_support": strong,
        "red_flags_with_strong_technicals": red_flags,
        "missing_fundamental_data": missing,
        "summary": "Fundamental Guardrails reviewed for confidence support." if scan else "No Fundamental Guardrail data available yet.",
    }
    warnings = tuple(f"{ticker} has Red Flag guardrails with strong technical score." for ticker in red_flags)
    if not scan:
        warnings = ("No latest Market Pulse scan found.",)
    return outputs, warnings, {"related_scan_id": scan.scan_id if scan else None}


def _memory_agent(output_dir: Path) -> tuple[dict, tuple[str, ...], dict]:
    rows = load_memory_rows(output_dir)
    scan_ids = sorted({row.get("scan_id", "") for row in rows if row.get("scan_id", "")}, reverse=True)
    leaders = _new_archetype_leaders(rows)
    outputs = {
        "memory_updated": bool(scan_ids),
        "latest_memory_scan_id": scan_ids[0] if scan_ids else "none",
        "prior_memory_scan_id": scan_ids[1] if len(scan_ids) > 1 else "none",
        "scan_count": len(scan_ids),
        "new_archetype_leaders": leaders,
        "summary": f"Atlas Memory has {len(rows)} remembered observations across {len(scan_ids)} scans.",
    }
    warnings = () if scan_ids else ("Atlas Memory has no scan observations yet.",)
    return outputs, warnings, {"related_scan_id": scan_ids[0] if scan_ids else None}


def _report_agent(output_dir: Path, db_path: Path) -> tuple[dict, tuple[str, ...], dict]:
    readiness = staging_report_readiness(output_dir, allow_underfilled=False)
    analytics = staging_analytics_status(output_dir)
    with connect(initialize_database(db_path)) as connection:
        reports = list_reports(connection)
    latest_report = reports[0] if reports else None
    safe_to_recommend = readiness.can_generate and analytics.complete
    outputs = {
        "analyst_slate_exists": any(item.count for item in staging_readiness(output_dir)),
        "staging_ready": readiness.can_generate,
        "analytics_complete": analytics.complete,
        "recommendation": "Report draft can be generated" if safe_to_recommend else "Report draft is not ready.",
        "summary": "Report draft can be generated." if safe_to_recommend else "Report Agent found staging blockers.",
        "blocked": not safe_to_recommend,
    }
    warnings = readiness.warnings + tuple(f"{ticker} is missing analytics." for ticker in analytics.missing_tickers)
    return outputs, warnings, {"related_report_run_id": latest_report.run_id if latest_report else None}


def _qa_agent(output_dir: Path, db_path: Path) -> tuple[dict, tuple[str, ...], dict]:
    scan = latest_scan(output_dir)
    analytics = staging_analytics_status(output_dir)
    readiness = staging_readiness(output_dir)
    with connect(initialize_database(db_path)) as connection:
        approvals = list_approvals(connection)
        artifacts = list_artifacts(connection)
        reports = list_reports(connection)
    pending = tuple(approval for approval in approvals if approval.status == ApprovalStatus.PENDING)
    exported_run_ids = {artifact.run_id for artifact in artifacts if artifact.artifact_type == "report_final_pdf"}
    approved_run_ids = {approval.run_id for approval in approvals if approval.status == ApprovalStatus.APPROVED and approval.run_id}
    missing_pdfs = tuple(report for report in reports if report.run_id in approved_run_ids and report.run_id not in exported_run_ids)
    issues = []
    if scan and scan.provider_failure_count:
        issues.append(f"{scan.provider_failure_count} provider failure(s)")
    if analytics.missing_count:
        issues.append(f"{analytics.missing_count} staged candidate(s) missing analytics")
    issues.extend(f"{item.label} {item.status.lower()}" for item in readiness if item.status in {"Underfilled", "Overfilled"})
    if pending:
        issues.append(f"{len(pending)} pending approval(s)")
    if missing_pdfs:
        issues.append(f"{len(missing_pdfs)} approved report(s) missing PDF")
    outputs = {
        "provider_failures": scan.provider_failure_count if scan else 0,
        "missing_analytics": analytics.missing_count,
        "underfilled_buckets": [item.label for item in readiness if item.status == "Underfilled"],
        "overfilled_buckets": [item.label for item in readiness if item.status == "Overfilled"],
        "pending_approvals": len(pending),
        "missing_pdfs_after_approval": len(missing_pdfs),
        "summary": "; ".join(issues) if issues else "No QA issues detected.",
    }
    return outputs, tuple(issues), {"related_scan_id": scan.scan_id if scan else None, "related_approval_id": pending[0].id if pending else None}


def _inbox_agent(
    output_dir: Path,
    prior_outputs: dict[str, dict],
    prior_run_refs: dict[str, AgentRun],
    run_id: str,
    cycle_id: str,
) -> tuple[dict, tuple[str, ...], dict]:
    created: list[InboxItem] = []
    market = prior_outputs.get("market", {})
    evidence = prior_outputs.get("evidence", {})
    report = prior_outputs.get("report", {})
    qa = prior_outputs.get("qa", {})
    related_scan_id = market.get("latest_scan_id") if market.get("latest_scan_id") != "none" else None
    related_report_run_id = prior_run_refs.get("report").related_report_run_id if prior_run_refs.get("report") else None
    related_approval_id = prior_run_refs.get("qa").related_approval_id if prior_run_refs.get("qa") else None
    if market.get("latest_scan_id") and market.get("latest_scan_id") != "none":
        created.append(_agent_inbox_item(output_dir, "info", "Review latest Market Pulse", f"Scan {market['latest_scan_id']} is available.", "/greenrock/market-pulse", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "Market Agent found a latest scan."))
        created.append(_agent_inbox_item(output_dir, "info", "Morning Brief snapshot available", "Latest agent cycle can be reviewed in Morning Brief.", "/atlas/morning-brief", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "Inbox Agent completed a local cycle."))
    if evidence.get("top_movers") or evidence.get("top_score_improvers"):
        created.append(_agent_inbox_item(output_dir, "action", "Review latest Market Pulse", "Evidence Agent found movement worth operator review.", "/greenrock/market-pulse", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "Evidence Agent found score or rank movement."))
    if report.get("recommendation") == "Report draft can be generated":
        created.append(_agent_inbox_item(output_dir, "action", "Stage Analyst Slate", "Report Agent says staging is ready for a human-invoked draft.", "/greenrock/staging/generate/confirm", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "Report Agent found staging and analytics ready."))
    if qa.get("pending_approvals", 0):
        created.append(_agent_inbox_item(output_dir, "action", "Review pending approval", f"{qa['pending_approvals']} approval(s) require human review.", "/greenrock", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "QA Agent found pending approval records."))
    if qa.get("missing_pdfs_after_approval", 0):
        created.append(_agent_inbox_item(output_dir, "action", "Export approved PDF", f"{qa['missing_pdfs_after_approval']} approved report(s) need local PDF export.", "/greenrock/final-reports", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "QA Agent found approved reports without final PDFs."))
    if qa.get("provider_failures", 0):
        created.append(_agent_inbox_item(output_dir, "warning", "Provider failures require cleanup", f"{qa['provider_failures']} provider failure(s) detected.", "/greenrock/universe", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "QA Agent found provider failures."))
    if qa.get("underfilled_buckets"):
        created.append(_agent_inbox_item(output_dir, "warning", "Staging underfilled", ", ".join(qa["underfilled_buckets"]), "/greenrock/staging", run_id, cycle_id, related_scan_id, related_report_run_id, related_approval_id, "QA Agent found staging bucket counts below targets."))
    outputs = {"items_created": len(created), "summary": f"{len(created)} local inbox item(s) created or refreshed."}
    return outputs, (), {}


def _agent_inbox_item(
    output_dir: Path,
    severity: str,
    title: str,
    detail: str,
    target_url: str,
    run_id: str,
    cycle_id: str,
    related_scan_id: str | None,
    related_report_run_id: str | None,
    related_approval_id: int | None,
    created_reason: str,
) -> InboxItem:
    return create_inbox_item(
        output_dir,
        "inbox",
        severity,
        title,
        detail,
        target_url,
        related_agent_run_id=run_id,
        related_cycle_id=cycle_id,
        related_scan_id=related_scan_id,
        related_report_run_id=related_report_run_id,
        related_approval_id=related_approval_id,
        created_reason=created_reason,
    )


def _mover_summaries(items) -> list[str]:
    return [
        f"{item.ticker}: rank {item.previous.get('rank', '')}->{item.current.get('rank', '')}; score {item.previous.get('greenrock_score', '')}->{item.current.get('greenrock_score', '')}"
        for item in items[:3]
    ]


def _safe_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _new_archetype_leaders(rows: tuple[dict[str, str], ...]) -> list[str]:
    scan_ids = sorted({row.get("scan_id", "") for row in rows if row.get("scan_id", "")}, reverse=True)
    if len(scan_ids) < 2:
        return []
    latest, previous = scan_ids[0], scan_ids[1]
    latest_leaders = _leaders_by_archetype(tuple(row for row in rows if row.get("scan_id") == latest))
    previous_leaders = _leaders_by_archetype(tuple(row for row in rows if row.get("scan_id") == previous))
    return [
        f"{archetype}: {leader.get('ticker', '')} replaced {previous_leaders.get(archetype, {}).get('ticker', 'none')}"
        for archetype, leader in latest_leaders.items()
        if leader.get("ticker", "") != previous_leaders.get(archetype, {}).get("ticker", "")
    ]


def _normalize_market_scan_policy(policy: str) -> str:
    return policy if policy in MARKET_SCAN_POLICIES else DEFAULT_MARKET_SCAN_POLICY


def _normalize_stale_hours(value: float) -> float:
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return DEFAULT_STALE_HOURS
    return max(0.0, hours)


def _scan_age_hours(scan) -> float | None:
    if not scan:
        return None
    try:
        modified = datetime.fromtimestamp(Path(scan.results_path).stat().st_mtime, timezone.utc)
    except OSError:
        return None
    return (datetime.now(timezone.utc) - modified).total_seconds() / 3600


def _scan_is_stale(scan, scan_age_hours: float | None, stale_hours: float) -> bool:
    return scan is None or scan_age_hours is None or scan_age_hours > stale_hours


def _market_policy_reason(policy: str, scan, scan_age_hours: float | None, stale_hours: float) -> str:
    if policy == "use_latest_scan":
        return "default safe mode reused latest successful scan"
    if policy == "run_fresh_scan":
        return "operator requested fresh market scan"
    if scan is None:
        return "no latest scan found; stale policy will run fresh scan"
    if scan_age_hours is None:
        return "scan age unavailable; stale policy will run fresh scan"
    if scan_age_hours > stale_hours:
        return f"latest scan age {scan_age_hours:.2f}h exceeded {stale_hours:.2f}h threshold"
    return f"latest scan age {scan_age_hours:.2f}h is within {stale_hours:.2f}h threshold"


def _leaders_by_archetype(rows: tuple[dict[str, str], ...]) -> dict[str, dict[str, str]]:
    leaders: dict[str, dict[str, str]] = {}
    for row in sorted(rows, key=lambda item: int(float(item.get("rank", "999999") or "999999"))):
        archetype = row.get("market_archetype", "")
        if archetype and archetype not in leaders:
            leaders[archetype] = row
    return leaders


def _write_run(output_dir: Path, run: AgentRun) -> None:
    directory = agent_output_dir(output_dir) / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.run_id}.json"
    path.write_text(json.dumps(asdict(run), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_cycle_summary(
    output_dir: Path,
    cycle_id: str,
    started_at: str,
    completed_at: str,
    runs: tuple[AgentRun, ...],
    before_items: tuple[InboxItem, ...],
    prior_cycle: dict | None,
    cycle_context: dict,
) -> None:
    after_items = list_inbox_items(output_dir, include_closed=True)
    before_by_id = {item.item_id: item for item in before_items}
    after_by_id = {item.item_id: item for item in after_items}
    new_items = tuple(item for item in after_items if item.item_id not in before_by_id)
    resolved_items = tuple(
        item
        for item in after_items
        if item.item_id in before_by_id and before_by_id[item.item_id].status == "open" and item.status in {"dismissed", "completed"}
    )
    latest_market = next((run for run in runs if run.agent_id == "market"), None)
    latest_memory = next((run for run in runs if run.agent_id == "memory"), None)
    latest_report = next((run for run in runs if run.agent_id == "report"), None)
    latest_qa = next((run for run in runs if run.agent_id == "qa"), None)
    current_provider_failures = int((latest_qa.outputs if latest_qa else {}).get("provider_failures", 0) or 0)
    current_approval_count = int((latest_qa.outputs if latest_qa else {}).get("pending_approvals", 0) or 0)
    prior_outputs = prior_cycle.get("signals", {}) if prior_cycle else {}
    summary = {
        "cycle_id": cycle_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "last_run": completed_at,
        "completed": sum(1 for run in runs if run.status == "completed"),
        "failed": sum(1 for run in runs if run.status == "failed"),
        "blocked": sum(1 for run in runs if run.status == "blocked"),
        "inbox_items_generated": len(new_items),
        "open_inbox_items": sum(1 for item in after_items if item.status == "open"),
        "warnings": [warning for run in runs for warning in run.warnings],
        "top_operator_actions": [item.title for item in after_items if item.status == "open"][:5],
        "run_ids": [run.run_id for run in runs],
        "market_scan_policy": (latest_market.outputs if latest_market else {}).get("market_scan_policy", cycle_context.get("market_scan_policy")),
        "market_scan": {
            "policy": (latest_market.outputs if latest_market else {}).get("market_scan_policy", cycle_context.get("market_scan_policy")),
            "latest_scan_id": (latest_market.outputs if latest_market else {}).get("latest_scan_id", "none"),
            "fresh_data_pulled": (latest_market.outputs if latest_market else {}).get("fresh_data_pulled", False),
            "scan_age_hours": (latest_market.outputs if latest_market else {}).get("scan_age_hours"),
            "stale_threshold_hours": (latest_market.outputs if latest_market else {}).get("stale_threshold_hours", cycle_context.get("stale_hours")),
            "reason": (latest_market.outputs if latest_market else {}).get("reason", ""),
        },
        "signals": {
            "provider_failures": current_provider_failures,
            "pending_approvals": current_approval_count,
            "latest_scan_id": (latest_market.outputs if latest_market else {}).get("latest_scan_id", "none"),
            "latest_memory_scan_id": (latest_memory.outputs if latest_memory else {}).get("latest_memory_scan_id", "none"),
            "report_recommendation": (latest_report.outputs if latest_report else {}).get("recommendation", ""),
        },
        "diff": {
            "new_inbox_items": [_inbox_summary(item) for item in new_items],
            "resolved_or_dismissed_items": [_inbox_summary(item) for item in resolved_items],
            "new_provider_failures": max(0, current_provider_failures - int(prior_outputs.get("provider_failures", 0) or 0)),
            "changed_approval_counts": current_approval_count - int(prior_outputs.get("pending_approvals", 0) or 0),
            "new_scan_memory_changes": _scan_memory_changes(latest_market, latest_memory, prior_outputs),
            "new_report_readiness_changes": _report_readiness_changes(latest_report, prior_outputs),
        },
    }
    directory = agent_output_dir(output_dir) / "cycles"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{cycle_id}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _inbox_summary(item: InboxItem) -> dict:
    return {
        "item_id": item.item_id,
        "title": item.title,
        "severity": item.severity,
        "status": item.status,
        "created_reason": item.created_reason,
    }


def _scan_memory_changes(latest_market: AgentRun | None, latest_memory: AgentRun | None, prior_outputs: dict) -> list[str]:
    changes = []
    latest_scan = (latest_market.outputs if latest_market else {}).get("latest_scan_id", "none")
    latest_memory_scan = (latest_memory.outputs if latest_memory else {}).get("latest_memory_scan_id", "none")
    if latest_scan != prior_outputs.get("latest_scan_id", "none"):
        changes.append(f"latest scan changed to {latest_scan}")
    if latest_memory_scan != prior_outputs.get("latest_memory_scan_id", "none"):
        changes.append(f"latest memory scan changed to {latest_memory_scan}")
    return changes


def _report_readiness_changes(latest_report: AgentRun | None, prior_outputs: dict) -> list[str]:
    recommendation = (latest_report.outputs if latest_report else {}).get("recommendation", "")
    previous = prior_outputs.get("report_recommendation", "")
    if recommendation != previous:
        return [f"report readiness changed from {previous or 'none'} to {recommendation or 'none'}"]
    return []


def _write_agent_state(output_dir: Path, runs: tuple[AgentRun, ...]) -> None:
    directory = agent_output_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    state = []
    for run in runs:
        agent = get_agent(run.agent_id)
        state.append(
            asdict(
                replace(
                    agent,
                    status=run.status,
                    last_run_at=run.completed_at,
                    last_message=run.errors[0] if run.errors else run.outputs.get("summary", ""),
                    current_task="",
                    output_summary=run.outputs.get("summary", ""),
                    health=_health_for_run(run),
                )
            )
        )
    (directory / STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _agent_from_state(agent: Agent, item: dict) -> Agent:
    return replace(
        agent,
        status=item.get("status", agent.status),
        last_run_at=item.get("last_run_at"),
        last_message=item.get("last_message", agent.last_message),
        current_task=item.get("current_task", ""),
        output_summary=item.get("output_summary", ""),
        health=item.get("health", agent.health),
    )


def _run_from_dict(item: dict) -> AgentRun:
    return AgentRun(
        run_id=str(item.get("run_id", "")),
        agent_id=str(item.get("agent_id", "")),
        started_at=str(item.get("started_at", "")),
        completed_at=item.get("completed_at"),
        status=item.get("status", "failed"),
        inputs=item.get("inputs", {}),
        outputs=item.get("outputs", {}),
        warnings=tuple(item.get("warnings", ())),
        errors=tuple(item.get("errors", ())),
        related_scan_id=item.get("related_scan_id"),
        related_report_run_id=item.get("related_report_run_id"),
        related_approval_id=item.get("related_approval_id"),
    )


def _health_for_run(run: AgentRun) -> str:
    if run.status == "failed":
        return "red"
    if run.status == "blocked":
        return "blocked"
    if run.warnings:
        return "yellow"
    return "green"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
