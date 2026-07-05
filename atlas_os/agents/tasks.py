"""Local agent task records for inspectable workflow work."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


TASKS_ROOT = Path("agents") / "tasks"
TASK_STATUSES = {"queued", "running", "completed", "blocked", "failed"}


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    agent_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    input_summary: str
    output_summary: str
    related_scan_id: str | None = None
    related_daily_id: str | None = None
    related_report_run_id: str | None = None
    related_approval_id: int | None = None
    target_url: str = ""
    operator_action_required: str = ""


def agent_tasks_dir(output_dir: Path) -> Path:
    return Path(output_dir) / TASKS_ROOT


def list_agent_tasks(output_dir: Path) -> tuple[AgentTask, ...]:
    directory = agent_tasks_dir(output_dir)
    if not directory.exists():
        return ()
    tasks = []
    for path in directory.glob("*.json"):
        try:
            tasks.append(_task_from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return tuple(sorted(tasks, key=lambda task: task.updated_at, reverse=True))


def get_agent_task(output_dir: Path, task_id: str) -> AgentTask:
    clean = task_id.strip().removesuffix(".json")
    if not clean or "/" in clean or ".." in clean:
        raise KeyError(task_id)
    path = agent_tasks_dir(output_dir) / f"{clean}.json"
    if not path.exists():
        raise KeyError(task_id)
    return _task_from_dict(json.loads(path.read_text(encoding="utf-8")))


def upsert_agent_task(
    output_dir: Path,
    agent_id: str,
    title: str,
    status: str,
    input_summary: str,
    output_summary: str,
    related_scan_id: str | None = None,
    related_daily_id: str | None = None,
    related_report_run_id: str | None = None,
    related_approval_id: int | None = None,
    target_url: str = "",
    operator_action_required: str = "",
) -> AgentTask:
    now = _now()
    normalized = status if status in TASK_STATUSES else "queued"
    existing = next((task for task in list_agent_tasks(output_dir) if task.agent_id == agent_id and task.title == title), None)
    task = AgentTask(
        task_id=existing.task_id if existing else "agent-task-" + now.replace("+00:00", "Z").replace(":", "").replace("-", "") + "-" + uuid4().hex[:8],
        agent_id=agent_id,
        title=title,
        status=normalized,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        input_summary=input_summary,
        output_summary=output_summary,
        related_scan_id=related_scan_id,
        related_daily_id=related_daily_id,
        related_report_run_id=related_report_run_id,
        related_approval_id=related_approval_id,
        target_url=target_url,
        operator_action_required=operator_action_required,
    )
    _write_task(output_dir, task)
    return task


def _write_task(output_dir: Path, task: AgentTask) -> None:
    directory = agent_tasks_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{task.task_id}.json").write_text(json.dumps(asdict(task), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _task_from_dict(item: dict) -> AgentTask:
    return AgentTask(
        task_id=str(item.get("task_id", "")),
        agent_id=str(item.get("agent_id", "")),
        title=str(item.get("title", "")),
        status=str(item.get("status", "queued")) if str(item.get("status", "queued")) in TASK_STATUSES else "queued",
        created_at=str(item.get("created_at", "")),
        updated_at=str(item.get("updated_at", item.get("created_at", ""))),
        input_summary=str(item.get("input_summary", "")),
        output_summary=str(item.get("output_summary", "")),
        related_scan_id=item.get("related_scan_id"),
        related_daily_id=item.get("related_daily_id"),
        related_report_run_id=item.get("related_report_run_id"),
        related_approval_id=item.get("related_approval_id"),
        target_url=str(item.get("target_url", "")),
        operator_action_required=str(item.get("operator_action_required", "")),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
