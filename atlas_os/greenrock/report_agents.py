"""Local GreenRock report-agent orchestration.

This workflow coordinates local research handoffs and draft assembly only. It
does not send, publish, trade, contact clients, call a broker, or run external
LLM/API actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from atlas_os.core.artifacts import create_artifact
from atlas_os.core.audit_log import create_audit_log
from atlas_os.core.workflow_runs import complete_workflow_run, create_workflow_run
from atlas_os.greenrock.report_dry_run import create_report_dry_run
from atlas_os.greenrock.scanner import latest_scan
from atlas_os.greenrock.staging import load_staged_candidates


WORKFLOW_NAME = "greenrock.report_agent_orchestration"
WORKFLOW_STATES = (
    "pending",
    "running",
    "blocked",
    "completed",
    "failed",
    "awaiting_human_approval",
    "approved",
    "rejected",
)
REPORT_AGENT_ORDER = (
    "market_scout",
    "derivative_analyst",
    "portfolio_analyst",
    "risk_officer",
    "compliance_reviewer",
    "report_writer",
    "atlas_chief_of_staff",
)
DISTRIBUTION_AGENT_ID = "distribution_agent"
TASK_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "market_scout": (),
    "derivative_analyst": ("market_scout",),
    "portfolio_analyst": ("market_scout",),
    "risk_officer": ("market_scout", "derivative_analyst", "portfolio_analyst"),
    "compliance_reviewer": ("risk_officer",),
    "report_writer": ("compliance_reviewer",),
    "atlas_chief_of_staff": ("report_writer",),
}


@dataclass(frozen=True)
class ReportAgentWorkflow:
    workflow_id: str
    status: str
    workflow_path: Path
    output_dir: Path
    final_draft_path: Path | None
    approval_status: str
    tasks: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ReportAgentApproval:
    approval_id: str
    workflow_id: str
    report_id: str
    approver: str
    decision: str
    approved_at: str
    decided_at: str
    note: str


def report_agent_workflows_dir(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / "report_agents"


def report_agent_approvals_path(output_dir: Path) -> Path:
    return report_agent_workflows_dir(output_dir) / "approvals.jsonl"


def run_greenrock_report_agent_workflow(
    connection: Connection,
    output_dir: Path,
    *,
    fail_agent: str | None = None,
) -> ReportAgentWorkflow:
    """Run the local report-agent workflow and stop at human approval."""

    workflow_run = create_workflow_run(
        connection,
        division="greenrock",
        workflow_name=WORKFLOW_NAME,
        mock_data_used=False,
        data_mode="local",
    )
    workflow_id = workflow_run.run_id
    workflow_dir = report_agent_workflows_dir(output_dir) / workflow_id
    handoff_dir = workflow_dir / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    handoffs: dict[str, dict[str, Any]] = {}
    final_draft_path: Path | None = None
    failed_or_blocked: set[str] = set()

    create_audit_log(
        connection,
        actor="greenrock_report_agents",
        action="workflow_started",
        detail="GreenRock report-agent orchestration started.",
        run_id=workflow_id,
    )

    for role in REPORT_AGENT_ORDER:
        dependencies = TASK_DEPENDENCIES[role]
        dependency_block = tuple(dependency for dependency in dependencies if dependency in failed_or_blocked)
        if dependency_block:
            task = _blocked_task(workflow_id, role, dependency_block, handoffs)
            tasks.append(task)
            handoffs[role] = _write_handoff(connection, workflow_id, role, handoff_dir, task)
            failed_or_blocked.add(role)
            continue

        task = _running_task(workflow_id, role, handoffs)
        try:
            if role == fail_agent:
                raise RuntimeError(f"Forced failure for {role}")
            payload, warnings, output_refs = _run_role(role, output_dir, handoffs)
            if role == "report_writer":
                draft = payload.get("final_draft_path")
                final_draft_path = Path(draft) if draft else None
                if final_draft_path:
                    artifact = create_artifact(connection, workflow_id, "greenrock_report_dry_run", final_draft_path)
                    output_refs.append(_artifact_ref(artifact.id, final_draft_path))
            task.update(
                {
                    "completed_at": _now(),
                    "status": "completed",
                    "warnings": warnings,
                    "errors": [],
                    "output_artifact_refs": output_refs,
                    "summary": payload.get("summary", ""),
                    "payload": payload,
                }
            )
        except Exception as error:  # pragma: no cover - exercised via fail_agent tests
            task.update(
                {
                    "completed_at": _now(),
                    "status": "failed",
                    "warnings": [],
                    "errors": [str(error)],
                    "output_artifact_refs": [],
                    "summary": f"{role} failed.",
                    "payload": {},
                }
            )
            failed_or_blocked.add(role)

        tasks.append(task)
        handoffs[role] = _write_handoff(connection, workflow_id, role, handoff_dir, task)

    status = "failed" if any(task["status"] == "failed" for task in tasks) else (
        "blocked" if any(task["status"] == "blocked" for task in tasks) else "awaiting_human_approval"
    )
    workflow_payload = _workflow_payload(
        workflow_id,
        status,
        tasks,
        final_draft_path,
        approval_status="none" if status != "awaiting_human_approval" else "awaiting_human_approval",
    )
    workflow_path = workflow_dir / "workflow.json"
    _write_json(workflow_path, workflow_payload)
    workflow_artifact = create_artifact(connection, workflow_id, "greenrock_report_agent_workflow", workflow_path)

    output_paths: dict[str, Path] = {"workflow": workflow_path}
    if final_draft_path:
        output_paths["draft"] = final_draft_path
    complete_workflow_run(connection, workflow_id, output_paths, status=status)
    create_audit_log(
        connection,
        actor="greenrock_report_agents",
        action="workflow_completed",
        detail=f"GreenRock report-agent workflow status={status}; workflow_artifact={workflow_artifact.id}.",
        run_id=workflow_id,
        artifact_id=workflow_artifact.id,
    )
    return _workflow_from_payload(workflow_payload, workflow_path, output_dir)


def get_report_agent_workflow(output_dir: Path, workflow_id: str) -> ReportAgentWorkflow:
    path = report_agent_workflows_dir(output_dir) / workflow_id / "workflow.json"
    if not path.exists():
        raise KeyError(f"Unknown GreenRock report-agent workflow: {workflow_id}")
    return _workflow_from_payload(json.loads(path.read_text(encoding="utf-8")), path, output_dir)


def list_report_agent_workflows(output_dir: Path) -> tuple[ReportAgentWorkflow, ...]:
    root = report_agent_workflows_dir(output_dir)
    if not root.exists():
        return ()
    workflows = []
    for path in sorted(root.glob("*/workflow.json"), reverse=True):
        workflows.append(_workflow_from_payload(json.loads(path.read_text(encoding="utf-8")), path, output_dir))
    return tuple(workflows)


def approve_report_agent_workflow(
    connection: Connection,
    output_dir: Path,
    workflow_id: str,
    *,
    approver: str,
    note: str = "",
) -> ReportAgentApproval:
    return _decide_report_agent_workflow(connection, output_dir, workflow_id, "approved", approver, note)


def reject_report_agent_workflow(
    connection: Connection,
    output_dir: Path,
    workflow_id: str,
    *,
    approver: str,
    note: str = "",
) -> ReportAgentApproval:
    return _decide_report_agent_workflow(connection, output_dir, workflow_id, "rejected", approver, note)


def distribution_agent_lock_status(output_dir: Path, workflow_id: str) -> dict[str, Any]:
    """Return fail-closed distribution status for the v0.8.0-alpha release."""

    workflow = get_report_agent_workflow(output_dir, workflow_id)
    approval = _approval_for_workflow(output_dir, workflow_id)
    if approval is None or approval.decision != "approved":
        return {
            "agent_role": DISTRIBUTION_AGENT_ID,
            "status": "blocked",
            "runnable": False,
            "reason": "missing_explicit_approval_record",
            "workflow_status": workflow.status,
        }
    return {
        "agent_role": DISTRIBUTION_AGENT_ID,
        "status": "blocked",
        "runnable": False,
        "reason": "distribution_disabled_in_phase_11c",
        "workflow_status": workflow.status,
        "approval_id": approval.approval_id,
    }


def _decide_report_agent_workflow(
    connection: Connection,
    output_dir: Path,
    workflow_id: str,
    decision: str,
    approver: str,
    note: str,
) -> ReportAgentApproval:
    if decision not in {"approved", "rejected"}:
        raise ValueError(f"Unsupported decision: {decision}")
    workflow = get_report_agent_workflow(output_dir, workflow_id)
    if workflow.status not in {"awaiting_human_approval", "approved", "rejected"}:
        raise ValueError(f"Workflow {workflow_id} is {workflow.status}; it is not awaiting approval.")
    if _approval_for_workflow(output_dir, workflow_id) is not None:
        raise ValueError(f"Workflow {workflow_id} already has an approval decision.")

    decided_at = _now()
    approval = ReportAgentApproval(
        approval_id=f"greenrock-report-approval-{workflow_id}-{decision}",
        workflow_id=workflow_id,
        report_id=workflow_id,
        approver=approver,
        decision=decision,
        approved_at=decided_at if decision == "approved" else "",
        decided_at=decided_at,
        note=note,
    )
    _append_approval(output_dir, approval)
    _update_workflow_status(output_dir, workflow_id, decision, approval)
    connection.execute(
        "UPDATE workflow_runs SET status = ? WHERE run_id = ?",
        (decision, workflow_id),
    )
    connection.commit()
    create_audit_log(
        connection,
        actor=approver,
        action=f"greenrock_report_agent_{decision}",
        detail=f"{decision} workflow {workflow_id}. Distribution remains disabled in this release.",
        run_id=workflow_id,
    )
    return approval


def _run_role(role: str, output_dir: Path, handoffs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    if role == "market_scout":
        return _market_scout(output_dir)
    if role == "derivative_analyst":
        return _derivative_analyst(output_dir)
    if role == "portfolio_analyst":
        return _portfolio_analyst(output_dir)
    if role == "risk_officer":
        return _risk_officer(handoffs)
    if role == "compliance_reviewer":
        return _compliance_reviewer(handoffs)
    if role == "report_writer":
        return _report_writer(output_dir)
    if role == "atlas_chief_of_staff":
        return _chief_of_staff(handoffs)
    raise KeyError(role)


def _market_scout(output_dir: Path) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    scan = latest_scan(output_dir)
    warnings: list[str] = []
    if scan is None:
        warnings.append("No successful market scan found; market scan section will be limited.")
        payload = {"summary": "No current market scan was available.", "scan_id": "none", "scored_count": 0}
    else:
        if scan.provider_failure_count:
            warnings.append(f"{scan.provider_failure_count} provider failures were present in the latest scan.")
        payload = {
            "summary": f"Latest market scan {scan.scan_id} available with {len(scan.rows)} scored rows.",
            "scan_id": scan.scan_id,
            "population": scan.population,
            "scored_count": len(scan.rows),
            "provider_failure_count": scan.provider_failure_count,
        }
    return payload, warnings, []


def _derivative_analyst(output_dir: Path) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    analyses = _latest_derivative_analyses(output_dir)
    warnings: list[str] = []
    if not analyses:
        warnings.append("No Derivative Workbench analysis found; derivative section will explain the absence.")
    top_count = sum(len(window_rows) for analysis in analyses for key in ("top_calls", "top_puts") for window_rows in analysis.get(key, {}).values())
    excluded_count = sum(len(window_rows) for analysis in analyses for key in ("excluded_calls", "excluded_puts") for window_rows in analysis.get(key, {}).values())
    payload = {
        "summary": f"Derivative Workbench context found for {len(analyses)} ticker(s).",
        "analysis_count": len(analyses),
        "top_research_count": top_count,
        "excluded_contract_count": excluded_count,
        "otm_guardrail": "Top Research remains OTM-only.",
    }
    return payload, warnings, []


def _portfolio_analyst(output_dir: Path) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    staged = load_staged_candidates(output_dir)
    warnings = [] if staged else ["No staged Wall candidates found; portfolio context is limited to local read-only data."]
    payload = {
        "summary": f"{len(staged)} staged Wall candidate(s) available for report context.",
        "staged_count": len(staged),
        "read_only_position_context": True,
        "brokerage_execution": "disabled",
    }
    return payload, warnings, []


def _risk_officer(handoffs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    inherited_warnings = _prior_warnings(handoffs)
    risk_flags = tuple(warning for warning in inherited_warnings if "No " in warning or "provider" in warning)
    payload = {
        "summary": f"Risk review completed with {len(risk_flags)} flag(s).",
        "risk_flags": risk_flags,
        "review_required": True,
    }
    return payload, list(risk_flags), []


def _compliance_reviewer(handoffs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    warnings = ["Human review is required before any use outside local draft review."]
    payload = {
        "summary": "Compliance review confirms local draft-only boundaries.",
        "compliance_flags": tuple(_prior_warnings(handoffs)),
        "email": "disabled",
        "publishing": "disabled",
        "brokerage": "disabled",
        "external_llm_api": "disabled",
        "approval_required": True,
    }
    return payload, warnings, []


def _report_writer(output_dir: Path) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    draft_path = create_report_dry_run(
        output_dir,
        scheduled_for="agent_orchestration",
        schedule_reason="greenrock_report_agent_orchestration",
    )
    payload = {
        "summary": "Review-only GreenRock report dry run assembled.",
        "final_draft_path": str(draft_path),
        "review_required": True,
    }
    return payload, [], []


def _chief_of_staff(handoffs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    report_writer = handoffs.get("report_writer", {}).get("payload", {})
    risk = handoffs.get("risk_officer", {}).get("payload", {})
    compliance = handoffs.get("compliance_reviewer", {}).get("payload", {})
    missing_data = tuple(_prior_warnings(handoffs))
    payload = {
        "summary": "Report-agent workflow completed and is awaiting human approval.",
        "completed_research": tuple(role for role in REPORT_AGENT_ORDER if role in handoffs),
        "disagreements_or_conflicts": tuple(warning for warning in missing_data if "conflict" in warning.lower()),
        "risk_flags": tuple(risk.get("risk_flags", ())),
        "compliance_flags": tuple(compliance.get("compliance_flags", ())),
        "missing_data": missing_data,
        "final_draft_location": report_writer.get("final_draft_path", ""),
        "approval_status": "awaiting_human_approval",
    }
    return payload, [], []


def _latest_derivative_analyses(output_dir: Path) -> tuple[dict[str, Any], ...]:
    root = Path(output_dir) / "greenrock" / "derivatives" / "snapshots"
    if not root.exists():
        return ()
    analyses: list[tuple[str, str, dict[str, Any]]] = []
    for path in root.glob("*/*/analysis.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        analyses.append((str(payload.get("created_at", "")), str(path), payload))
    analyses.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(item[2] for item in analyses[:10])


def _running_task(workflow_id: str, role: str, handoffs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_id": f"{workflow_id}-{role}",
        "agent_role": role,
        "input_artifact_refs": _input_refs(role, handoffs),
        "output_artifact_refs": [],
        "started_at": _now(),
        "completed_at": "",
        "status": "running",
        "warnings": [],
        "errors": [],
        "summary": "",
        "payload": {},
    }


def _blocked_task(workflow_id: str, role: str, blocked_by: tuple[str, ...], handoffs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    timestamp = _now()
    return {
        "task_id": f"{workflow_id}-{role}",
        "agent_role": role,
        "input_artifact_refs": _input_refs(role, handoffs),
        "output_artifact_refs": [],
        "started_at": timestamp,
        "completed_at": timestamp,
        "status": "blocked",
        "warnings": [],
        "errors": [f"Blocked by upstream task(s): {', '.join(blocked_by)}."],
        "summary": f"{role} blocked by upstream task.",
        "payload": {"blocked_by": blocked_by},
    }


def _write_handoff(
    connection: Connection,
    workflow_id: str,
    role: str,
    handoff_dir: Path,
    task: dict[str, Any],
) -> dict[str, Any]:
    path = handoff_dir / f"{role}.json"
    _write_json(path, task)
    artifact = create_artifact(connection, workflow_id, "greenrock_report_agent_handoff", path)
    task["output_artifact_refs"].append(_artifact_ref(artifact.id, path))
    _write_json(path, task)
    return dict(task)


def _input_refs(role: str, handoffs: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for dependency in TASK_DEPENDENCIES[role]:
        dependency_handoff = handoffs.get(dependency)
        if dependency_handoff:
            refs.extend(dependency_handoff.get("output_artifact_refs", []))
    return refs


def _prior_warnings(handoffs: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    warnings: list[str] = []
    for role in REPORT_AGENT_ORDER:
        handoff = handoffs.get(role)
        if handoff:
            warnings.extend(str(warning) for warning in handoff.get("warnings", ()))
    return tuple(warnings)


def _workflow_payload(
    workflow_id: str,
    status: str,
    tasks: list[dict[str, Any]],
    final_draft_path: Path | None,
    *,
    approval_status: str,
) -> dict[str, Any]:
    warnings = tuple(warning for task in tasks for warning in task.get("warnings", ()))
    errors = tuple(error for task in tasks for error in task.get("errors", ()))
    chief = next((task.get("payload", {}) for task in tasks if task.get("agent_role") == "atlas_chief_of_staff"), {})
    return {
        "workflow_id": workflow_id,
        "workflow_name": WORKFLOW_NAME,
        "status": status,
        "states": WORKFLOW_STATES,
        "approval_status": approval_status,
        "distribution_agent": distribution_agent_registration(),
        "agent_dependency_graph": {key: list(value) for key, value in TASK_DEPENDENCIES.items()},
        "tasks": tasks,
        "warnings": warnings,
        "errors": errors,
        "final_draft_path": str(final_draft_path) if final_draft_path else "",
        "chief_of_staff_summary": chief,
        "safety_boundary": "Local draft generation only; no distribution, email, publishing, brokerage, orders, credentials, client contact, or external LLM/API action.",
    }


def distribution_agent_registration() -> dict[str, Any]:
    return {
        "agent_role": DISTRIBUTION_AGENT_ID,
        "registered": True,
        "enabled": False,
        "runnable": False,
        "phase_11c_behavior": "fail_closed",
    }


def _workflow_from_payload(payload: dict[str, Any], workflow_path: Path, output_dir: Path) -> ReportAgentWorkflow:
    draft = payload.get("final_draft_path") or ""
    return ReportAgentWorkflow(
        workflow_id=str(payload["workflow_id"]),
        status=str(payload["status"]),
        workflow_path=workflow_path,
        output_dir=Path(output_dir),
        final_draft_path=Path(draft) if draft else None,
        approval_status=str(payload.get("approval_status", "none")),
        tasks=tuple(payload.get("tasks", ())),
        warnings=tuple(payload.get("warnings", ())),
        errors=tuple(payload.get("errors", ())),
    )


def _append_approval(output_dir: Path, approval: ReportAgentApproval) -> None:
    path = report_agent_approvals_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(approval.__dict__, sort_keys=True) + "\n")


def _approval_for_workflow(output_dir: Path, workflow_id: str) -> ReportAgentApproval | None:
    path = report_agent_approvals_path(output_dir)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("workflow_id") == workflow_id:
            return ReportAgentApproval(**item)
    return None


def _update_workflow_status(output_dir: Path, workflow_id: str, status: str, approval: ReportAgentApproval) -> None:
    path = report_agent_workflows_dir(output_dir) / workflow_id / "workflow.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = status
    payload["approval_status"] = status
    payload["approval_record"] = approval.__dict__
    _write_json(path, payload)


def _artifact_ref(artifact_id: int, path: Path) -> dict[str, str]:
    return {"artifact_id": str(artifact_id), "path": str(path)}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
