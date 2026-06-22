"""GreenRock report lifecycle cleanup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from atlas_os.core.artifacts import Artifact, archive_artifact, list_artifacts
from atlas_os.core.reports import list_reports, update_report_status
from atlas_os.core.workflow_runs import list_workflow_runs


DRAFT_ARTIFACT_TYPES = {
    "candidates_csv",
    "large_cap_csv",
    "small_cap_csv",
    "report_draft_md",
}
FINAL_PDF_ARTIFACT_TYPE = "report_final_pdf"


@dataclass(frozen=True)
class CleanupResult:
    dry_run: bool
    latest_draft_run_id: str | None
    removed_files: tuple[str, ...]
    archived_artifact_ids: tuple[int, ...]
    preserved_final_pdfs: tuple[str, ...]
    preserved_latest_artifacts: tuple[str, ...]


def cleanup_greenrock_drafts(connection: Connection, dry_run: bool = False) -> CleanupResult:
    latest_draft_run_id = _latest_greenrock_draft_run_id(connection)
    artifacts = list_artifacts(connection)
    reports = list_reports(connection)
    report_by_artifact_id = {
        report.artifact_id: report
        for report in reports
        if report.artifact_id is not None
    }

    removed_files: list[str] = []
    archived_artifact_ids: list[int] = []
    preserved_final_pdfs: list[str] = []
    preserved_latest_artifacts: list[str] = []

    for artifact in artifacts:
        if not artifact.run_id.startswith("greenrock-"):
            continue
        if artifact.artifact_type == FINAL_PDF_ARTIFACT_TYPE:
            preserved_final_pdfs.append(artifact.path)
            continue
        if artifact.run_id == latest_draft_run_id:
            preserved_latest_artifacts.append(artifact.path)
            continue
        if artifact.artifact_type not in DRAFT_ARTIFACT_TYPES:
            continue

        removed_files.append(artifact.path)
        archived_artifact_ids.append(artifact.id)
        if dry_run:
            continue

        path = Path(artifact.path)
        if path.exists() and path.is_file():
            path.unlink()
        archive_artifact(connection, artifact.id)
        report = report_by_artifact_id.get(artifact.id)
        if report and report.status != "approved":
            update_report_status(connection, report.id, "archived_draft")

    return CleanupResult(
        dry_run=dry_run,
        latest_draft_run_id=latest_draft_run_id,
        removed_files=tuple(removed_files),
        archived_artifact_ids=tuple(archived_artifact_ids),
        preserved_final_pdfs=tuple(preserved_final_pdfs),
        preserved_latest_artifacts=tuple(preserved_latest_artifacts),
    )


def _latest_greenrock_draft_run_id(connection: Connection) -> str | None:
    active_draft_run_ids = {
        artifact.run_id
        for artifact in list_artifacts(connection)
        if artifact.artifact_type == "report_draft_md"
        and artifact.run_id.startswith("greenrock-")
    }
    for workflow_run in list_workflow_runs(connection):
        if workflow_run.run_id in active_draft_run_ids:
            return workflow_run.run_id
    return None
