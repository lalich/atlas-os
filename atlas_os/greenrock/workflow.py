"""GreenRock local workflow persistence."""

from __future__ import annotations

from pathlib import Path

from atlas_os.core.approvals import ApprovalRequest, create_approval_request
from atlas_os.core.artifacts import Artifact, create_artifact
from atlas_os.core.workflow_runs import WorkflowRun, complete_workflow_run, create_workflow_run
from atlas_os.greenrock.report import build_report_draft
from atlas_os.greenrock.screener import run_screen, write_screen_outputs
from sqlite3 import Connection


WORKFLOW_NAME = "greenrock.local-screening"


def run_greenrock_screening_workflow(
    connection: Connection,
    output_dir: Path,
    include_report_draft: bool = True,
) -> tuple[WorkflowRun, tuple[Artifact, ...], ApprovalRequest | None]:
    workflow_run = create_workflow_run(
        connection,
        division="greenrock",
        workflow_name=WORKFLOW_NAME,
        mock_data_used=True,
    )

    result = run_screen()
    paths = write_screen_outputs(result, output_dir)
    output_paths = dict(paths)
    artifacts: list[Artifact] = [
        create_artifact(connection, workflow_run.run_id, "candidates_csv", paths["all"]),
        create_artifact(connection, workflow_run.run_id, "large_cap_csv", paths["large_cap"]),
        create_artifact(connection, workflow_run.run_id, "small_cap_csv", paths["small_cap"]),
    ]

    approval = None
    if include_report_draft:
        output_dir.mkdir(parents=True, exist_ok=True)
        report = build_report_draft()
        report_path = output_dir / "greenrock_report_draft.md"
        report_path.write_text(report.markdown, encoding="utf-8")
        output_paths["report_draft"] = report_path
        report_artifact = create_artifact(
            connection,
            workflow_run.run_id,
            "report_draft_md",
            report_path,
        )
        artifacts.append(report_artifact)
        approval = create_approval_request(
            connection,
            artifact_type="report_draft_md",
            artifact_path=str(report_path),
            run_id=workflow_run.run_id,
            artifact_id=report_artifact.id,
            notes="Blocked until explicitly approved by a human.",
        )

    completed_run = complete_workflow_run(
        connection,
        workflow_run.run_id,
        output_paths=output_paths,
        status="awaiting_approval" if approval else "completed",
    )
    return completed_run, tuple(artifacts), approval

