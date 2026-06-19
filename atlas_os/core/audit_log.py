"""Audit log event model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class AuditEvent:
    actor: str
    action: str
    detail: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

