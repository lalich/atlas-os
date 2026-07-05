"""Atlas Daily Intelligence Cycle synthesis."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.agents.base import AgentRun
from atlas_os.agents.orchestrator import (
    DEFAULT_MARKET_SCAN_POLICY,
    DEFAULT_STALE_HOURS,
    agent_cycle_summary,
    run_agent_cycle,
)
from atlas_os.agents.updates import AgentUpdate, new_update_id, updates_for_cycle, write_agent_update
from atlas_os.core.approvals import ApprovalStatus, list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.reports import list_reports
from atlas_os.db.database import connect, initialize_database
from atlas_os.diagnostics import provider_diagnostics
from atlas_os.greenrock.memory import load_memory_rows, memory_movers
from atlas_os.greenrock.scanner import latest_scan
from atlas_os.greenrock.staging import staging_analytics_status, staging_readiness
from atlas_os.greenrock.staging_report import staging_report_readiness
from atlas_os.inbox import create_inbox_item, list_inbox_items
from atlas_os.morning_brief import save_morning_brief_snapshot


DAILY_ROOT = Path("atlas") / "daily"


def daily_dir(output_dir: Path) -> Path:
    return Path(output_dir) / DAILY_ROOT


def run_daily_cycle(
    output_dir: Path,
    db_path: Path,
    market_scan_policy: str = DEFAULT_MARKET_SCAN_POLICY,
    stale_hours: float = DEFAULT_STALE_HOURS,
) -> dict:
    db = initialize_database(db_path)
    started_at = _now()
    prior_daily = latest_daily_brief(output_dir)
    prior_inbox = list_inbox_items(output_dir, include_closed=True)
    provider = provider_diagnostics()
    runs = run_agent_cycle(output_dir, db, market_scan_policy=market_scan_policy, stale_hours=stale_hours)
    cycle = agent_cycle_summary(output_dir)
    cycle_id = str(cycle.get("cycle_id", _cycle_id_from_runs(runs)))
    updates = _build_agent_updates(output_dir, db, cycle_id, runs, provider)
    for update in updates:
        write_agent_update(output_dir, update)
    brief = _synthesize_daily_brief(output_dir, db, cycle, updates, prior_daily, prior_inbox, started_at)
    _create_material_inbox_items(output_dir, brief, updates)
    _refresh_inbox_comparison(output_dir, brief, prior_inbox)
    snapshot = save_morning_brief_snapshot(output_dir, db)
    brief["morning_brief_snapshot_id"] = snapshot.get("snapshot_id", "")
    brief["completed_at"] = _now()
    _write_daily_brief(output_dir, brief)
    return brief


def list_daily_briefs(output_dir: Path) -> tuple[dict, ...]:
    directory = daily_dir(output_dir)
    if not directory.exists():
        return ()
    briefs = []
    for path in directory.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        item["path"] = str(path)
        briefs.append(item)
    return tuple(sorted(briefs, key=lambda item: item.get("created_at", ""), reverse=True))


def latest_daily_brief(output_dir: Path) -> dict | None:
    briefs = list_daily_briefs(output_dir)
    return briefs[0] if briefs else None


def get_daily_brief(output_dir: Path, daily_id: str) -> dict:
    clean = daily_id.strip().removesuffix(".json")
    if not clean or "/" in clean or ".." in clean:
        raise KeyError(daily_id)
    path = daily_dir(output_dir) / f"{clean}.json"
    if not path.exists():
        raise KeyError(daily_id)
    item = json.loads(path.read_text(encoding="utf-8"))
    item["path"] = str(path)
    return item


def _build_agent_updates(output_dir: Path, db_path: Path, cycle_id: str, runs: tuple[AgentRun, ...], provider) -> tuple[AgentUpdate, ...]:
    by_agent = {run.agent_id: run for run in runs}
    scan = latest_scan(output_dir)
    movers = memory_movers(output_dir)
    memory_rows = load_memory_rows(output_dir)
    with connect(initialize_database(db_path)) as connection:
        approvals = list_approvals(connection)
        artifacts = list_artifacts(connection)
        reports = list_reports(connection)
    pending = tuple(approval for approval in approvals if approval.status == ApprovalStatus.PENDING)
    approved_run_ids = {approval.run_id for approval in approvals if approval.status == ApprovalStatus.APPROVED and approval.run_id}
    exported_run_ids = {artifact.run_id for artifact in artifacts if artifact.artifact_type == "report_final_pdf"}
    missing_pdfs = tuple(report for report in reports if report.run_id in approved_run_ids and report.run_id not in exported_run_ids)
    readiness = staging_readiness(output_dir)
    analytics = staging_analytics_status(output_dir)
    report_readiness = staging_report_readiness(output_dir, allow_underfilled=False)
    updates = [
        _market_update(cycle_id, by_agent.get("market"), scan, memory_rows),
        _evidence_update(cycle_id, by_agent.get("evidence"), scan, movers),
        _fundamental_update(cycle_id, by_agent.get("fundamental"), scan),
        _memory_update(cycle_id, by_agent.get("memory"), memory_rows, movers),
        _report_update(cycle_id, by_agent.get("report"), report_readiness, analytics, pending, missing_pdfs),
        _qa_update(cycle_id, by_agent.get("qa"), scan, provider, readiness, analytics, pending, missing_pdfs, runs, memory_rows),
    ]
    return tuple(updates)


def _market_update(cycle_id: str, run: AgentRun | None, scan, memory_rows: tuple[dict[str, str], ...]) -> AgentUpdate:
    rows = scan.rows if scan else ()
    leaders = _leaders_by_archetype(rows)
    prior_top = _prior_top_tickers(memory_rows)
    current_top = [row.get("symbol", "") for row in rows[:10]]
    new_high = [ticker for ticker in current_top if ticker and ticker not in prior_top][:5]
    leaving = [ticker for ticker in prior_top if ticker and ticker not in current_top][:5]
    findings = [
        f"Universe scored {len(rows)} names; skipped {scan.skipped_ticker_count if scan else 0}.",
        f"Provider failures {scan.provider_failure_count if scan else 0}.",
        f"Archetype leaders: {_compact_leaders(leaders)}",
    ]
    if new_high:
        findings.append(f"New top-ranked names: {', '.join(new_high)}.")
    if leaving:
        findings.append(f"Names leaving top ranks: {', '.join(leaving)}.")
    metrics = {
        "configured": scan.configured_ticker_count if scan else 0,
        "ranked": len(rows),
        "skipped": scan.skipped_ticker_count if scan else 0,
        "provider_failures": scan.provider_failure_count if scan else 0,
        "market_scan_policy": (run.outputs if run else {}).get("market_scan_policy", "use_latest_scan"),
        "fresh_data_pulled": (run.outputs if run else {}).get("fresh_data_pulled", False),
    }
    return _update(
        cycle_id,
        "Market Agent",
        run,
        "info" if rows else "warning",
        "Market Pulse reviewed",
        f"Latest scan {scan.scan_id if scan else 'none'} ranked {len(rows)} opportunities using {(run.outputs if run else {}).get('market_scan_policy', 'use_latest_scan')}.",
        findings,
        metrics,
        tuple(row.get("symbol", "") for row in rows[:5] if row.get("symbol", "")),
        scan.scan_id if scan else None,
        "Review latest Market Pulse leaders.",
        "/greenrock/market-pulse",
    )


def _evidence_update(cycle_id: str, run: AgentRun | None, scan, movers) -> AgentUpdate:
    rank_improvers = _mover_rows(movers["rank_improvers"])
    confidence = _mover_rows(movers["confidence_improvers"])
    evidence = _mover_rows(movers["evidence_improvers"])
    deteriorations = _mover_rows(movers["deteriorations"])
    findings = []
    if rank_improvers:
        findings.append(f"Rank improvers: {_ticker_list(rank_improvers)}.")
    if confidence:
        findings.append(f"Confidence improvers: {_ticker_list(confidence)}.")
    if evidence:
        findings.append(f"Evidence improvers: {_ticker_list(evidence)}.")
    if deteriorations:
        findings.append(f"Deteriorations: {_ticker_list(deteriorations)}.")
    if not findings:
        findings.append("No material evidence movement versus prior memory.")
    return _update(
        cycle_id,
        "Evidence Agent",
        run,
        "info",
        "Evidence movement summarized",
        "Evidence Agreement, Confidence, and Memory movers were compared against the prior scan.",
        findings,
        {"rank_improvers": len(rank_improvers), "confidence_improvers": len(confidence), "evidence_improvers": len(evidence), "deteriorations": len(deteriorations)},
        tuple(row["ticker"] for row in (rank_improvers + confidence + evidence)[:5]),
        scan.scan_id if scan else None,
        "Review movers with improving evidence and confidence.",
        "/greenrock/memory/movers",
    )


def _fundamental_update(cycle_id: str, run: AgentRun | None, scan) -> AgentUpdate:
    rows = scan.rows if scan else ()
    strongest = [row for row in rows if row.get("fundamental_guardrail") == "Strong Balance Sheet"][:5]
    red_flags = [row for row in rows if row.get("fundamental_guardrail") == "Red Flag" and _float(row.get("greenrock_score", "")) >= 70][:5]
    missing = [row for row in rows if row.get("fundamental_guardrail") == "Insufficient Data"][:5]
    findings = [
        f"Strong fundamental support: {_symbols(strongest) or 'none'}.",
        f"Red flags with strong technical scores: {_symbols(red_flags) or 'none'}.",
        f"Missing fundamental guardrail data: {_symbols(missing) or 'none'}.",
    ]
    severity = "warning" if red_flags else "info"
    return _update(
        cycle_id,
        "Fundamental Agent",
        run,
        severity,
        "Fundamental guardrails reviewed",
        "Fundamental guardrails were checked as evidence and confidence support, not as a valuation model.",
        findings,
        {"strong_balance_sheet": len(strongest), "red_flags_with_strong_technicals": len(red_flags), "insufficient_data": len(missing)},
        tuple(row.get("symbol", "") for row in (red_flags + strongest)[:5] if row.get("symbol", "")),
        scan.scan_id if scan else None,
        "Investigate red-flag fundamentals before staging any technically strong names.",
        "/greenrock/market-pulse",
    )


def _memory_update(cycle_id: str, run: AgentRun | None, memory_rows: tuple[dict[str, str], ...], movers) -> AgentUpdate:
    leaders = _new_archetype_leaders(memory_rows)
    persistent = _persistent_top_names(memory_rows)
    threshold = [row["ticker"] for row in _mover_rows(movers["rank_improvers"]) if _int(row.get("current_rank", "")) <= 10][:5]
    findings = [
        f"Memory contains {len(memory_rows)} observations.",
        f"New archetype leaders: {', '.join(leaders) if leaders else 'none'}.",
        f"Persistent high-ranking names: {', '.join(persistent) if persistent else 'none'}.",
    ]
    if threshold:
        findings.append(f"Threshold entries into top ranks: {', '.join(threshold)}.")
    return _update(
        cycle_id,
        "Memory Agent",
        run,
        "info",
        "Memory deltas checked",
        "Rank, score, confidence, evidence, archetype leader, and persistence changes were reviewed.",
        findings,
        {"memory_observations": len(memory_rows), "new_archetype_leaders": len(leaders), "persistent_high_ranking": len(persistent)},
        tuple((persistent + threshold)[:5]),
        (run.related_scan_id if run else None),
        "Use Memory movers to focus the research queue.",
        "/greenrock/memory/movers",
    )


def _report_update(cycle_id: str, run: AgentRun | None, readiness, analytics, pending, missing_pdfs) -> AgentUpdate:
    findings = [
        f"Staging readiness: {'ready' if readiness.can_generate else 'blocked'}.",
        f"Analytics complete: {'yes' if analytics.complete else 'no'}; missing {analytics.missing_count}.",
        f"Pending approvals: {len(pending)}; approved PDFs missing: {len(missing_pdfs)}.",
    ]
    findings.extend(readiness.warnings[:3])
    safe = readiness.can_generate and analytics.complete
    severity = "action" if safe or pending or missing_pdfs else "warning"
    action = "Generate report draft only if operator explicitly invokes it." if safe else "Resolve staging, analytics, approval, or PDF readiness blockers."
    return _update(
        cycle_id,
        "Report Agent",
        run,
        severity,
        "Report readiness checked",
        "Report Agent checked staging fill, archetype coverage, analytics, approvals, and PDF state without generating a report.",
        findings,
        {"staging_ready": readiness.can_generate, "analytics_complete": analytics.complete, "pending_approvals": len(pending), "missing_pdfs": len(missing_pdfs)},
        (),
        run.related_scan_id if run else None,
        action,
        "/greenrock/staging",
        related_report_run_id=run.related_report_run_id if run else None,
        related_approval_id=pending[0].id if pending else None,
    )


def _qa_update(cycle_id: str, run: AgentRun | None, scan, provider, readiness, analytics, pending, missing_pdfs, runs, memory_rows) -> AgentUpdate:
    failures = scan.provider_failure_count if scan else 0
    issues = []
    if not scan:
        issues.append("No latest successful scan is available.")
    if failures:
        issues.append(f"{failures} provider failure(s) detected.")
    if analytics.missing_count:
        issues.append(f"{analytics.missing_count} staged candidate(s) missing analytics.")
    issues.extend(f"{item.label} {item.status.lower()}." for item in readiness if item.status in {"Underfilled", "Overfilled"})
    if pending:
        issues.append(f"{len(pending)} pending approval(s).")
    if missing_pdfs:
        issues.append(f"{len(missing_pdfs)} approved report(s) missing PDF.")
    if any(agent_run.status == "failed" for agent_run in runs):
        issues.append("One or more agents failed.")
    duplicate_count = _duplicate_symbol_count(scan.rows if scan else ())
    if duplicate_count:
        issues.append(f"{duplicate_count} duplicate ticker row(s) detected.")
    if not memory_rows:
        issues.append("Atlas Memory has no stored observations.")
    if not issues:
        issues.append("No QA issues detected.")
    return _update(
        cycle_id,
        "QA Agent",
        run,
        "warning" if len(issues) > 1 or issues[0] != "No QA issues detected." else "info",
        "QA checks complete",
        "QA checked provider health, data freshness, analytics, staging coverage, approvals, PDFs, agent failures, and provenance.",
        issues,
        {
            "provider_ready": provider.scanner_ready,
            "provider_failures": failures,
            "missing_analytics": analytics.missing_count,
            "pending_approvals": len(pending),
            "missing_pdfs": len(missing_pdfs),
            "duplicate_rows": duplicate_count,
        },
        (),
        scan.scan_id if scan else None,
        "Clear QA warnings before advancing report work.",
        "/agents",
        related_approval_id=pending[0].id if pending else None,
    )


def _synthesize_daily_brief(
    output_dir: Path,
    db_path: Path,
    cycle: dict,
    updates: tuple[AgentUpdate, ...],
    prior_daily: dict | None,
    prior_inbox,
    started_at: str,
) -> dict:
    scan = latest_scan(output_dir)
    movers = memory_movers(output_dir)
    with connect(initialize_database(db_path)) as connection:
        approvals = list_approvals(connection)
    pending_count = sum(1 for approval in approvals if approval.status == ApprovalStatus.PENDING)
    priorities = _research_priorities(scan, movers)
    actions = _operator_actions(updates, pending_count)
    comparison = _daily_comparison(prior_daily, priorities, cycle, list_inbox_items(output_dir, include_closed=True), prior_inbox)
    daily_id = "daily-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    executive = _executive_summary(scan, cycle, updates, priorities, actions)
    return {
        "daily_id": daily_id,
        "cycle_id": cycle.get("cycle_id", ""),
        "created_at": started_at,
        "completed_at": "",
        "executive_summary": executive,
        "what_changed": comparison["what_changed"],
        "research_priorities": priorities[:5],
        "agent_updates": [asdict(update) for update in updates],
        "operator_actions": actions[:5],
        "comparison": comparison,
        "market_scan_policy": cycle.get("market_scan_policy", "use_latest_scan"),
        "market_scan": cycle.get("market_scan", {}),
        "warnings": cycle.get("warnings", []),
        "safety": {
            "local_only": True,
            "email": "disabled",
            "publishing": "disabled",
            "trading": "disabled",
            "client_files": "disabled",
            "external_llm_api": "disabled",
            "approval_gates": "mandatory",
        },
    }


def _research_priorities(scan, movers) -> list[dict]:
    rows = list(scan.rows if scan else ())
    rank_changes = {item.ticker: _int(item.previous.get("rank", "")) - _int(item.current.get("rank", "")) for item in movers["rank_improvers"]}
    priorities = []
    for row in rows[:12]:
        ticker = row.get("symbol", "")
        priority = {
            "ticker": ticker,
            "rank": row.get("rank", ""),
            "rank_change": rank_changes.get(ticker, 0),
            "score": row.get("greenrock_score", ""),
            "confidence": row.get("greenrock_confidence", ""),
            "evidence": row.get("evidence_agreement", ""),
            "priority": row.get("research_priority", ""),
            "thesis": row.get("top_bullish_signal", "") or "Technical dislocation/opportunity ranks near the top of Market Pulse.",
            "risk": row.get("top_caution_signal", "") or row.get("data_quality_warnings", "") or "Review evidence quality before action.",
            "link": f"/greenrock/score?ticker={ticker}" if ticker else "/greenrock/market-pulse",
        }
        priorities.append(priority)
    return priorities[:5]


def _operator_actions(updates: tuple[AgentUpdate, ...], pending_count: int) -> list[dict]:
    actions = []
    for update in updates:
        if update.severity in {"warning", "critical", "action"} and update.recommended_operator_action:
            actions.append(
                {
                    "title": update.recommended_operator_action,
                    "source_agent": update.agent_name,
                    "severity": update.severity,
                    "target_url": update.target_url,
                    "reason": update.headline,
                }
            )
    if pending_count:
        actions.insert(
            0,
            {
                "title": f"Review {pending_count} pending approval(s).",
                "source_agent": "Report Agent",
                "severity": "action",
                "target_url": "/greenrock",
                "reason": "Approval gates remain mandatory.",
            },
        )
    if not actions:
        actions.append({"title": "Review latest Daily Intelligence Brief.", "source_agent": "Atlas Synthesis", "severity": "info", "target_url": "/atlas/morning-brief", "reason": "Daily cycle completed."})
    deduped = []
    seen = set()
    for action in actions:
        key = (action["title"], action["source_agent"])
        if key not in seen:
            seen.add(key)
            deduped.append(action)
    return deduped[:5]


def _daily_comparison(prior_daily: dict | None, priorities: list[dict], cycle: dict, current_inbox, prior_inbox) -> dict:
    current_tickers = {item.get("ticker", "") for item in priorities}
    prior_tickers = {item.get("ticker", "") for item in (prior_daily or {}).get("research_priorities", [])}
    current_actions = sum(1 for item in current_inbox if item.status == "open")
    prior_actions = sum(1 for item in prior_inbox if item.status == "open")
    what_changed = []
    new_priorities = sorted(current_tickers - prior_tickers)
    removed_priorities = sorted(prior_tickers - current_tickers)
    if new_priorities:
        what_changed.append(f"New research priorities: {', '.join(new_priorities[:5])}.")
    if removed_priorities:
        what_changed.append(f"Removed priorities: {', '.join(removed_priorities[:5])}.")
    for change in cycle.get("diff", {}).get("new_scan_memory_changes", [])[:3]:
        what_changed.append(change)
    for change in cycle.get("diff", {}).get("new_report_readiness_changes", [])[:2]:
        what_changed.append(change)
    if current_actions != prior_actions:
        what_changed.append(f"Open Inbox action count changed from {prior_actions} to {current_actions}.")
    if not what_changed:
        what_changed.append("No material cycle-to-cycle change detected.")
    return {
        "new_priorities": new_priorities,
        "removed_priorities": removed_priorities,
        "leader_changes": cycle.get("diff", {}).get("new_scan_memory_changes", []),
        "qa_health_change": cycle.get("diff", {}).get("new_provider_failures", 0),
        "report_readiness_change": cycle.get("diff", {}).get("new_report_readiness_changes", []),
        "inbox_action_count_change": current_actions - prior_actions,
        "what_changed": what_changed[:6],
    }


def _create_material_inbox_items(output_dir: Path, brief: dict, updates: tuple[AgentUpdate, ...]) -> None:
    by_agent = {update.agent_name: update for update in updates}
    for action in brief.get("operator_actions", [])[:5]:
        severity = action.get("severity", "info")
        if severity == "info":
            continue
        source = str(action.get("source_agent", "daily"))
        update = by_agent.get(source)
        create_inbox_item(
            output_dir,
            "daily",
            severity,
            str(action.get("title", "Review Daily Intelligence Brief")),
            str(action.get("reason", "")),
            str(action.get("target_url", "/atlas/morning-brief")),
            related_agent_run_id=(update.provenance.get("agent_run_id") if update else None),
            related_cycle_id=brief.get("cycle_id"),
            related_scan_id=(update.related_scan_id if update else None),
            related_report_run_id=(update.related_report_run_id if update else None),
            related_approval_id=(update.related_approval_id if update else None),
            created_reason=f"Daily Intelligence materiality threshold from {source}.",
        )


def _refresh_inbox_comparison(output_dir: Path, brief: dict, prior_inbox) -> None:
    current_actions = sum(1 for item in list_inbox_items(output_dir, include_closed=True) if item.status == "open")
    prior_actions = sum(1 for item in prior_inbox if item.status == "open")
    comparison = brief.setdefault("comparison", {})
    delta = current_actions - prior_actions
    comparison["inbox_action_count_change"] = delta
    changed_line = f"Open Inbox action count changed from {prior_actions} to {current_actions}."
    what_changed = [item for item in brief.get("what_changed", []) if not str(item).startswith("Open Inbox action count changed from ")]
    if delta:
        what_changed.append(changed_line)
    brief["what_changed"] = what_changed[:6] or ["No material cycle-to-cycle change detected."]


def _executive_summary(scan, cycle: dict, updates: tuple[AgentUpdate, ...], priorities: list[dict], actions: list[dict]) -> str:
    scored = len(scan.rows) if scan else 0
    failed = cycle.get("failed", 0)
    blocked = cycle.get("blocked", 0)
    first_priority = priorities[0]["ticker"] if priorities else "none"
    qa = next((update for update in updates if update.agent_name == "QA Agent"), None)
    qa_note = qa.findings[0] if qa and qa.findings else "QA produced no material warnings."
    return " ".join(
        [
            f"Atlas completed a local Daily Intelligence Cycle using market policy {cycle.get('market_scan_policy', 'use_latest_scan')}.",
            f"Market Pulse currently has {scored} scored names, with top research priority {first_priority}.",
            f"Agent cycle status: {cycle.get('completed', 0)} completed, {failed} failed, {blocked} blocked.",
            f"{qa_note}",
            f"{len(actions)} operator action(s) are queued for review; no email, publishing, trading, client files, or approval bypass occurred.",
        ]
    )


def _update(
    cycle_id: str,
    agent_name: str,
    run: AgentRun | None,
    severity: str,
    headline: str,
    summary: str,
    findings: list[str],
    metrics: dict,
    tickers: tuple[str, ...],
    related_scan_id: str | None,
    action: str,
    target_url: str,
    related_report_run_id: str | None = None,
    related_approval_id: int | None = None,
) -> AgentUpdate:
    created_at = _now()
    status = run.status if run else "completed"
    return AgentUpdate(
        update_id=new_update_id(cycle_id, agent_name),
        cycle_id=cycle_id,
        agent_name=agent_name,
        created_at=created_at,
        status=status,
        severity=severity,
        headline=headline,
        summary=summary,
        findings=tuple(findings[:6]),
        supporting_metrics=metrics,
        related_tickers=tuple(ticker for ticker in tickers if ticker)[:8],
        related_scan_id=related_scan_id,
        related_report_run_id=related_report_run_id or (run.related_report_run_id if run else None),
        related_approval_id=related_approval_id or (run.related_approval_id if run else None),
        recommended_operator_action=action,
        target_url=target_url,
        provenance={
            "agent_run_id": run.run_id if run else None,
            "cycle_id": cycle_id,
            "canonical_sources": ["latest_scan", "memory", "staging", "approvals", "inbox"],
            "local_only": True,
        },
    )


def _write_daily_brief(output_dir: Path, brief: dict) -> None:
    directory = daily_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{brief['daily_id']}.json").write_text(json.dumps(brief, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _leaders_by_archetype(rows) -> dict[str, dict[str, str]]:
    leaders = {}
    for row in rows:
        archetype = row.get("market_archetype", "")
        if archetype and archetype not in leaders:
            leaders[archetype] = row
    return leaders


def _new_archetype_leaders(rows: tuple[dict[str, str], ...]) -> list[str]:
    scan_ids = sorted({row.get("scan_id", "") for row in rows if row.get("scan_id", "")}, reverse=True)
    if len(scan_ids) < 2:
        return []
    latest, previous = scan_ids[0], scan_ids[1]
    latest_leaders = _leaders_by_archetype(row for row in rows if row.get("scan_id") == latest)
    previous_leaders = _leaders_by_archetype(row for row in rows if row.get("scan_id") == previous)
    return [
        f"{archetype}: {leader.get('ticker', '')} replaced {previous_leaders.get(archetype, {}).get('ticker', 'none')}"
        for archetype, leader in latest_leaders.items()
        if leader.get("ticker", "") != previous_leaders.get(archetype, {}).get("ticker", "")
    ][:5]


def _persistent_top_names(rows: tuple[dict[str, str], ...]) -> list[str]:
    scan_ids = sorted({row.get("scan_id", "") for row in rows if row.get("scan_id", "")}, reverse=True)[:2]
    if len(scan_ids) < 2:
        return []
    sets = []
    for scan_id in scan_ids:
        top = {row.get("ticker", "") for row in rows if row.get("scan_id") == scan_id and _int(row.get("rank", "")) <= 10}
        sets.append(top)
    return sorted(sets[0] & sets[1])[:5]


def _prior_top_tickers(rows: tuple[dict[str, str], ...]) -> list[str]:
    scan_ids = sorted({row.get("scan_id", "") for row in rows if row.get("scan_id", "")}, reverse=True)
    if len(scan_ids) < 2:
        return []
    previous = scan_ids[1]
    ranked = sorted((row for row in rows if row.get("scan_id") == previous), key=lambda row: _int(row.get("rank", "")))
    return [row.get("ticker", "") for row in ranked[:10]]


def _mover_rows(items) -> list[dict[str, str]]:
    return [
        {
            "ticker": item.ticker,
            "previous_rank": item.previous.get("rank", ""),
            "current_rank": item.current.get("rank", ""),
            "previous_score": item.previous.get("greenrock_score", ""),
            "current_score": item.current.get("greenrock_score", ""),
        }
        for item in items[:5]
    ]


def _compact_leaders(leaders: dict[str, dict[str, str]]) -> str:
    if not leaders:
        return "none"
    return ", ".join(f"{label} {row.get('symbol', row.get('ticker', ''))}" for label, row in list(leaders.items())[:4])


def _ticker_list(rows: list[dict[str, str]]) -> str:
    return ", ".join(row["ticker"] for row in rows[:5])


def _symbols(rows: list[dict[str, str]]) -> str:
    return ", ".join(row.get("symbol", "") for row in rows if row.get("symbol", ""))


def _duplicate_symbol_count(rows) -> int:
    seen = set()
    duplicates = 0
    for row in rows:
        symbol = row.get("symbol", "")
        if symbol in seen:
            duplicates += 1
        seen.add(symbol)
    return duplicates


def _cycle_id_from_runs(runs: tuple[AgentRun, ...]) -> str:
    if not runs:
        return "none"
    return runs[0].run_id.removeprefix("agent-cycle-").rsplit("-", 1)[0]


def _float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 999999


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
