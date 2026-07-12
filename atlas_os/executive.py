"""Local executive context and timeline helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ExecutiveEvent:
    timestamp: str
    label: str
    detail: str
    href: str
    identity: str


def executive_context_path(output_dir: Path) -> Path:
    return Path(output_dir) / "atlas" / "executive_context.json"


def load_executive_context(output_dir: Path) -> dict[str, str]:
    path = executive_context_path(output_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if isinstance(value, str)}


def remember_workflow(output_dir: Path, workflow_id: str) -> None:
    if not workflow_id.startswith("greenrock-"):
        return
    context = load_executive_context(output_dir)
    context["selected_workspace"] = "greenrock"
    context["last_viewed_workflow_id"] = workflow_id
    context["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = executive_context_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")


def executive_timeline(
    audits,
    reports,
    approvals,
    artifacts,
    limit: int = 8,
) -> tuple[ExecutiveEvent, ...]:
    events: list[ExecutiveEvent] = []
    for approval in approvals:
        if approval.status.value == "pending":
            events.append(
                ExecutiveEvent(
                    timestamp=approval.requested_at or "",
                    label="Approval Requested",
                    detail=f"Report approval {approval.id} is waiting for Managing Director review.",
                    href=f"/approvals/{approval.id}",
                    identity=f"approval-requested-{approval.id}",
                )
            )
        elif approval.status.value == "approved":
            events.append(
                ExecutiveEvent(
                    timestamp=approval.decided_at or approval.requested_at or "",
                    label="Report Approved",
                    detail=f"Approval {approval.id} approved. Distribution remains separate and disabled.",
                    href=f"/approvals/{approval.id}",
                    identity=f"approval-approved-{approval.id}",
                )
            )
        elif approval.status.value == "rejected":
            events.append(
                ExecutiveEvent(
                    timestamp=approval.decided_at or approval.requested_at or "",
                    label="Report Rejected",
                    detail=f"Approval {approval.id} rejected.",
                    href=f"/approvals/{approval.id}",
                    identity=f"approval-rejected-{approval.id}",
                )
            )
    for report in reports:
        label = "Report Assembled" if report.status != "approved" else "Approved Report Available"
        events.append(
            ExecutiveEvent(
                timestamp=report.approved_at or report.created_at,
                label=label,
                detail=f"{report.title} ({report.status})",
                href=f"/greenrock/reports/{report.run_id}/review" if report.run_id else "/greenrock",
                identity=f"report-{report.id}-{report.status}",
            )
        )
    for artifact in artifacts:
        if artifact.artifact_type == "report_final_pdf":
            events.append(
                ExecutiveEvent(
                    timestamp=artifact.created_at,
                    label="PDF Generated",
                    detail=f"Local approved PDF artifact for {artifact.run_id}.",
                    href=f"/artifacts/{artifact.id}",
                    identity=f"pdf-{artifact.id}",
                )
            )
    for audit in audits:
        if audit.action in {"staging_candidate_replaced", "approval_approved", "approval_rejected"}:
            events.append(
                ExecutiveEvent(
                    timestamp=audit.created_at.isoformat(timespec="seconds"),
                    label=_audit_label(audit.action),
                    detail=audit.detail or audit.action,
                    href="/reports",
                    identity=f"audit-{audit.id}",
                )
            )
    deduped: dict[str, ExecutiveEvent] = {}
    for event in events:
        deduped.setdefault(event.identity, event)
    return tuple(sorted(deduped.values(), key=lambda event: (event.timestamp, event.identity), reverse=True)[:limit])


def _audit_label(action: str) -> str:
    return {
        "staging_candidate_replaced": "Staging Replacement",
        "approval_approved": "Report Approved",
        "approval_rejected": "Report Rejected",
    }.get(action, action.replace("_", " ").title())
