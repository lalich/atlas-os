"""Command line interface for Atlas OS."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
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
from atlas_os.core.artifacts import create_artifact, get_artifact, list_artifacts, list_artifacts_for_run
from atlas_os.core.audit_log import create_audit_log, get_audit_log, list_audit_logs
from atlas_os.core.reports import ReportRecord, list_reports, list_reports_for_run
from atlas_os.core.workflow_runs import WorkflowRun, get_workflow_run, list_workflow_runs
from atlas_os.core.workflow_steps import list_workflow_steps
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.lifecycle import cleanup_greenrock_drafts
from atlas_os.greenrock.market_data import MarketDataConfigurationError
from atlas_os.greenrock.pdf_export import render_markdown_report_to_pdf
from atlas_os.greenrock.population import (
    ALL_POPULATION,
    GREENROCK_POPULATION_NAMES,
    add_population_tickers,
    load_populations,
    remove_population_tickers,
    reset_all_populations,
    validate_populations,
)
from atlas_os.greenrock.report import build_sample_report
from atlas_os.greenrock.score import calculate_score_preview, score_signal
from atlas_os.greenrock.scanner import promote_scan_ticker, run_population_scan
from atlas_os.greenrock.screener import run_screen
from atlas_os.greenrock.staging import (
    STAGING_BUCKET_LABELS,
    add_staged_candidate,
    enrich_staged_candidates,
    load_staged_candidates,
    move_staged_candidate,
    remove_staged_candidate,
    staging_analytics_status,
    staging_readiness,
)
from atlas_os.greenrock.staging_report import (
    run_greenrock_staging_report_workflow,
    staging_report_analytics_readiness,
    staging_report_readiness,
)
from atlas_os.greenrock.universe import (
    LARGE_CAP_UNIVERSE,
    MEGA_ROCK_UNIVERSE,
    SMALL_MID_CAP_UNIVERSE,
    add_tickers,
    load_greenrock_universes,
    remove_tickers,
    reset_all_universes,
    reset_universe,
    validate_watchlists,
)
from atlas_os.greenrock.universe_manager import default_universe_manager, provider_label
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
    serve = subparsers.add_parser("serve", help="Start the local Atlas Command Center web app.")
    serve.add_argument("--host", default="127.0.0.1", help="Host for the local web app.")
    serve.add_argument("--port", default=8000, type=int, help="Port for the local web app.")

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
    run_screen_parser = greenrock_subparsers.add_parser(
        "run-screen",
        help="Run the local GreenRock mock screening engine and write CSV outputs.",
    )
    run_screen_parser.add_argument(
        "--data",
        choices=("mock", "real"),
        default="mock",
        help="Market data mode. Defaults to mock.",
    )
    run_screen_parser.add_argument(
        "--selection",
        choices=("strict", "ranked"),
        default=None,
        help="Selection mode. Defaults to strict for mock and ranked for real.",
    )
    greenrock_subparsers.add_parser(
        "candidates",
        help="Print the current local GreenRock selected candidates.",
    )
    report_draft = greenrock_subparsers.add_parser(
        "report-draft",
        help="Generate a local GreenRock draft report from mock screening data.",
    )
    report_draft.add_argument(
        "--data",
        choices=("mock", "real"),
        default="mock",
        help="Market data mode. Defaults to mock.",
    )
    report_draft.add_argument(
        "--selection",
        choices=("strict", "ranked"),
        default=None,
        help="Selection mode. Defaults to strict for mock and ranked for real.",
    )
    report_from_staging = greenrock_subparsers.add_parser(
        "report-from-staging",
        help="Generate an approval-gated GreenRock report draft from staged candidates.",
    )
    report_from_staging.add_argument(
        "--allow-underfilled",
        action="store_true",
        help="Allow a staging-sourced draft when report buckets are underfilled.",
    )
    report_from_staging.add_argument(
        "--allow-missing-analytics",
        action="store_true",
        help="Allow a staging-sourced draft when staged candidate analytics are still missing.",
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
    greenrock_subparsers.add_parser(
        "picks-board",
        help="Show latest GreenRock Picks Board summary and local URL guidance.",
    )
    score = greenrock_subparsers.add_parser(
        "score",
        help="Preview the GreenRock Score for one ticker without creating a report.",
    )
    score.add_argument("ticker")
    score.add_argument(
        "--data",
        choices=("mock", "real"),
        default="real",
        help=argparse.SUPPRESS,
    )
    score.add_argument(
        "--selection",
        choices=("strict", "ranked"),
        default=None,
        help="Selection mode. Defaults to strict for mock and ranked for real.",
    )
    greenrock_subparsers.add_parser(
        "review",
        help="Show latest GreenRock report review summary.",
    )
    greenrock_subparsers.add_parser(
        "open-latest",
        help="Open the latest GreenRock report file on macOS.",
    )
    export_pdf = greenrock_subparsers.add_parser(
        "export-pdf",
        help="Export an approved GreenRock report to PDF.",
    )
    export_pdf.add_argument("approval_id", type=int)
    export_pdf.add_argument(
        "--open",
        action="store_true",
        dest="open_after_export",
        help="Open the PDF after successful export on macOS.",
    )
    final_packet = greenrock_subparsers.add_parser(
        "final-packet",
        help="Show final approved GreenRock report packet.",
    )
    final_packet.add_argument("approval_id", type=int)
    final_packet.add_argument(
        "--print",
        action="store_true",
        dest="print_contents",
        help="Print the Markdown report contents after the packet summary.",
    )
    open_pdf = greenrock_subparsers.add_parser(
        "open-pdf",
        help="Open an approved exported GreenRock PDF on macOS.",
    )
    open_pdf.add_argument("approval_id", type=int)
    cleanup_drafts = greenrock_subparsers.add_parser(
        "cleanup-drafts",
        help="Archive older GreenRock draft artifacts while preserving latest draft and final PDFs.",
    )
    cleanup_drafts.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting files or archiving artifacts.",
    )
    universe = greenrock_subparsers.add_parser(
        "universe",
        help="Manage local GreenRock ticker watchlists.",
    )
    universe_subparsers = universe.add_subparsers(dest="universe_command")
    universe_subparsers.add_parser("list", help="List current GreenRock ticker watchlists.")
    universe_add = universe_subparsers.add_parser("add", help="Add tickers to the local Mega Rock candidate pool.")
    universe_add.add_argument("tickers", nargs="+")
    universe_remove = universe_subparsers.add_parser("remove", help="Remove tickers from the local Mega Rock candidate pool.")
    universe_remove.add_argument("tickers", nargs="+")
    universe_subparsers.add_parser("reset-mega-rock", help="Reset local universe to the Mega Rock default.")
    universe_subparsers.add_parser("reset-large-cap", help="Reset local universe to the large-cap default.")
    universe_subparsers.add_parser("reset-small-mid", help="Reset local universe to the small/mid-cap default.")
    universe_subparsers.add_parser("reset-all", help="Reset all local GreenRock universes.")
    universe_subparsers.add_parser("validate", help="Validate local GreenRock watchlists.")

    population = greenrock_subparsers.add_parser(
        "population",
        help="Manage local GreenRock population scan sources.",
    )
    population_subparsers = population.add_subparsers(dest="population_command")
    population_subparsers.add_parser("list", help="List current GreenRock populations.")
    population_subparsers.add_parser("master", help="Show the Atlas master research universe.")
    population_subparsers.add_parser("reset-all", help="Reset all GreenRock populations.")
    population_add = population_subparsers.add_parser("add", help="Add tickers to a population.")
    population_add.add_argument("population", choices=GREENROCK_POPULATION_NAMES)
    population_add.add_argument("tickers", nargs="+")
    population_remove = population_subparsers.add_parser("remove", help="Remove tickers from a population.")
    population_remove.add_argument("population", choices=GREENROCK_POPULATION_NAMES)
    population_remove.add_argument("tickers", nargs="+")
    population_subparsers.add_parser("validate", help="Validate local GreenRock populations.")

    scan = greenrock_subparsers.add_parser(
        "scan",
        help="Run a real-data GreenRock population scan.",
    )
    scan.add_argument(
        "--population",
        choices=GREENROCK_POPULATION_NAMES + (ALL_POPULATION,),
        required=True,
        help="Population to scan.",
    )
    scan_promote = greenrock_subparsers.add_parser(
        "scan-promote",
        help="Promote one ticker from a local population scan into a GreenRock list.",
    )
    scan_promote.add_argument("scan_id")
    scan_promote.add_argument("ticker")
    scan_promote.add_argument(
        "--list",
        choices=("watchlist", "personal", "ranked", "strict", "mega_rock", "large_cap", "small_mid"),
        required=True,
        dest="list_key",
        help="Destination GreenRock list.",
    )

    staging = greenrock_subparsers.add_parser(
        "staging",
        help="Manage local GreenRock report candidate staging.",
    )
    staging_subparsers = staging.add_subparsers(dest="staging_command")
    staging_subparsers.add_parser("list", help="List staged GreenRock report candidates.")
    staging_add = staging_subparsers.add_parser("add", help="Add or update a staged GreenRock report candidate.")
    staging_add.add_argument("ticker")
    staging_add.add_argument("--bucket", choices=("mega", "large", "small_mid", "research", "excluded"), required=True)
    staging_add.add_argument("--notes", default="", help="Operator notes.")
    staging_move = staging_subparsers.add_parser("move", help="Move a staged ticker to a different bucket.")
    staging_move.add_argument("ticker")
    staging_move.add_argument("--bucket", choices=("mega", "large", "small_mid", "research", "excluded"), required=True)
    staging_remove = staging_subparsers.add_parser("remove", help="Remove a ticker from staging.")
    staging_remove.add_argument("ticker")
    staging_subparsers.add_parser("ready", help="Show staging readiness versus report slot targets.")
    staging_enrich = staging_subparsers.add_parser("enrich", help="Refresh staged candidates with current GreenRock analytics.")
    staging_enrich.add_argument("--ticker", default=None, help="Only enrich one staged ticker.")
    staging_enrich.add_argument("--bucket", choices=("mega", "large", "small_mid", "research", "excluded"), default=None)

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


def run_greenrock_screen(data_mode: str = "mock", selection_mode: str | None = None) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    try:
        with connect(db_path) as connection:
            workflow_run, artifacts, approval = run_greenrock_screening_workflow(
                connection,
                settings.output_dir,
                include_report_draft=True,
                data_mode=data_mode,
                selection_mode=selection_mode,
            )
    except MarketDataConfigurationError as error:
        print("GreenRock screen blocked")
        print(f"data_mode: {data_mode.upper()}")
        print(f"reason: {error}")
        print("No report, approval, artifact, email, publication, or external action was created.")
        return 1
    print("GreenRock local screen complete")
    print(f"run_id: {workflow_run.run_id}")
    print(f"status: {workflow_run.status}")
    print(f"data_mode: {workflow_run.data_mode.upper()}")
    print(f"selection_mode: {selection_mode or _default_selection_mode(data_mode)}")
    print(f"artifacts: {len(artifacts)}")
    print(f"approval_id: {approval.id if approval else 'none'}")
    print("Draft remains blocked until approved by a human.")
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


def run_greenrock_report_draft(data_mode: str = "mock", selection_mode: str | None = None) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    try:
        with connect(db_path) as connection:
            workflow_run, artifacts, approval = run_greenrock_screening_workflow(
                connection,
                settings.output_dir,
                include_report_draft=True,
                data_mode=data_mode,
                selection_mode=selection_mode,
            )
    except MarketDataConfigurationError as error:
        print("GreenRock report draft blocked")
        print(f"data_mode: {data_mode.upper()}")
        print(f"reason: {error}")
        print("No report, approval, artifact, email, publication, or external action was created.")
        return 1
    print("GreenRock report draft created")
    print(f"run_id: {workflow_run.run_id}")
    print(f"status: {workflow_run.status}")
    print(f"data_mode: {workflow_run.data_mode.upper()}")
    print(f"selection_mode: {selection_mode or _default_selection_mode(data_mode)}")
    print(f"artifacts: {len(artifacts)}")
    print(f"approval_id: {approval.id if approval else 'none'}")
    print("Draft is blocked until approved by a human.")
    return 0


def run_greenrock_report_from_staging(allow_underfilled: bool = False, allow_missing_analytics: bool = False) -> int:
    settings = get_settings()
    readiness = staging_report_readiness(settings.output_dir, allow_underfilled=allow_underfilled)
    if not readiness.can_generate:
        print("GreenRock staging report blocked")
        print("reason: staged report buckets are underfilled")
        print("warnings:")
        for warning in readiness.warnings:
            print(f"  {warning}")
        print("Run with --allow-underfilled to create an approval-gated draft anyway.")
        print("No report, approval, PDF, email, publication, or external action was created.")
        return 1
    analytics_readiness = staging_report_analytics_readiness(settings.output_dir)
    if not analytics_readiness.can_generate and not allow_missing_analytics:
        print("GreenRock staging report blocked")
        print("reason: staging candidates need enrichment")
        print("warnings:")
        for warning in analytics_readiness.warnings:
            print(f"  {warning}")
        print("Run atlas greenrock staging enrich or configure market data provider.")
        print("Use --allow-missing-analytics only for an intentional draft with explicit data warnings.")
        print("No report, approval, PDF, email, publication, or external action was created.")
        return 1
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        workflow_run, artifacts, approval = run_greenrock_staging_report_workflow(
            connection,
            settings.output_dir,
            allow_underfilled=allow_underfilled,
            allow_missing_analytics=allow_missing_analytics,
        )
    print("GreenRock staging report draft created")
    print(f"run_id: {workflow_run.run_id}")
    print(f"status: {workflow_run.status}")
    print(f"data_mode: {workflow_run.data_mode.upper()}")
    print("selection_mode: STAGING")
    print(f"artifacts: {len(artifacts)}")
    print(f"approval_id: {approval.id if approval else 'none'}")
    print("warnings:")
    if readiness.warnings:
        for warning in readiness.warnings:
            print(f"  {warning}")
    else:
        print("  none")
    print("Draft is blocked until approved by a human. No email, publication, or PDF export was created.")
    return 0


def run_greenrock_latest_report(print_contents: bool = False) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_report = _latest_greenrock_report(connection)
        latest_run = _latest_greenrock_run(connection)
    if latest_report is None:
        print("No GreenRock reports found.")
        return 0
    print("Latest GreenRock report")
    print(f"run_id: {latest_report.run_id}")
    print(f"status: {latest_report.status}")
    print(f"data_mode: {latest_run.data_mode.upper() if latest_run else 'UNKNOWN'}")
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
    print(f"data_mode: {latest_run.data_mode.upper()}")
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


def run_greenrock_review() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_run = _latest_greenrock_run(connection)
        latest_report = _latest_greenrock_report(connection)
        if latest_run is None or latest_report is None:
            print("No GreenRock report found. Run atlas greenrock report-draft first.")
            return 0
        approvals = _approvals_for_run(connection, latest_run.run_id)
    pending_approval = next((approval for approval in approvals if approval.status == ApprovalStatus.PENDING), None)
    approval_status = approvals[0].status.value if approvals else "none"

    print("GreenRock Review")
    print(f"latest_run: {latest_run.run_id}")
    print(f"run_status: {latest_run.status}")
    print(f"latest_report_path: {latest_report.content_path}")
    print(f"approval_status: {approval_status}")
    print(f"pending_approval_id: {pending_approval.id if pending_approval else 'none'}")
    _print_candidate_file("Top large-cap names", latest_run.output_paths.get("large_cap"), limit=5)
    _print_candidate_file("Top small/mid-cap names", latest_run.output_paths.get("small_cap"), limit=5)
    return 0


def run_greenrock_picks_board() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_run = _latest_greenrock_run(connection)
        latest_report = _latest_greenrock_report(connection)
        approvals = _approvals_for_run(connection, latest_run.run_id) if latest_run else ()

    if latest_run is None:
        print("No GreenRock runs found. Run atlas greenrock report-draft first.")
        print("Picks Board URL: http://127.0.0.1:8000/greenrock/picks")
        return 0

    mega_rows = _read_candidate_rows(latest_run.output_paths.get("mega_rock"), limit=1)
    large_rows = _read_candidate_rows(latest_run.output_paths.get("large_cap"), limit=11)
    small_rows = _read_candidate_rows(latest_run.output_paths.get("small_cap"), limit=11)
    all_rows = _read_candidate_rows(latest_run.output_paths.get("all"), limit=None)
    mega = mega_rows[0] if mega_rows else (all_rows[0] if all_rows else None)
    approval_status = approvals[0].status.value if approvals else "none"
    slot_count = (1 if mega else 0) + len(large_rows) + len(small_rows)

    print("GreenRock Picks Board")
    print(f"url: http://127.0.0.1:8000/greenrock/picks")
    print(f"latest_run: {latest_run.run_id}")
    print(f"data_mode: {latest_run.data_mode.upper()}")
    print(f"approval_status: {approval_status}")
    print(f"report_path: {latest_report.content_path if latest_report else 'none'}")
    print(f"visible_slots: {slot_count}/23")
    print(f"mega_rock_count: {1 if mega else 0}/1")
    print(f"large_cap_count: {len(large_rows)}/11")
    print(f"small_mid_count: {len(small_rows)}/11")
    print(f"mega_rock_pick: {_candidate_summary(mega) if mega else 'none'}")
    _print_candidate_rows("Large-cap picks", large_rows)
    _print_candidate_rows("Small/mid-cap picks", small_rows)
    print("Start the local Command Center with: atlas serve")
    return 0


def run_greenrock_score(ticker: str, data_mode: str = "real", selection_mode: str | None = None) -> int:
    settings = get_settings()
    try:
        preview = calculate_score_preview(
            ticker,
            data_mode=data_mode,
            selection_mode=selection_mode,
            output_dir=settings.output_dir,
        )
    except (MarketDataConfigurationError, ValueError) as error:
        print("GreenRock score preview blocked")
        print(f"ticker: {ticker.upper()}")
        print(f"data_mode: {data_mode.upper()}")
        print(f"reason: {error}")
        print("setup: export ATLAS_MARKET_DATA_PROVIDER=yfinance")
        print('setup: python3 -m pip install -e ".[market-data]"')
        print("No report, approval, artifact, email, publication, or external action was created.")
        return 1

    candidate = preview.candidate
    indicators = candidate.indicators
    print("GreenRock Score Preview")
    print(f"ticker: {candidate.symbol}")
    print(f"company: {candidate.company_name}")
    print(f"data_mode: {preview.data_mode.upper()}")
    print(f"data_source: {preview.data_source}")
    print(f"selection_mode: {preview.selection_mode}")
    print(f"market_cap: {candidate.market_cap:.2f}")
    print(f"price: {indicators.latest_close:.2f}")
    print(f"greenrock_score: {candidate.score:.2f}")
    print(f"greenrock_confidence: {preview.confidence_score:.2f}")
    print(f"confidence_band: {preview.confidence_band}")
    print(f"evidence_agreement: {preview.evidence_agreement_score:.2f}")
    print(f"score_confidence_divergence: {preview.score_confidence_divergence}")
    print(f"signal_label: {score_signal(candidate)}")
    print(f"research_priority: {preview.research_priority}")
    print(f"analyst_summary: {preview.analyst_summary}")
    print(f"rank_band: {_score_rank_band(candidate.score)}")
    print(f"selection_label: {candidate.selection_label}")
    print(f"rsi: {indicators.rsi_14:.2f}")
    print(f"bollinger_position: {_score_bollinger_position(candidate)}")
    print(f"52_week_low_distance: {indicators.low_proximity:.2%}")
    print(f"volume_acceleration: {_score_volume_acceleration(candidate)}")
    print(f"moving_average_structure: {_score_moving_average_structure(candidate)}")
    guardrail = preview.fundamental_guardrails
    print("fundamental_guardrails:")
    print(f"  label: {guardrail.label}")
    print(f"  net_cash_debt: {_format_cli_net_cash_debt(guardrail.net_cash)}")
    print(f"  net_cash_per_share: {_format_cli_price(guardrail.net_cash_per_share)}")
    print(f"  quick_ratio: {_format_cli_ratio(guardrail.quick_ratio)}")
    print(f"  share_count_change_percent: {_format_cli_percent(guardrail.shares_outstanding_change_percent)}")
    print(f"  confidence_impact: {guardrail.confidence_impact:+.2f}")
    print(f"  score_adjustment: {preview.fundamental_guardrail_adjustment:+.2f}")
    print("  bullish_fundamental_evidence:")
    for item in guardrail.bullish_evidence:
        print(f"    {item}")
    print("  bearish_fundamental_evidence:")
    for item in guardrail.bearish_evidence:
        print(f"    {item}")
    print("  warnings:")
    if guardrail.warnings:
        for warning in guardrail.warnings:
            print(f"    {warning}")
    else:
        print("    none")
    print(f"all_time_high: {_format_cli_price(preview.all_time_high)}")
    print("one_year_statistical_price_targets:")
    print(f"  historical_lookback: {preview.price_target_lookback}")
    print(f"  horizon: {preview.price_target_horizon}")
    print(f"  data_source: {preview.data_source}")
    print("  disclosure: statistical targets, not forecasts or guarantees")
    for target in preview.price_targets:
        relation = target.relation_to_ath.replace("-", "_")
        print(f"  {target.label}: {_format_cli_price(target.price)} ({relation})")
    if preview.price_target_warnings:
        print("price_target_warnings:")
        for warning in preview.price_target_warnings:
            print(f"  {warning}")
    print("evidence_engine:")
    for item in preview.evidence_items:
        print(
            f"  {item.name}: {item.category} {item.direction} {item.strength} "
            f"{item.numeric_contribution:+.2f} - {item.explanation}"
        )
    print("top_bullish_evidence:")
    for item in preview.bullish_evidence[:3]:
        print(f"  {item}")
    print("top_bearish_evidence:")
    for item in preview.bearish_evidence[:3]:
        print(f"  {item}")
    print("bullish_evidence:")
    for item in preview.bullish_evidence:
        print(f"  {item}")
    print("bearish_evidence:")
    for item in preview.bearish_evidence:
        print(f"  {item}")
    print("positive_confidence_drivers:")
    for item in preview.confidence_drivers:
        print(f"  {item}")
    print("confidence_drags:")
    for item in preview.confidence_drags:
        print(f"  {item}")
    print("what_to_watch_next:")
    for item in preview.watch_next:
        print(f"  {item}")
    print(f"finviz: https://finviz.com/quote.ashx?t={candidate.symbol}")
    print("component_scores:")
    for name, value in preview.component_scores.items():
        print(f"  {_score_component_label(name)}: {value:.2f}")
    print("component_explanations:")
    for component in preview.component_explanations:
        print(f"  {component.name}:")
        print(f"    raw_metric: {component.raw_metric}")
        print(f"    component_score: {component.component_score:.2f}")
        print(f"    weight: {component.weight}")
        print(f"    explanation: {component.explanation}")
    print("data_quality_warnings:")
    if preview.data_quality_warnings:
        for warning in preview.data_quality_warnings:
            print(f"  {warning}")
    else:
        print("  none")
    return 0


def run_greenrock_population(command: str | None, population: str | None = None, tickers: list[str] | None = None) -> int:
    settings = get_settings()
    if command == "list":
        populations = load_populations(settings.output_dir)
        print("GreenRock populations")
        for item in populations.values():
            print(f"name: {item.name}")
            print(f"label: {item.label}")
            print(f"ticker_count: {len(item.tickers)}")
            print(f"path: {item.path}")
            print(f"tickers: {', '.join(item.tickers)}")
        master = default_universe_manager(settings.output_dir).master_universe()
        print("Atlas master universe")
        print(f"ticker_count: {master.size}")
        print(f"duplicates_removed: {master.duplicates_removed}")
        print(f"path: {master.path}")
        return 0
    if command == "master":
        master = default_universe_manager(settings.output_dir).master_universe()
        print("Atlas Research Master Universe")
        print(f"ticker_count: {master.size}")
        print(f"duplicates_removed: {master.duplicates_removed}")
        print(f"last_refresh: {master.last_refresh}")
        print(f"path: {master.path}")
        print("providers:")
        for provider in master.providers:
            print(
                f"  {provider.name} ({provider_label(provider.name)}): "
                f"{provider.ticker_count} tickers status={provider.status} health={provider.health}"
            )
        print("tickers:")
        for row in master.rows:
            print(
                f"  {row.ticker} bucket={row.market_cap_bucket} "
                f"membership={','.join(row.provider_membership)} health={row.health}"
            )
        return 0
    if command == "reset-all":
        populations = reset_all_populations(settings.output_dir)
        print("GreenRock populations reset")
        for item in populations.values():
            print(f"{item.name}: {len(item.tickers)} tickers -> {item.path}")
        return 0
    if command == "add" and population:
        item = add_population_tickers(settings.output_dir, population, tuple(tickers or ()))
        print("GreenRock population updated")
        print(f"name: {item.name}")
        print(f"ticker_count: {len(item.tickers)}")
        print(f"path: {item.path}")
        return 0
    if command == "remove" and population:
        item = remove_population_tickers(settings.output_dir, population, tuple(tickers or ()))
        print("GreenRock population updated")
        print(f"name: {item.name}")
        print(f"ticker_count: {len(item.tickers)}")
        print(f"path: {item.path}")
        return 0
    if command == "validate":
        validation = validate_populations(settings.output_dir)
        print("GreenRock population validation")
        print(f"duplicate_count: {len(validation.duplicate_tickers)}")
        if validation.duplicate_tickers:
            print(f"duplicates: {', '.join(validation.duplicate_tickers)}")
        print("warnings:")
        if validation.warnings:
            for warning in validation.warnings:
                print(f"  {warning}")
        else:
            print("  none")
        return 0
    print("Choose a population command: list, master, reset-all, add, remove, or validate.")
    return 1


def run_greenrock_scan(population: str) -> int:
    settings = get_settings()
    try:
        result = run_population_scan(settings.output_dir, population)
    except (MarketDataConfigurationError, ValueError) as error:
        print("GreenRock population scan blocked")
        print(f"population: {population}")
        print(f"reason: {error}")
        print("setup: export ATLAS_MARKET_DATA_PROVIDER=yfinance")
        print('setup: python3 -m pip install -e ".[market-data]"')
        print("No report, approval, email, publication, or external action was created.")
        return 1
    print("GreenRock population scan complete")
    print(f"scan_id: {result.scan_id}")
    print(f"population: {result.population}")
    print(f"data_source: {result.data_source}")
    print(f"results_csv: {result.results_path}")
    print(f"summary_md: {result.summary_path}")
    print(f"total_configured_tickers: {result.configured_ticker_count}")
    print(f"successfully_fetched_scored: {result.fetched_ticker_count}")
    print(f"skipped_tickers: {result.skipped_ticker_count}")
    print(f"provider_failures: {result.provider_failure_count}")
    print(f"duplicates_removed: {result.duplicates_removed}")
    print(f"ranked_count: {len(result.rows)}")
    if result.warnings:
        print("warnings:")
        for warning in result.warnings:
            print(f"  {warning}")
    print("top_ranked:")
    for row in result.rows[:10]:
        print(
            f"  {row['rank']} {row['symbol']} score={row['greenrock_score']} "
            f"confidence={row['greenrock_confidence']} evidence={row['evidence_agreement']} "
            f"percentile={row.get('percentile', '')} membership={row.get('universe_membership', '')} "
            f"guardrail={row['fundamental_guardrail']}"
        )
    return 0


def run_greenrock_scan_promote(scan_id: str, ticker: str, list_key: str) -> int:
    settings = get_settings()
    try:
        placement = promote_scan_ticker(settings.output_dir, scan_id, ticker, list_key)
    except ValueError as error:
        print("GreenRock scan promotion blocked")
        print(f"scan_id: {scan_id}")
        print(f"ticker: {ticker.upper()}")
        print(f"reason: {error}")
        print("No report, approval, PDF, email, publication, or external action was created.")
        return 1
    verb = "blocked" if placement.blocked else ("saved to" if placement.added else "already exists in")
    print("GreenRock scan promotion complete")
    print(f"scan_id: {scan_id}")
    print(f"ticker: {placement.ticker}")
    print(f"list: {placement.list_label}")
    print(f"status: {verb}")
    print(f"path: {placement.path}")
    print("warnings:")
    if placement.warnings:
        for warning in placement.warnings:
            print(f"  {warning}")
    else:
        print("  none")
    print("No report, approval, PDF, email, publication, or external action was created.")
    return 0


def run_greenrock_staging(command: str | None, ticker: str | None = None, bucket: str | None = None, notes: str = "") -> int:
    settings = get_settings()
    try:
        if command == "list":
            rows = load_staged_candidates(settings.output_dir)
            print("GreenRock report candidate staging")
            if not rows:
                print("No staged candidates found.")
                return 0
            print("ticker bucket score confidence evidence guardrail priority source_scan_id notes")
            for row in rows:
                print(
                    f"{row['ticker']} {row['staged_bucket']} {row['greenrock_score'] or '-'} "
                    f"{row['confidence'] or '-'} {row['evidence_agreement'] or '-'} "
                    f"{row['guardrail'] or '-'} {row['research_priority'] or '-'} "
                    f"{row['source_scan_id'] or '-'} {row['notes'] or '-'}"
                )
            return 0
        if command == "add" and ticker and bucket:
            row = add_staged_candidate(settings.output_dir, ticker, bucket, notes=notes)
            print("GreenRock staging candidate saved")
            _print_staging_row(row)
            print("No report, approval, PDF, email, publication, or external action was created.")
            return 0
        if command == "move" and ticker and bucket:
            row = move_staged_candidate(settings.output_dir, ticker, bucket)
            print("GreenRock staging candidate moved")
            _print_staging_row(row)
            print("No report, approval, PDF, email, publication, or external action was created.")
            return 0
        if command == "remove" and ticker:
            removed = remove_staged_candidate(settings.output_dir, ticker)
            print("GreenRock staging candidate removed" if removed else "GreenRock staging candidate not found")
            print(f"ticker: {ticker.upper()}")
            print("No report, approval, PDF, email, publication, or external action was created.")
            return 0
        if command == "ready":
            print("GreenRock staging readiness")
            for item in staging_readiness(settings.output_dir):
                target = item.target if item.target is not None else "review"
                print(f"{item.label}: {item.count}/{target} {item.status}")
            analytics = staging_analytics_status(settings.output_dir)
            print("analytics_completeness:")
            print(f"  status: {analytics.label}")
            print(f"  missing_count: {analytics.missing_count}")
            print(f"  missing_tickers: {', '.join(analytics.missing_tickers) if analytics.missing_tickers else 'none'}")
            return 0
        if command == "enrich":
            result = enrich_staged_candidates(settings.output_dir, ticker=ticker, bucket=bucket)
            print("GreenRock staging enrichment")
            print(f"enriched_count: {len(result.enriched)}")
            print(f"enriched_tickers: {', '.join(result.enriched) if result.enriched else 'none'}")
            print(f"skipped_tickers: {', '.join(result.skipped) if result.skipped else 'none'}")
            print("reasons:")
            if result.errors:
                for error in result.errors:
                    print(f"  {error}")
            else:
                print("  none")
            print("No report, approval, PDF, email, publication, or external action was created.")
            if result.errors and not result.enriched:
                print("setup: export ATLAS_MARKET_DATA_PROVIDER=yfinance")
                print('setup: python3 -m pip install -e ".[market-data]"')
                return 1
            return 0
    except ValueError as error:
        print("GreenRock staging blocked")
        print(f"reason: {error}")
        print("No report, approval, PDF, email, publication, or external action was created.")
        return 1
    print("Choose a staging command: list, add, move, remove, ready, or enrich.")
    return 1


def _print_staging_row(row: dict[str, str]) -> None:
    print(f"ticker: {row['ticker']}")
    print(f"bucket: {STAGING_BUCKET_LABELS[row['staged_bucket']]}")
    print(f"source_list: {row['source_list'] or 'manual'}")
    print(f"source_scan_id: {row['source_scan_id'] or '-'}")
    print(f"greenrock_score: {row['greenrock_score'] or '-'}")
    print(f"confidence: {row['confidence'] or '-'}")
    print(f"evidence_agreement: {row['evidence_agreement'] or '-'}")
    print(f"guardrail: {row['guardrail'] or '-'}")
    print(f"research_priority: {row['research_priority'] or '-'}")
    print(f"notes: {row['notes'] or '-'}")


def _score_rank_band(score: float) -> str:
    if score >= 85:
        return "Exceptional"
    if score >= 70:
        return "Strong"
    if score >= 55:
        return "Watchlist"
    return "Low Priority"


def _format_cli_price(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.2f}"


def _format_cli_ratio(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.2f}"


def _format_cli_percent(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.2%}"


def _format_cli_net_cash_debt(value: float | None) -> str:
    if value is None:
        return "unavailable"
    label = "net_cash" if value >= 0 else "net_debt"
    return f"{label}:{abs(value):.2f}"


def _score_component_label(name: str) -> str:
    labels = {
        "52_week_low_proximity": "52-week low proximity",
        "bollinger_band_setup": "Bollinger Band setup",
        "rsi": "RSI",
        "volume_acceleration": "Volume acceleration",
        "moving_average_structure": "Moving average structure",
        "bonus_penalty_factors": "Bullish / Bearish Evidence",
    }
    return labels.get(name, name.replace("_", " "))


def run_greenrock_open_latest() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        latest_report = _latest_greenrock_report(connection)
    if latest_report is None or not latest_report.content_path:
        print("No GreenRock report found. Run atlas greenrock report-draft first.")
        return 0

    report_path = Path(latest_report.content_path)
    if sys.platform == "darwin":
        subprocess.run(["open", str(report_path)], check=False)
        print(f"Opened latest GreenRock report: {report_path}")
    else:
        print(f"Latest GreenRock report: {report_path}")
    return 0


def run_greenrock_export_pdf(approval_id: int, open_after_export: bool = False) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = get_approval(connection, approval_id)
        if approval.status != ApprovalStatus.APPROVED:
            print(f"PDF export blocked: approval {approval_id} is {approval.status.value}.")
            print("Approve the report before exporting a final PDF.")
            return 1
        report = _report_for_approval(connection, approval_id)
        if report is None or not report.content_path or not report.run_id:
            print(f"PDF export blocked: no report found for approval {approval_id}.")
            return 1

        markdown_path = Path(report.content_path)
        pdf_path = markdown_path.with_name("greenrock_report_final.pdf")
        render_markdown_report_to_pdf(markdown_path, pdf_path)
        existing_artifact = _pdf_artifact_for_run(connection, report.run_id)
        if existing_artifact:
            artifact = existing_artifact
            audit_action = "artifact_updated"
        else:
            artifact = create_artifact(connection, report.run_id, "report_final_pdf", pdf_path)
            audit_action = "artifact_created"
        create_audit_log(
            connection,
            actor="greenrock_pdf_export",
            action=audit_action,
            detail=f"report_final_pdf: {pdf_path}",
            run_id=report.run_id,
            artifact_id=artifact.id,
            approval_id=approval_id,
        )

    print("GreenRock final PDF exported")
    print(f"approval_id: {approval_id}")
    print(f"run_id: {report.run_id}")
    print(f"pdf_path: {pdf_path}")
    print(f"artifact_id: {artifact.id}")
    if open_after_export:
        _open_path_or_print(pdf_path, opened_label="Opened GreenRock PDF", fallback_label="GreenRock PDF")
    return 0


def run_greenrock_final_packet(approval_id: int, print_contents: bool = False) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = get_approval(connection, approval_id)
        report = _report_for_approval(connection, approval_id)
        artifacts = list_artifacts_for_run(connection, approval.run_id) if approval.run_id else ()
        pdf_artifact = _pdf_artifact_for_run(connection, approval.run_id) if approval.run_id else None
        workflow_run = get_workflow_run(connection, approval.run_id) if approval.run_id else None

    print("GreenRock Final Report Packet")
    print(f"approval_id: {approval.id}")
    print(f"approval_status: {approval.status.value}")
    print(f"approval_timestamp: {approval.decided_at}")
    print(f"run_id: {approval.run_id}")
    print(f"data_mode: {workflow_run.data_mode.upper() if workflow_run else 'UNKNOWN'}")
    print(f"markdown_report_path: {report.content_path if report else None}")
    print(f"pdf_path: {pdf_artifact.path if pdf_artifact else 'not exported'}")
    print("artifacts:")
    for artifact in artifacts:
        print(f"  {artifact.id} {artifact.artifact_type} {artifact.path}")
    if workflow_run and workflow_run.data_mode == "real":
        print("mock_data_disclaimer: not applicable - this packet is labeled REAL data mode.")
        print("data_mode_disclaimer: Real-data packets remain approval-gated and local-only.")
    else:
        print("mock_data_disclaimer: This packet uses mock data only.")
    if approval.status != ApprovalStatus.APPROVED:
        print("human_approval_confirmation: not approved - this is not a final packet.")
        print("next_step: approve the report before treating it as final.")
        return 1
    print("human_approval_confirmation: approved by human review workflow.")
    if not pdf_artifact:
        print("next_step: run atlas greenrock export-pdf <approval_id> to create the final PDF.")
    if print_contents and report and report.content_path:
        print("")
        print("Markdown report contents")
        print(Path(report.content_path).read_text(encoding="utf-8"))
    return 0


def run_greenrock_open_pdf(approval_id: int) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = get_approval(connection, approval_id)
        pdf_artifact = _pdf_artifact_for_run(connection, approval.run_id) if approval.run_id else None

    if approval.status != ApprovalStatus.APPROVED:
        print(f"Cannot open PDF: approval {approval_id} is {approval.status.value}.")
        print("Next step: approve the report, then export the PDF.")
        return 1
    if not pdf_artifact or not Path(pdf_artifact.path).exists():
        print(f"No exported PDF found for approval {approval_id}.")
        print(f"Next step: atlas greenrock export-pdf {approval_id}")
        return 1

    pdf_path = Path(pdf_artifact.path)
    _open_path_or_print(pdf_path, opened_label="Opened GreenRock PDF", fallback_label="GreenRock PDF")
    return 0


def run_greenrock_cleanup_drafts(dry_run: bool = False) -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        result = cleanup_greenrock_drafts(connection, dry_run=dry_run)

    print("GreenRock draft cleanup")
    print(f"dry_run: {result.dry_run}")
    print(f"latest_draft_run_id: {result.latest_draft_run_id or 'none'}")
    print(f"removed_file_count: {len(result.removed_files)}")
    print(f"archived_artifact_count: {len(result.archived_artifact_ids) if not dry_run else 0}")
    print(f"preserved_final_pdf_count: {len(result.preserved_final_pdfs)}")
    print(f"preserved_latest_artifact_count: {len(result.preserved_latest_artifacts)}")
    print("removed_files:")
    for path in result.removed_files:
        print(f"  {path}")
    print("preserved_final_pdfs:")
    for path in result.preserved_final_pdfs:
        print(f"  {path}")
    print("preserved_latest_artifacts:")
    for path in result.preserved_latest_artifacts:
        print(f"  {path}")
    print("Audit logs and approval records were preserved.")
    return 0


def run_greenrock_universe(command: str | None, tickers: list[str] | None = None) -> int:
    settings = get_settings()
    if command in (None, "list"):
        universes = load_greenrock_universes(settings.output_dir)
        print("GreenRock ticker watchlists")
        for universe in universes.values():
            print(f"name: {universe.name}")
            print(f"path: {universe.path}")
            print(f"ticker_count: {len(universe.tickers)}")
            print("tickers:")
            for ticker in universe.tickers:
                print(f"  {ticker}")
        return 0
    if command == "add":
        universe = add_tickers(settings.output_dir, tuple(tickers or ()))
        print("GreenRock ticker watchlist updated")
        print(f"ticker_count: {len(universe.tickers)}")
        print(f"path: {universe.path}")
        return 0
    if command == "remove":
        universe = remove_tickers(settings.output_dir, tuple(tickers or ()))
        print("GreenRock ticker watchlist updated")
        print(f"ticker_count: {len(universe.tickers)}")
        print(f"path: {universe.path}")
        return 0
    if command == "reset-mega-rock":
        _print_reset_universe(reset_universe(settings.output_dir, MEGA_ROCK_UNIVERSE))
        return 0
    if command == "reset-large-cap":
        _print_reset_universe(reset_universe(settings.output_dir, LARGE_CAP_UNIVERSE))
        return 0
    if command == "reset-small-mid":
        _print_reset_universe(reset_universe(settings.output_dir, SMALL_MID_CAP_UNIVERSE))
        return 0
    if command == "reset-all":
        universes = reset_all_universes(settings.output_dir)
        print("GreenRock ticker watchlists reset")
        for universe in universes.values():
            print(f"{universe.name}: {len(universe.tickers)} tickers at {universe.path}")
        return 0
    if command == "validate":
        validation = validate_watchlists(settings.output_dir)
        print("GreenRock watchlist validation")
        print("duplicate_tickers:")
        if validation.duplicate_tickers:
            for ticker in validation.duplicate_tickers:
                print(f"  {ticker}")
        else:
            print("  none")
        print("probable_bucket_mismatches:")
        if validation.probable_bucket_mismatches:
            for mismatch in validation.probable_bucket_mismatches:
                print(f"  {mismatch}")
        else:
            print("  none")
        print("warnings:")
        if validation.warnings:
            for warning in validation.warnings:
                print(f"  {warning}")
        else:
            print("  none")
        return 0
    raise ValueError(f"Unsupported universe command: {command}")


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
        latest_report_approval = (
            get_approval(connection, latest_report.approval_id)
            if latest_report and latest_report.approval_id
            else None
        )
        latest_pdf_artifact = (
            _pdf_artifact_for_run(connection, latest_report.run_id)
            if latest_report and latest_report.run_id
            else None
        )

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
        latest_run = _latest_greenrock_run(connection)
        print(f"data_mode: {latest_run.data_mode.upper() if latest_run else 'UNKNOWN'}")
        print(f"path: {latest_report.content_path}")
        print(f"approval_status: {latest_report_approval.status.value if latest_report_approval else 'none'}")
        print(f"final_pdf_status: {'exported' if latest_pdf_artifact else 'not exported'}")
        print(f"final_pdf_path: {latest_pdf_artifact.path if latest_pdf_artifact else 'not exported'}")
    else:
        print("No GreenRock reports found.")
    return 0


def run_serve(host: str, port: int) -> int:
    from atlas_os.web_app import serve

    serve(host=host, port=port)
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


def _print_reset_universe(universe) -> None:
    print("GreenRock ticker watchlist reset")
    print(f"name: {universe.name}")
    print(f"ticker_count: {len(universe.tickers)}")
    print(f"path: {universe.path}")


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


def _report_for_approval(connection, approval_id: int) -> ReportRecord | None:
    for report in list_reports(connection):
        if report.approval_id == approval_id:
            return report
    return None


def _pdf_artifact_for_run(connection, run_id: str | None):
    if not run_id:
        return None
    for artifact in list_artifacts_for_run(connection, run_id):
        if artifact.artifact_type == "report_final_pdf":
            return artifact
    return None


def _open_path_or_print(path: Path, opened_label: str, fallback_label: str) -> None:
    if sys.platform == "darwin":
        result = subprocess.run(["open", str(path)], check=False)
        if result.returncode == 0:
            print(f"{opened_label}: {path}")
        else:
            print(f"Could not open automatically. {fallback_label}: {path}")
    else:
        print(f"{fallback_label}: {path}")


def _default_selection_mode(data_mode: str) -> str:
    return "ranked" if data_mode == "real" else "strict"


def _score_bollinger_position(candidate) -> str:
    indicators = candidate.indicators
    if indicators.latest_close < indicators.bollinger_lower:
        return "Below lower 2.5σ band"
    lower_distance = abs(indicators.latest_close - indicators.bollinger_lower)
    upper_distance = abs(indicators.bollinger_upper - indicators.latest_close)
    return "Closer to lower band" if lower_distance < upper_distance else "Closer to upper band"


def _score_volume_acceleration(candidate) -> str:
    indicators = candidate.indicators
    if indicators.previous_volume_avg_10 <= 0:
        return "unavailable"
    return f"{(indicators.volume_avg_10 - indicators.previous_volume_avg_10) / indicators.previous_volume_avg_10:.2%}"


def _score_moving_average_structure(candidate) -> str:
    return (
        f"8 EMA {'below' if 'ema8_below_sma10' in candidate.passed_rules else 'not below'} 10 SMA; "
        f"50 DMA {'below' if 'dma50_below_dma150' in candidate.passed_rules else 'not below'} 150 DMA; "
        f"50 DMA ROC {'improving' if 'dma50_roc_improving_vs_dma150' in candidate.passed_rules else 'not improving'} vs 150 DMA"
    )


def _approvals_for_run(connection, run_id: str) -> tuple[ApprovalRequest, ...]:
    return tuple(approval for approval in list_approvals(connection) if approval.run_id == run_id)


def _print_candidate_file(title: str, path: str | None, limit: int | None = None) -> None:
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
        rows = list(csv.DictReader(csv_file))
        for row in rows[:limit]:
            print(f"{row['symbol']} {row['score']} {row['market_cap']} {row['company_name']}")


def _read_candidate_rows(path: str | None, limit: int | None = None) -> list[dict[str, str]]:
    if not path:
        return []
    candidate_path = Path(path)
    if not candidate_path.exists():
        return []
    with candidate_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    rows.sort(key=_candidate_row_score, reverse=True)
    return rows[:limit] if limit is not None else rows


def _candidate_row_score(row: dict[str, str]) -> float:
    try:
        return float(row.get("score", "0"))
    except ValueError:
        return 0.0


def _candidate_summary(row: dict[str, str]) -> str:
    return f"{row.get('symbol', '')} score={row.get('score', '')} {row.get('company_name', '')}"


def _print_candidate_rows(title: str, rows: list[dict[str, str]]) -> None:
    print("")
    print(title)
    if not rows:
        print("No candidate rows found.")
        return
    print("symbol score evidence_agreement market_cap top_bullish_signal top_caution_signal company")
    for row in rows:
        print(
            f"{row.get('symbol', '')} {row.get('score', '')} "
            f"{row.get('evidence_agreement', '')} {row.get('market_cap', '')} "
            f"{row.get('top_bullish_signal', 'none')} | {row.get('top_caution_signal', 'none')} | "
            f"{row.get('company_name', '')}"
        )


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return run_status()

    if args.command == "dashboard":
        return run_dashboard()

    if args.command == "serve":
        return run_serve(args.host, args.port)

    if args.command == "greenrock":
        if args.greenrock_command == "sample-report":
            return run_greenrock_sample_report()
        if args.greenrock_command == "run-screen":
            return run_greenrock_screen(args.data, args.selection)
        if args.greenrock_command == "candidates":
            return run_greenrock_candidates()
        if args.greenrock_command == "report-draft":
            return run_greenrock_report_draft(args.data, args.selection)
        if args.greenrock_command == "report-from-staging":
            return run_greenrock_report_from_staging(args.allow_underfilled, args.allow_missing_analytics)
        if args.greenrock_command == "latest-report":
            return run_greenrock_latest_report(args.print_contents)
        if args.greenrock_command == "latest-run":
            return run_greenrock_latest_run()
        if args.greenrock_command == "latest-candidates":
            return run_greenrock_latest_candidates()
        if args.greenrock_command == "picks-board":
            return run_greenrock_picks_board()
        if args.greenrock_command == "score":
            return run_greenrock_score(args.ticker, args.data, args.selection)
        if args.greenrock_command == "population":
            return run_greenrock_population(
                args.population_command,
                getattr(args, "population", None),
                getattr(args, "tickers", None),
            )
        if args.greenrock_command == "scan":
            return run_greenrock_scan(args.population)
        if args.greenrock_command == "scan-promote":
            return run_greenrock_scan_promote(args.scan_id, args.ticker, args.list_key)
        if args.greenrock_command == "staging":
            return run_greenrock_staging(
                args.staging_command,
                getattr(args, "ticker", None),
                getattr(args, "bucket", None),
                getattr(args, "notes", ""),
            )
        if args.greenrock_command == "review":
            return run_greenrock_review()
        if args.greenrock_command == "open-latest":
            return run_greenrock_open_latest()
        if args.greenrock_command == "export-pdf":
            return run_greenrock_export_pdf(args.approval_id, args.open_after_export)
        if args.greenrock_command == "final-packet":
            return run_greenrock_final_packet(args.approval_id, args.print_contents)
        if args.greenrock_command == "open-pdf":
            return run_greenrock_open_pdf(args.approval_id)
        if args.greenrock_command == "cleanup-drafts":
            return run_greenrock_cleanup_drafts(args.dry_run)
        if args.greenrock_command == "universe":
            return run_greenrock_universe(args.universe_command, getattr(args, "tickers", None))
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
