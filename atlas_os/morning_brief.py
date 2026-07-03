"""Local Atlas Morning Brief snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.agents.orchestrator import agent_cycle_summary, list_agent_states
from atlas_os.core.approvals import ApprovalStatus, list_approvals
from atlas_os.core.artifacts import list_artifacts
from atlas_os.core.reports import list_reports
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.memory import load_memory_rows, memory_movers
from atlas_os.greenrock.scanner import latest_scan
from atlas_os.greenrock.universe_manager import default_universe_manager
from atlas_os.inbox import list_inbox_items


SNAPSHOT_ROOT = Path("atlas") / "morning_briefs"


def morning_brief_snapshot_dir(output_dir: Path) -> Path:
    return Path(output_dir) / SNAPSHOT_ROOT


def build_morning_brief_snapshot(output_dir: Path, db_path: Path) -> dict:
    db = initialize_database(db_path)
    scan = latest_scan(output_dir)
    master = default_universe_manager(output_dir).master_universe()
    movers = memory_movers(output_dir)
    with connect(db) as connection:
        approvals = list_approvals(connection)
        artifacts = list_artifacts(connection)
        reports = list_reports(connection)
    pending = tuple(approval for approval in approvals if approval.status == ApprovalStatus.PENDING)
    pdf_exported = tuple(artifact for artifact in artifacts if artifact.artifact_type == "report_final_pdf")
    exported_run_ids = {artifact.run_id for artifact in pdf_exported}
    approved_run_ids = {approval.run_id for approval in approvals if approval.status == ApprovalStatus.APPROVED and approval.run_id}
    pdf_ready = tuple(report for report in reports if report.run_id in approved_run_ids and report.run_id not in exported_run_ids)
    awaiting_review = tuple(report for report in reports if report.status in {"draft", "pending", "awaiting_approval"})
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot_id = "morning-brief-" + timestamp.replace("+00:00", "Z").replace(":", "").replace("-", "")
    suggested_actions = _suggested_actions(scan, len(pending), len(pdf_ready), movers)
    agent_summary = agent_cycle_summary(output_dir)
    agents = list_agent_states(output_dir)
    inbox_items = list_inbox_items(output_dir)
    return {
        "snapshot_id": snapshot_id,
        "timestamp": timestamp,
        "latest_scan_id": scan.scan_id if scan else "none",
        "universe_size": master.size,
        "configured_count": scan.configured_ticker_count if scan else 0,
        "scored_count": len(scan.rows) if scan else 0,
        "skipped_count": scan.skipped_ticker_count if scan else 0,
        "provider_failures": scan.provider_failure_count if scan else 0,
        "top_movers": _top_movers(movers),
        "new_archetype_leaders": _new_archetype_leaders(output_dir),
        "pending_approvals": len(pending),
        "reports_awaiting_review": len(awaiting_review),
        "pdf_ready": len(pdf_ready),
        "pdf_exported": len(pdf_exported),
        "suggested_actions": suggested_actions,
        "last_agent_cycle": agent_summary["last_run"],
        "agent_run_summary": {
            "completed": agent_summary["completed"],
            "failed": agent_summary["failed"],
            "blocked": agent_summary["blocked"],
            "inbox_items_generated": agent_summary["inbox_items_generated"],
        },
        "agent_health_cards": [
            {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "status": agent.status,
                "health": agent.health,
                "last_message": agent.last_message,
            }
            for agent in agents
        ],
        "agent_inbox_items": [
            {
                "item_id": item.item_id,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "severity": item.severity,
                "status": item.status,
                "source_agent": item.source_agent,
                "related_cycle_id": item.related_cycle_id,
                "title": item.title,
                "detail": item.detail,
                "target_url": item.target_url,
            }
            for item in inbox_items[:10]
        ],
        "safety": {
            "local_only": True,
            "email": "disabled",
            "publishing": "disabled",
            "trading": "disabled",
            "client_files": "disabled",
        },
    }


def save_morning_brief_snapshot(output_dir: Path, db_path: Path) -> dict:
    snapshot = build_morning_brief_snapshot(output_dir, db_path)
    directory = morning_brief_snapshot_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot['snapshot_id']}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    snapshot["path"] = str(path)
    return snapshot


def list_morning_brief_snapshots(output_dir: Path) -> tuple[dict, ...]:
    directory = morning_brief_snapshot_dir(output_dir)
    if not directory.exists():
        return ()
    snapshots = []
    for path in directory.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot["path"] = str(path)
        snapshots.append(snapshot)
    return tuple(sorted(snapshots, key=lambda item: item.get("timestamp", ""), reverse=True))


def load_morning_brief_snapshot(output_dir: Path, snapshot_id: str) -> dict:
    clean = snapshot_id.strip().removesuffix(".json")
    if not clean or "/" in clean or ".." in clean:
        raise KeyError(snapshot_id)
    path = morning_brief_snapshot_dir(output_dir) / f"{clean}.json"
    if not path.exists():
        raise KeyError(snapshot_id)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["path"] = str(path)
    return snapshot


def latest_morning_brief_snapshot(output_dir: Path) -> dict | None:
    snapshots = list_morning_brief_snapshots(output_dir)
    return snapshots[0] if snapshots else None


def _top_movers(movers) -> dict[str, list[dict[str, str]]]:
    return {
        key: [_mover_row(item) for item in movers[key][:3]]
        for key in ("rank_improvers", "score_improvers", "confidence_improvers", "evidence_improvers", "deteriorations")
    }


def _mover_row(item) -> dict[str, str]:
    return {
        "ticker": item.ticker,
        "previous_rank": item.previous.get("rank", ""),
        "current_rank": item.current.get("rank", ""),
        "previous_score": item.previous.get("greenrock_score", ""),
        "current_score": item.current.get("greenrock_score", ""),
        "previous_confidence": item.previous.get("confidence", ""),
        "current_confidence": item.current.get("confidence", ""),
        "summary": f"{item.ticker} rank {item.previous.get('rank', '')}->{item.current.get('rank', '')}; score {item.previous.get('greenrock_score', '')}->{item.current.get('greenrock_score', '')}",
    }


def _new_archetype_leaders(output_dir: Path) -> tuple[str, ...]:
    rows = load_memory_rows(output_dir)
    scan_ids = sorted({row["scan_id"] for row in rows}, reverse=True)
    if len(scan_ids) < 2:
        return ()
    latest, previous = scan_ids[0], scan_ids[1]
    latest_leaders = _leaders_by_archetype(tuple(row for row in rows if row["scan_id"] == latest))
    previous_leaders = _leaders_by_archetype(tuple(row for row in rows if row["scan_id"] == previous))
    return tuple(
        f"{archetype}: {leader.get('ticker', '')} replaced {previous_leaders.get(archetype, {}).get('ticker', 'none')}"
        for archetype, leader in latest_leaders.items()
        if leader.get("ticker", "") != previous_leaders.get(archetype, {}).get("ticker", "")
    )


def _leaders_by_archetype(rows: tuple[dict[str, str], ...]) -> dict[str, dict[str, str]]:
    leaders: dict[str, dict[str, str]] = {}
    for row in sorted(rows, key=lambda item: int(float(item.get("rank", "999999") or "999999"))):
        archetype = row.get("market_archetype", "")
        if archetype and archetype not in leaders:
            leaders[archetype] = row
    return leaders


def _suggested_actions(scan, pending_count: int, pdf_ready_count: int, movers) -> tuple[str, ...]:
    actions = []
    if not scan:
        actions.append("Run atlas greenrock scan --population all.")
    if pending_count:
        actions.append(f"Review {pending_count} pending approval(s).")
    if pdf_ready_count:
        actions.append(f"Export {pdf_ready_count} approved PDF(s) when ready.")
    if any(movers[key] for key in ("rank_improvers", "score_improvers", "confidence_improvers", "evidence_improvers", "deteriorations")):
        actions.append("Review Atlas Memory movers before staging the next report slate.")
    if not actions:
        actions.append("No urgent local action items.")
    return tuple(actions)
