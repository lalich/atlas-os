"""Human approval records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ApprovalRequest:
    artifact_type: str
    artifact_path: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    notes: str | None = None


def require_human_approval(artifact_type: str, artifact_path: str | None = None) -> ApprovalRequest:
    return ApprovalRequest(artifact_type=artifact_type, artifact_path=artifact_path)

