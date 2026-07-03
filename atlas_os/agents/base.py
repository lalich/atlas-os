"""Base types for local agent definitions and run records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str
    division: str
    responsibility: str
    status: Literal["idle", "running", "completed", "failed", "blocked"] = "idle"
    last_run_at: str | None = None
    last_message: str = "Not run yet."
    current_task: str = ""
    output_summary: str = ""
    health: Literal["green", "yellow", "red", "blocked", "unknown"] = "unknown"

    @property
    def key(self) -> str:
        return self.agent_id

    @property
    def description(self) -> str:
        return self.responsibility


@dataclass(frozen=True)
class AgentRun:
    run_id: str
    agent_id: str
    started_at: str
    completed_at: str | None
    status: Literal["idle", "running", "completed", "failed", "blocked"]
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    related_scan_id: str | None = None
    related_report_run_id: str | None = None
    related_approval_id: int | None = None
