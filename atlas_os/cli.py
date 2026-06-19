"""Command line interface for Atlas OS."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from atlas_os import __version__
from atlas_os.config import get_settings
from atlas_os.core.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    approve_approval,
    get_approval,
    list_approvals,
    reject_approval,
)
from atlas_os.core.artifacts import get_artifact, list_artifacts, list_artifacts_for_run
from atlas_os.core.audit_log import get_audit_log, list_audit_logs
from atlas_os.core.reports import ReportRecord, list_reports, list_reports_for_run
from atlas_os.core.workflow_runs import WorkflowRun, get_workflow_run, list_workflow_runs
from atlas_os.core.workflow_steps import list_workflow_steps
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.report import build_sample_report
from atlas_os.greenrock.screener import run_screen
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow
from atlas_os.logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Atlas OS local workflow runner.",
    )
    parser.add_argument("--version", action="version", version=f"atlas {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show local Atlas OS status.")
    subparsers.add_parser("dashboard", help="Show analyst-friendly Atlas OS overview.")

    runs = subparsers.add_parser("runs", help="Workflow run inspection commands.")
    runs_subparsers = runs.add_subparsers(dest="runs_command")
    runs_subparsers.add_parser("list", help="List workflow runs.")
    runs_show = runs_subparsers.add_parser("show", help="Show one workflow run.")
    runs_show.add_argument("run_id")

    artifacts = subparsers.add_parser("artifacts", help="Artifact inspection commands.")
    artifact_subparsers = artifacts.add_subparsers(dest="artifact_command")
    artifact_subparsers.add_parser("list", help="List stored artifacts.")
    artifact_show = artifact_subparsers.add_parser("show", help="Show one artifact.")
    artifact_show.add_argument("artifact_id", type=int)

    audit = subparsers.add_parser("audit", help="Audit log inspection commands.")
    audit_subparsers = audit.add_subparsers(dest="audit_command")
    audit_subparsers.add_parser("list", help="List audit log entries.")
    audit_show = audit_subparsers.add_parser("show", help="Show one audit log entry.")
    audit_show.add_argument("audit_id", type=int)

    approvals = subparsers.add_parser("approvals", help="Approval queue commands.")
    approval_subparsers = approvals.add_subparsers(dest="approval_command")
    approval_subparsers.add_parser("list", help="List approval queue records.")
    approval_subparsers.add_parser("pending", help="List pending approval queue records.")
    approval_subparsers.add_parser("latest", help="Show the latest approval record.")
    approval_show = approval_subparsers.add_parser("show", help="Show one approval record.")
    approval_show.add_argument("approval_id", type=int)
    approval_approve = approval_subparsers.add_parser("approve", help="Approve one pending record.")
    approval_approve.add_argument("approval_id", type=int)
    approval_reject = approval_subparsers.add_parser("reject", help="Reject one pending record.")
    approval_reject.add_argument("approval_id", type=int)

    greenrock = subparsers.add_parser("greenrock", help="GreenRock Analysts commands.")
    greenrock_subparsers = greenrock.add_subparsers(dest="greenrock_command")
    greenrock_subparsers.add_parser(
        "sample-report",
        help="Generate a local sample GreenRock report from mock data.",
    )
    greenrock_subparsers.add_parser(
        "run-screen",
        help="Run the local GreenRock mock screening engine and write CSV outputs.",
    )
    greenrock_subparsers.add_parser(
        "candidates",
        help="Print the current local GreenRock selected candidates.",
    )
    greenrock_subparsers.add_parser(
        "report-draft",
        help="Generate a local GreenRock draft report from mock screening data.",
    )
    latest_report = greenrock_subparsers.add_parser(
        "latest-report",
        help="Show the latest GreenRock report path.",
    )
    latest_report.add_argument(
        "--print",
        action="store_true",
        dest="print_contents",
        help="Print the latest report contents.",
    )
    greenrock_subparsers.add_parser(
        "latest-run",
        help="Show the latest GreenRock workflow run.",
    )
    greenrock_subparsers.add_parser(
        "latest-candidates",
        help="Show candidate summaries from the latest GreenRock run.",
    )

    return parser


def run_status() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    print("Atlas OS status")
    print(f"version: {__version__}")
    print(f"environment: {settings.env}")
    print(f"database: {db_path}")
    print("external services: disabled")
    print("approval gate: required for client-facing publication")
    return 0


def run_greenrock_sample_report() -> int:
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    report = build_sample_report()
    output_path = Path(settings.output_dir) / "greenrock_sample_report.md"
    output_path.write_text(report.markdown, encoding="utf-8")
    print(f"Sample GreenRock report created: {output_path}")
    print("This is mock data only and is not approved for publication.")
    return 0


def run_greenrock_screen() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        workflow_run, artifacts, approval = run_greenrock_screening_workflow(
            connection,
            settings.output_dir,
            include_report_draft=True,
        )
    print("GreenRock local screen complete")
    print(f"run_id: {workflow_run.run_id}")
    print(f"status: {workflow_run.status}")
    print(f"artifacts: {len(artifacts)}")
    print(f"approval_id: {approval.id if approval else 'none'}")
    print("Mock data only. No external services were used.")
    return 0


def run_greenrock_candidates() -> int:
    result = run_screen()
    print("GreenRock selected candidates")
    print("bucket symbol score company")
    for candidate in result.selected:
        print(
            f"{candidate.market_cap_bucket} "
            f"{candidate.symbol} "
            f"{candidate.score:.2f} "
            f"{candidate.company_name}"
        )
    return 0


def run_greenrock_report_draft() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        workflow_run, artifacts, approval = run_greenrock_screening_workflow(
            connection,
            settings.output_dir,
            include_report_draft=True,
        )
    print("GreenRock report draft created")
    print(f"run_id: {workflow_run.run_id}")
    print(f"status: {workflow_run.status}")
    print(f"artifacts: {len(artifacts)}")
    print(f"approval_id: {approval.id if approval else 'none'}")
    print("Draft is blocked until approved by a human.")
    return 0


def run_greenrock_latest_report(print_contents: bool = False) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_report = _latest_greenrock_report(connection)
    if latest_report is None:
        print("No GreenRock reports found.")
        return 0
    print("Latest GreenRock report")
    print(f"run_id: {latest_report.run_id}")
    print(f"status: {latest_report.status}")
    print(f"path: {latest_report.content_path}")
    if print_contents and latest_report.content_path:
        print("")
        print(Path(latest_report.content_path).read_text(encoding="utf-8"))
    return 0


def run_greenrock_latest_run() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_run = _latest_greenrock_run(connection)
        if latest_run is None:
            print("No GreenRock runs found.")
            return 0
        approvals = _approvals_for_run(connection, latest_run.run_id)
        artifacts = list_artifacts_for_run(connection, latest_run.run_id)
    approval_status = approvals[0].status.value if approvals else "none"
    print("Latest GreenRock run")
    print(f"run_id: {latest_run.run_id}")
    print(f"status: {latest_run.status}")
    print(f"approval_status: {approval_status}")
    print(f"artifact_count: {len(artifacts)}")
    print(f"started_at: {latest_run.started_at}")
    print(f"completed_at: {latest_run.completed_at}")
    return 0


def run_greenrock_latest_candidates() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_run = _latest_greenrock_run(connection)
        if latest_run is None:
            print("No GreenRock runs found.")
            return 0
    print("Latest GreenRock candidates")
    print(f"run_id: {latest_run.run_id}")
    _print_candidate_file("Large-cap candidates", latest_run.output_paths.get("large_cap"))
    _print_candidate_file("Small-cap candidates", latest_run.output_paths.get("small_cap"))
    return 0


def run_approvals_list() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approvals = list_approvals(connection)
    print("Approval queue")
    if not approvals:
        print("No approval records found.")
        return 0
    print("id status artifact_type run_id artifact_path")
    for approval in approvals:
        print(
            f"{approval.id} "
            f"{approval.status.value} "
            f"{approval.artifact_type} "
            f"{approval.run_id or '-'} "
            f"{approval.artifact_path or '-'}"
        )
    return 0


def run_approvals_pending() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approvals = tuple(
            approval
            for approval in list_approvals(connection)
            if approval.status == ApprovalStatus.PENDING
        )
    print("Pending approvals")
    if not approvals:
        print("No pending approvals found.")
        return 0
    _print_approval_rows(approvals)
    return 0


def run_approvals_latest() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approvals = list_approvals(connection)
    if not approvals:
        print("No approval records found.")
        return 0
    approval = approvals[0]
    print("Latest approval")
    _print_approval_detail(approval)
    return 0


def run_approvals_show(approval_id: int) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = get_approval(connection, approval_id)
    _print_approval_detail(approval)
    return 0


def run_approvals_approve(approval_id: int) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = approve_approval(connection, approval_id)
    print(f"Approval {approval.id} approved")
    print("Report draft may now be used according to human-approved workflow rules.")
    return 0


def run_approvals_reject(approval_id: int) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = reject_approval(connection, approval_id)
    print(f"Approval {approval.id} rejected")
    print("Report draft remains blocked from client-facing use.")
    return 0


def run_runs_list() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        runs = list_workflow_runs(connection)
    print("Workflow runs")
    if not runs:
        print("No workflow runs found.")
        return 0
    print("run_id status division workflow_name mock_data_used started_at")
    for workflow_run in runs:
        print(
            f"{workflow_run.run_id} "
            f"{workflow_run.status} "
            f"{workflow_run.division} "
            f"{workflow_run.workflow_name} "
            f"{workflow_run.mock_data_used} "
            f"{workflow_run.started_at}"
        )
    return 0


def run_runs_show(run_id: str) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        workflow_run = get_workflow_run(connection, run_id)
        steps = list_workflow_steps(connection, run_id)
        artifacts = list_artifacts_for_run(connection, run_id)
        reports = list_reports_for_run(connection, run_id)
    print(f"Run {workflow_run.run_id}")
    print(f"division: {workflow_run.division}")
    print(f"workflow_name: {workflow_run.workflow_name}")
    print(f"status: {workflow_run.status}")
    print(f"started_at: {workflow_run.started_at}")
    print(f"completed_at: {workflow_run.completed_at}")
    print(f"mock_data_used: {workflow_run.mock_data_used}")
    print("output_paths:")
    for key, path in workflow_run.output_paths.items():
        print(f"  {key}: {path}")
    print("steps:")
    for step in steps:
        print(f"  {step.id} {step.step_name} {step.status.value}")
    print("artifacts:")
    for artifact in artifacts:
        print(f"  {artifact.id} {artifact.artifact_type} {artifact.path}")
    print("reports:")
    for report in reports:
        print(f"  {report.id} {report.status} approval_id={report.approval_id} {report.content_path}")
    return 0


def run_artifacts_list() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        artifacts = list_artifacts(connection)
    print("Artifacts")
    if not artifacts:
        print("No artifacts found.")
        return 0
    print("id run_id artifact_type path")
    for artifact in artifacts:
        print(f"{artifact.id} {artifact.run_id} {artifact.artifact_type} {artifact.path}")
    return 0


def run_artifacts_show(artifact_id: int) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        artifact = get_artifact(connection, artifact_id)
    print(f"Artifact {artifact.id}")
    print(f"run_id: {artifact.run_id}")
    print(f"artifact_type: {artifact.artifact_type}")
    print(f"path: {artifact.path}")
    print(f"created_at: {artifact.created_at}")
    return 0


def run_audit_list() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        audit_logs = list_audit_logs(connection)
    print("Audit logs")
    if not audit_logs:
        print("No audit logs found.")
        return 0
    print("id action actor run_id detail")
    for event in audit_logs:
        print(f"{event.id} {event.action} {event.actor} {event.run_id or '-'} {event.detail or '-'}")
    return 0


def run_audit_show(audit_id: int) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        event = get_audit_log(connection, audit_id)
    print(f"Audit {event.id}")
    print(f"action: {event.action}")
    print(f"actor: {event.actor}")
    print(f"run_id: {event.run_id}")
    print(f"artifact_id: {event.artifact_id}")
    print(f"approval_id: {event.approval_id}")
    print(f"detail: {event.detail}")
    print(f"created_at: {event.created_at.isoformat()}")
    return 0


def run_dashboard() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        runs = list_workflow_runs(connection)
        approvals = list_approvals(connection)
        artifacts = list_artifacts(connection)
        latest_report = _latest_greenrock_report(connection)

    pending = tuple(approval for approval in approvals if approval.status == ApprovalStatus.PENDING)
    print("Atlas OS Dashboard")
    print(f"environment: {settings.env}")
    print(f"total_runs: {len(runs)}")
    print(f"pending_approvals: {len(pending)}")
    print(f"artifact_count: {len(artifacts)}")
    print("")
    print("Recent runs")
    if runs:
        for workflow_run in runs[:5]:
            print(f"{workflow_run.run_id} {workflow_run.status} {workflow_run.workflow_name}")
    else:
        print("No workflow runs found.")
    print("")
    print("Pending approvals")
    if pending:
        for approval in pending[:5]:
            print(f"{approval.id} {approval.artifact_type} {approval.run_id} {approval.artifact_path}")
    else:
        print("No pending approvals found.")
    print("")
    print("Latest GreenRock report")
    if latest_report:
        print(f"run_id: {latest_report.run_id}")
        print(f"status: {latest_report.status}")
        print(f"path: {latest_report.content_path}")
    else:
        print("No GreenRock reports found.")
    return 0


def _print_approval_rows(approvals: tuple[ApprovalRequest, ...]) -> None:
    print("id status artifact_type run_id artifact_path")
    for approval in approvals:
        print(
            f"{approval.id} "
            f"{approval.status.value} "
            f"{approval.artifact_type} "
            f"{approval.run_id or '-'} "
            f"{approval.artifact_path or '-'}"
        )


def _print_approval_detail(approval: ApprovalRequest) -> None:
    print(f"Approval {approval.id}")
    print(f"status: {approval.status.value}")
    print(f"run_id: {approval.run_id}")
    print(f"artifact_id: {approval.artifact_id}")
    print(f"artifact_type: {approval.artifact_type}")
    print(f"artifact_path: {approval.artifact_path}")
    print(f"requested_at: {approval.requested_at}")
    print(f"decided_at: {approval.decided_at}")
    print(f"decided_by: {approval.decided_by}")
    print(f"notes: {approval.notes}")


def _latest_greenrock_run(connection) -> WorkflowRun | None:
    for workflow_run in list_workflow_runs(connection):
        if workflow_run.division == "greenrock":
            return workflow_run
    return None


def _latest_greenrock_report(connection) -> ReportRecord | None:
    reports_by_run = {}
    for report in list_reports(connection):
        if report.run_id and report.run_id not in reports_by_run:
            reports_by_run[report.run_id] = report
    for workflow_run in list_workflow_runs(connection):
        if workflow_run.division == "greenrock" and workflow_run.run_id in reports_by_run:
            return reports_by_run[workflow_run.run_id]
    return None


def _approvals_for_run(connection, run_id: str) -> tuple[ApprovalRequest, ...]:
    return tuple(approval for approval in list_approvals(connection) if approval.run_id == run_id)


def _print_candidate_file(title: str, path: str | None) -> None:
    print("")
    print(title)
    if not path:
        print("No candidate file found.")
        return
    candidate_path = Path(path)
    if not candidate_path.exists():
        print(f"Missing candidate file: {candidate_path}")
        return
    print("symbol score market_cap company")
    with candidate_path.open(newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            print(f"{row['symbol']} {row['score']} {row['market_cap']} {row['company_name']}")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return run_status()

    if args.command == "dashboard":
        return run_dashboard()

    if args.command == "greenrock":
        if args.greenrock_command == "sample-report":
            return run_greenrock_sample_report()
        if args.greenrock_command == "run-screen":
            return run_greenrock_screen()
        if args.greenrock_command == "candidates":
            return run_greenrock_candidates()
        if args.greenrock_command == "report-draft":
            return run_greenrock_report_draft()
        if args.greenrock_command == "latest-report":
            return run_greenrock_latest_report(args.print_contents)
        if args.greenrock_command == "latest-run":
            return run_greenrock_latest_run()
        if args.greenrock_command == "latest-candidates":
            return run_greenrock_latest_candidates()
        parser.error("greenrock requires a subcommand")

    if args.command == "approvals":
        if args.approval_command == "list":
            return run_approvals_list()
        if args.approval_command == "pending":
            return run_approvals_pending()
        if args.approval_command == "latest":
            return run_approvals_latest()
        if args.approval_command == "show":
            return run_approvals_show(args.approval_id)
        if args.approval_command == "approve":
            return run_approvals_approve(args.approval_id)
        if args.approval_command == "reject":
            return run_approvals_reject(args.approval_id)
        parser.error("approvals requires a subcommand")

    if args.command == "runs":
        if args.runs_command == "list":
            return run_runs_list()
        if args.runs_command == "show":
            return run_runs_show(args.run_id)
        parser.error("runs requires a subcommand")

    if args.command == "artifacts":
        if args.artifact_command == "list":
            return run_artifacts_list()
        if args.artifact_command == "show":
            return run_artifacts_show(args.artifact_id)
        parser.error("artifacts requires a subcommand")

    if args.command == "audit":
        if args.audit_command == "list":
            return run_audit_list()
        if args.audit_command == "show":
            return run_audit_show(args.audit_id)
        parser.error("audit requires a subcommand")

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
