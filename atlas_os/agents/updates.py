"""Structured local agent update records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


UPDATES_ROOT = Path("agents") / "updates"


@dataclass(frozen=True)
class AgentUpdate:
    update_id: str
    cycle_id: str
    agent_name: str
    created_at: str
    status: str
    severity: str
    headline: str
    summary: str
    findings: tuple[str, ...] = ()
    supporting_metrics: dict = field(default_factory=dict)
    related_tickers: tuple[str, ...] = ()
    related_scan_id: str | None = None
    related_report_run_id: str | None = None
    related_approval_id: int | None = None
    recommended_operator_action: str = ""
    target_url: str = ""
    provenance: dict = field(default_factory=dict)


def agent_updates_dir(output_dir: Path) -> Path:
    return Path(output_dir) / UPDATES_ROOT


def write_agent_update(output_dir: Path, update: AgentUpdate) -> AgentUpdate:
    directory = agent_updates_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{update.update_id}.json"
    path.write_text(json.dumps(asdict(update), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return update


def list_agent_updates(output_dir: Path, agent_name: str | None = None) -> tuple[AgentUpdate, ...]:
    directory = agent_updates_dir(output_dir)
    if not directory.exists():
        return ()
    updates = []
    for path in directory.glob("*.json"):
        try:
            update = _update_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if agent_name and update.agent_name.lower().replace(" ", "-") != agent_name.lower().replace(" ", "-"):
            if update.agent_name.lower() != agent_name.lower():
                continue
        updates.append(update)
    return tuple(sorted(updates, key=lambda item: item.created_at, reverse=True))


def updates_for_cycle(output_dir: Path, cycle_id: str) -> tuple[AgentUpdate, ...]:
    return tuple(update for update in list_agent_updates(output_dir) if update.cycle_id == cycle_id)


def latest_agent_update(output_dir: Path, agent_name: str) -> AgentUpdate | None:
    updates = list_agent_updates(output_dir, agent_name)
    return updates[0] if updates else None


def get_agent_update(output_dir: Path, update_id: str) -> AgentUpdate:
    clean = update_id.strip().removesuffix(".json")
    if not clean or "/" in clean or ".." in clean:
        raise KeyError(update_id)
    path = agent_updates_dir(output_dir) / f"{clean}.json"
    if not path.exists():
        raise KeyError(update_id)
    return _update_from_dict(json.loads(path.read_text(encoding="utf-8")))


def new_update_id(cycle_id: str, agent_name: str) -> str:
    safe_agent = agent_name.lower().replace(" ", "-")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return f"agent-update-{cycle_id}-{safe_agent}-{stamp.replace(':', '').replace('-', '')}"


def _update_from_dict(item: dict) -> AgentUpdate:
    return AgentUpdate(
        update_id=str(item.get("update_id", "")),
        cycle_id=str(item.get("cycle_id", "")),
        agent_name=str(item.get("agent_name", "")),
        created_at=str(item.get("created_at", "")),
        status=str(item.get("status", "completed")),
        severity=str(item.get("severity", "info")),
        headline=str(item.get("headline", "")),
        summary=str(item.get("summary", "")),
        findings=tuple(item.get("findings", ())),
        supporting_metrics=dict(item.get("supporting_metrics", {})),
        related_tickers=tuple(item.get("related_tickers", ())),
        related_scan_id=item.get("related_scan_id"),
        related_report_run_id=item.get("related_report_run_id"),
        related_approval_id=item.get("related_approval_id"),
        recommended_operator_action=str(item.get("recommended_operator_action", "")),
        target_url=str(item.get("target_url", "")),
        provenance=dict(item.get("provenance", {})),
    )
