"""GreenRock local workflow using the reusable runner."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from atlas_os.core.approvals import ApprovalRequest, create_approval_request
from atlas_os.core.artifacts import Artifact
from atlas_os.core.reports import create_report_record
from atlas_os.core.workflow_runner import WorkflowContext, WorkflowExecution, WorkflowRunner, WorkflowStep
from atlas_os.core.workflow_runs import WorkflowRun
from atlas_os.greenrock.market_data import MarketDataProvider, MockMarketDataProvider, get_market_data_provider
from atlas_os.greenrock.report import build_report_draft
from atlas_os.greenrock.screener import run_screen, write_screen_outputs


WORKFLOW_NAME = "greenrock.local-screening"


def run_greenrock_screening_workflow(
    connection: Connection,
    output_dir: Path,
    include_report_draft: bool = True,
    data_mode: str = "mock",
    provider: MarketDataProvider | None = None,
) -> tuple[WorkflowRun, tuple[Artifact, ...], ApprovalRequest | None]:
    market_data_provider = provider or get_market_data_provider(data_mode)
    steps = [WorkflowStep("screen_candidates", _screen_candidates_step(market_data_provider))]
    if include_report_draft:
        steps.append(WorkflowStep("draft_report", _draft_report))

    execution = run_greenrock_workflow(
        connection,
        output_dir,
        tuple(steps),
        data_mode=market_data_provider.data_mode,
    )
    approval = None
    if execution.approval_id is not None:
        from atlas_os.core.approvals import get_approval

        approval = get_approval(connection, execution.approval_id)
    return execution.run, execution.artifacts, approval


def run_greenrock_workflow(
    connection: Connection,
    output_dir: Path,
    steps: tuple[WorkflowStep, ...] | None = None,
    data_mode: str = "mock",
) -> WorkflowExecution:
    default_provider = MockMarketDataProvider()
    runner = WorkflowRunner(
        connection=connection,
        division="greenrock",
        workflow_name=WORKFLOW_NAME,
        steps=steps
        or (
            WorkflowStep("screen_candidates", _screen_candidates_step(default_provider)),
            WorkflowStep("draft_report", _draft_report),
        ),
        output_dir=output_dir,
        mock_data_used=data_mode == "mock",
        data_mode=data_mode,
    )
    return runner.run()


def _screen_candidates_step(provider: MarketDataProvider):
    def _screen_candidates(context: WorkflowContext) -> None:
        result = run_screen(provider)
        context.greenrock_screening_result = result
        paths = write_screen_outputs(result, context.output_dir)
        context.record_artifact("candidates_csv", paths["all"], output_key="all")
        context.record_artifact("large_cap_csv", paths["large_cap"], output_key="large_cap")
        context.record_artifact("small_cap_csv", paths["small_cap"], output_key="small_cap")

    return _screen_candidates


def _draft_report(context: WorkflowContext) -> None:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    screening = getattr(context, "greenrock_screening_result", None)
    report = build_report_draft(
        run_id=context.run.run_id,
        screening=screening,
        data_mode=context.run.data_mode,
        data_source=screening.data_source if screening else context.run.data_mode,
    )
    report_path = context.output_dir / "greenrock_report_draft.md"
    report_path.write_text(report.markdown, encoding="utf-8")
    report_artifact = context.record_artifact(
        "report_draft_md",
        report_path,
        output_key="report_draft",
    )
    approval = create_approval_request(
        context.connection,
        artifact_type="report_draft_md",
        artifact_path=str(report_path),
        run_id=context.run.run_id,
        artifact_id=report_artifact.id,
        notes="Blocked until explicitly approved by a human.",
    )
    create_report_record(
        context.connection,
        title=report.title,
        report_type="greenrock_monthly_draft",
        content_path=str(report_path),
        run_id=context.run.run_id,
        artifact_id=report_artifact.id,
        approval_id=approval.id,
        status="blocked_for_approval",
    )
    context.approval_id = approval.id
    context.blocked_for_approval = True
