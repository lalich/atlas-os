"""Local Atlas Command Center web app."""

from __future__ import annotations

import csv
import html
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from atlas_os import __version__
from atlas_os.agents.orchestrator import agent_cycle_summary, get_agent_run, list_agent_runs, list_agent_states, run_agent_cycle
from atlas_os.agents.tasks import list_agent_tasks
from atlas_os.agents.updates import latest_agent_update, list_agent_updates
from atlas_os.config import get_settings
from atlas_os.daily import latest_daily_brief, run_daily_cycle
from atlas_os.diagnostics import provider_diagnostics
from atlas_os.core.approvals import (
    ApprovalStatus,
    approve_approval,
    get_approval,
    list_approvals,
    reject_approval,
)
from atlas_os.core.artifacts import create_artifact, get_artifact, list_artifacts, list_artifacts_for_run
from atlas_os.core.audit_log import create_audit_log, list_audit_logs
from atlas_os.core.manual_tasks import (
    DEFAULT_PROJECT_NAME,
    PROJECT_STAGES,
    TASK_STATUSES,
    create_manual_task,
    create_project,
    list_projects,
    list_manual_tasks,
    move_manual_task_project,
    update_project_status,
    update_manual_task_status,
)
from atlas_os.core.reports import get_report, list_reports
from atlas_os.core.workflow_runs import get_workflow_run, list_workflow_runs
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.pdf_export import render_markdown_report_to_pdf
from atlas_os.greenrock.market_data import MarketDataConfigurationError
from atlas_os.greenrock.market_engine import MARKET_ARCHETYPES, classify_market_archetype
from atlas_os.greenrock.market_pulse import (
    select_analyst_slate_candidates,
    select_market_pulse_candidates,
    stage_analyst_slate_candidates,
    stage_top_market_pulse_candidates,
)
from atlas_os.greenrock.memory import compare_ticker, load_memory_rows, memory_movers, movement_explanation, movement_symbol, ticker_history
from atlas_os.greenrock.population import GREENROCK_POPULATION_LABELS
from atlas_os.greenrock.report_workbench import (
    CANDIDATE_DECISIONS,
    record_candidate_decision,
    report_readiness,
    report_workbench_summary,
)
from atlas_os.greenrock.score import calculate_score_preview, score_signal
from atlas_os.greenrock.scanner import (
    latest_scan,
    load_scan_failures,
    universe_health_rows,
    load_promotion_metadata,
    promote_scan_ticker,
    run_population_scan,
)
from atlas_os.greenrock.staging import (
    STAGING_BUCKET_LABELS,
    STAGING_BUCKET_TARGETS,
    add_staged_candidate,
    add_staged_scan_candidate,
    enrich_staged_candidates,
    load_staged_candidates,
    move_staged_candidate,
    remove_staged_candidate,
    row_missing_analytics,
    staging_analytics_status,
    staging_readiness,
    trim_staged_bucket,
    update_staged_notes,
)
from atlas_os.greenrock.staging_report import run_greenrock_staging_report_workflow, staging_report_readiness
from atlas_os.greenrock.universe import (
    GREENROCK_PLACEMENT_LABELS,
    add_ticker_to_greenrock_list,
    load_greenrock_universes,
    placement_path,
    remove_ticker_from_greenrock_list,
)
from atlas_os.greenrock.universe_manager import default_universe_manager, provider_label
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow
from atlas_os.inbox import complete_inbox_item, dismiss_inbox_item, get_inbox_item, list_inbox_items
from atlas_os.morning_brief import (
    latest_morning_brief_snapshot,
    list_morning_brief_snapshots,
    load_morning_brief_snapshot,
    save_morning_brief_snapshot,
)
from atlas_os.greenrock.scoring import signal_label


PLANNED_AGENTS = (
    ("Atlas Core", "atlas-core", "planned"),
    ("GreenRock Analyst Agent", "greenrock", "planned"),
    ("Publisher Agent", "publishing", "inactive"),
    ("Compliance Review Agent", "compliance", "planned"),
    ("Bat Signal Agent", "variance-capital", "inactive"),
    ("Insurance Follow-Up Agent", "greenrock-insurance", "inactive"),
)

PROJECTS = (
    (
        "GreenRock Analysts",
        "greenrock",
        "Monthly technical dislocation research, approval queue, and local final packet workflow.",
        "/greenrock",
        "active",
    ),
    (
        "Variance Capital / The Bat Signal",
        "variance-capital",
        "Fixture design and future signal workflow placeholder.",
        "/projects",
        "planned",
    ),
    (
        "GreenRock Insurance",
        "greenrock-insurance",
        "Insurance prospect follow-up and relationship tracking placeholder.",
        "/projects",
        "planned",
    ),
    (
        "Atlas Core",
        "atlas-core",
        "Local workflow runner, audit trail, task board, and command center foundation.",
        "/projects",
        "active",
    ),
)


@dataclass(frozen=True)
class WebResponse:
    status: int
    body: str | bytes
    content_type: str = "text/html; charset=utf-8"
    location: str | None = None


STATIC_DIR = Path(__file__).resolve().parent / "static"
GREENROCK_LOGO_PATH = STATIC_DIR / "greenrock_logo.png"
GREENROCK_LOGO_URL = "/static/greenrock_logo.png"
ATLAS_LOGO_PATH = STATIC_DIR / "atlas_logo.png"
ATLAS_LOGO_URL = "/static/atlas_logo.png"


def create_app():
    """Return a FastAPI app when FastAPI is installed."""
    try:
        from fastapi import FastAPI, Form, Request
        from fastapi.responses import HTMLResponse, RedirectResponse
    except ImportError:
        return None

    app = FastAPI(title="Atlas Command Center")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return render_dashboard()

    @app.get("/projects", response_class=HTMLResponse)
    def projects() -> str:
        return render_pt()

    @app.get("/pt", response_class=HTMLResponse)
    def pt() -> str:
        return render_pt()

    @app.post("/pt/tasks")
    def create_pt_task(name: str = Form(...), project_id: int | None = Form(None), division: str = Form("general"), notes: str = Form("")):
        save_manual_task(name, division, notes, project_id=project_id)
        return RedirectResponse(_with_status("/pt", "Task created."), status_code=303)

    @app.post("/pt/projects")
    def create_pt_project(name: str = Form(...), division: str = Form("atlas-core"), status: str = Form("planned")):
        save_project(name, division, status)
        return RedirectResponse(_with_status("/pt", "Project created."), status_code=303)

    @app.post("/pt/projects/{project_id}/status")
    def update_pt_project(project_id: int, status: str = Form("planned")):
        save_project_status(project_id, status)
        return RedirectResponse(_with_status("/pt", "Project stage updated."), status_code=303)

    @app.get("/atlas/morning-brief", response_class=HTMLResponse)
    def morning_brief() -> str:
        return render_morning_brief()

    @app.post("/atlas/morning-brief/snapshot")
    def morning_brief_snapshot():
        message = save_morning_brief_snapshot_from_browser()
        return RedirectResponse(_with_status("/atlas/morning-brief", message), status_code=303)

    @app.get("/atlas/morning-brief/history", response_class=HTMLResponse)
    def morning_brief_history() -> str:
        return render_morning_brief_history()

    @app.get("/atlas/morning-brief/history/{snapshot_id}", response_class=HTMLResponse)
    def morning_brief_snapshot_detail(snapshot_id: str) -> str:
        return render_morning_brief_snapshot(snapshot_id)

    @app.get("/atlas/inbox", response_class=HTMLResponse)
    def atlas_inbox() -> str:
        return render_atlas_inbox()

    @app.get("/atlas/wall", response_class=HTMLResponse)
    def atlas_wall() -> str:
        return render_atlas_wall()

    @app.post("/atlas/wall/run")
    def atlas_wall_run():
        message = run_agent_cycle_from_browser("use_latest_scan", 24.0)
        return RedirectResponse(_with_status("/atlas/wall", message), status_code=303)

    @app.get("/atlas/inbox/{item_id}", response_class=HTMLResponse)
    def atlas_inbox_detail(item_id: str) -> str:
        return render_atlas_inbox_detail(item_id)

    @app.post("/atlas/inbox/{item_id}/dismiss")
    def atlas_inbox_dismiss(item_id: str):
        dismiss_atlas_inbox_item(item_id)
        return RedirectResponse(_with_status("/atlas/inbox", "Inbox item dismissed."), status_code=303)

    @app.post("/atlas/inbox/{item_id}/complete")
    def atlas_inbox_complete(item_id: str):
        complete_atlas_inbox_item(item_id)
        return RedirectResponse(_with_status("/atlas/inbox", "Inbox item completed."), status_code=303)

    @app.get("/greenrock", response_class=HTMLResponse)
    def greenrock() -> str:
        return render_greenrock()

    @app.get("/greenrock/report-workbench", response_class=HTMLResponse)
    def greenrock_report_workbench(request: Request) -> str:
        return render_greenrock_report_workbench(request.query_params.get("status"))

    @app.post("/greenrock/report-workbench/action")
    def greenrock_report_workbench_action(action: str = Form("")):
        message, target = run_greenrock_report_workbench_action(action)
        return RedirectResponse(_with_status(target, message), status_code=303)

    @app.post("/greenrock/report-workbench/candidate-decision")
    def greenrock_report_workbench_candidate_decision(
        ticker: str = Form(""),
        decision: str = Form(""),
        note: str = Form(""),
        related_scan_id: str = Form(""),
        related_daily_id: str = Form(""),
        related_report_run_id: str = Form(""),
    ):
        message = save_candidate_decision_from_browser(ticker, decision, note, related_scan_id, related_daily_id, related_report_run_id)
        return RedirectResponse(_with_status("/greenrock/report-workbench#candidate-review", message), status_code=303)

    @app.get("/greenrock/picks", response_class=HTMLResponse)
    def greenrock_picks() -> str:
        return render_greenrock_picks_board()

    @app.get("/greenrock/discovery", response_class=HTMLResponse)
    def greenrock_discovery() -> str:
        return render_greenrock_discovery()

    @app.get("/greenrock/scanner", response_class=HTMLResponse)
    def greenrock_scanner() -> str:
        return render_greenrock_scanner()

    @app.get("/greenrock/universe", response_class=HTMLResponse)
    def greenrock_universe(request: Request) -> str:
        return render_greenrock_universe(query=dict(request.query_params))

    @app.get("/greenrock/market-pulse", response_class=HTMLResponse)
    def greenrock_market_pulse(request: Request) -> str:
        return render_greenrock_market_pulse(request.query_params.get("status"))

    @app.get("/greenrock/market-pulse/stage/confirm", response_class=HTMLResponse)
    def greenrock_market_pulse_stage_confirm(request: Request) -> str:
        return render_market_pulse_stage_confirmation(request.query_params.get("status"), request.query_params.get("slate", "market_pulse"))

    @app.post("/greenrock/market-pulse/stage")
    def greenrock_market_pulse_stage(overwrite_staging: str = Form(""), slate_mode: str = Form("market_pulse")):
        message = stage_market_pulse_from_browser(overwrite_staging == "yes", slate_mode)
        return RedirectResponse(_with_status("/greenrock/market-pulse", message), status_code=303)

    @app.get("/greenrock/market-pulse/report/confirm", response_class=HTMLResponse)
    def greenrock_market_pulse_report_confirm(request: Request) -> str:
        return render_market_pulse_report_confirmation(request.query_params.get("status"))

    @app.post("/greenrock/market-pulse/report")
    def greenrock_market_pulse_report(allow_underfilled: str = Form("")):
        message = generate_market_pulse_report_from_browser(allow_underfilled == "yes")
        redirect_target = _review_path_from_status(message) or "/greenrock/market-pulse"
        return RedirectResponse(_with_status(redirect_target, message), status_code=303)

    @app.post("/greenrock/scanner/run")
    def greenrock_scanner_run(population: str = Form("qqq")):
        message = run_greenrock_scan_from_browser(population)
        return RedirectResponse(_with_status("/greenrock/scanner", message), status_code=303)

    @app.post("/greenrock/scanner/promote", response_class=HTMLResponse)
    def greenrock_scanner_promote(
        scan_id: str = Form(""),
        ticker: str = Form(""),
        list_key: str = Form(""),
    ) -> str:
        return promote_greenrock_scan_ticker(scan_id, ticker, list_key)

    @app.post("/greenrock/scanner/promote-batch", response_class=HTMLResponse)
    def greenrock_scanner_promote_batch(
        scan_id: str = Form(""),
        tickers: list[str] = Form(default=[]),
        list_key: str = Form(""),
    ) -> str:
        return promote_greenrock_scan_tickers(scan_id, tuple(tickers), list_key)

    @app.post("/greenrock/scanner/stage-batch", response_class=HTMLResponse)
    def greenrock_scanner_stage_batch(
        scan_id: str = Form(""),
        tickers: list[str] = Form(default=[]),
        bucket: str = Form("research"),
    ) -> str:
        return stage_greenrock_scan_tickers(scan_id, tuple(tickers), bucket)

    @app.get("/greenrock/watchlists", response_class=HTMLResponse)
    def greenrock_watchlists() -> str:
        return render_greenrock_watchlists()

    @app.get("/greenrock/staging", response_class=HTMLResponse)
    def greenrock_staging() -> str:
        return render_greenrock_staging()

    @app.get("/greenrock/staging/generate/confirm", response_class=HTMLResponse)
    def greenrock_staging_generate_confirm() -> str:
        return render_greenrock_staging_generation_confirmation()

    @app.post("/greenrock/staging/generate")
    def greenrock_staging_generate(allow_underfilled: str = Form("")):
        message = generate_greenrock_staging_report(allow_underfilled == "yes")
        return RedirectResponse(_with_status("/greenrock/staging", message), status_code=303)

    @app.post("/greenrock/staging/add", response_class=HTMLResponse)
    def greenrock_staging_add(
        ticker: str = Form(""),
        bucket: str = Form("research"),
        source_list: str = Form("manual"),
        notes: str = Form(""),
    ) -> str:
        return stage_greenrock_candidate(ticker, bucket, source_list, notes)

    @app.post("/greenrock/staging/move", response_class=HTMLResponse)
    def greenrock_staging_move(ticker: str = Form(""), bucket: str = Form("research")) -> str:
        return move_greenrock_staging_candidate(ticker, bucket)

    @app.post("/greenrock/staging/remove", response_class=HTMLResponse)
    def greenrock_staging_remove(ticker: str = Form("")) -> str:
        return remove_greenrock_staging_candidate(ticker)

    @app.post("/greenrock/staging/notes", response_class=HTMLResponse)
    def greenrock_staging_notes(ticker: str = Form(""), notes: str = Form("")) -> str:
        return save_greenrock_staging_notes(ticker, notes)

    @app.post("/greenrock/staging/trim", response_class=HTMLResponse)
    def greenrock_staging_trim(bucket: str = Form("")) -> str:
        return trim_greenrock_staging_bucket(bucket)

    @app.post("/greenrock/staging/enrich", response_class=HTMLResponse)
    def greenrock_staging_enrich() -> str:
        return enrich_greenrock_staging_candidates()

    @app.post("/greenrock/watchlists/remove", response_class=HTMLResponse)
    def greenrock_watchlists_remove(ticker: str = Form(""), list_key: str = Form("")) -> str:
        return remove_greenrock_watchlist_ticker(ticker, list_key)

    @app.get("/greenrock/score", response_class=HTMLResponse)
    def greenrock_score() -> str:
        return render_greenrock_score()

    @app.post("/greenrock/score", response_class=HTMLResponse)
    def greenrock_score_post(ticker: str = Form("")) -> str:
        return render_greenrock_score(ticker=ticker)

    @app.post("/greenrock/run-report")
    def run_greenrock_report(data_mode: str = Form("mock")):
        ok, message = run_greenrock_report_from_browser(data_mode)
        return RedirectResponse(_with_status("/greenrock", message), status_code=303)

    @app.get("/greenrock/final-reports", response_class=HTMLResponse)
    def final_reports() -> str:
        return render_greenrock_final_reports()

    @app.get("/greenrock/reports/{run_id}/review", response_class=HTMLResponse)
    def greenrock_report_review(run_id: str) -> str:
        return render_greenrock_report_review(run_id)

    @app.get("/approvals/{approval_id}", response_class=HTMLResponse)
    def approval_detail(approval_id: int) -> str:
        return render_approval_detail(approval_id)

    @app.get("/approvals/{approval_id}/confirm", response_class=HTMLResponse)
    def approval_confirm(approval_id: int, action: str = "approve", return_to: str = "/greenrock") -> str:
        return render_approval_confirmation(approval_id, action, return_to)

    @app.post("/approvals/{approval_id}/decide")
    def approval_decide(approval_id: int, action: str = Form(...), return_to: str = Form("/greenrock")):
        decide_approval(approval_id, action)
        return RedirectResponse(_with_status(return_to, f"Approval {approval_id} {action}d."), status_code=303)

    @app.post("/greenrock/approvals/{approval_id}/export-pdf")
    def export_pdf(approval_id: int, return_to: str = Form("/greenrock")):
        export_greenrock_pdf(approval_id)
        return RedirectResponse(_with_status(return_to, f"PDF exported for approval {approval_id}."), status_code=303)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks() -> str:
        return render_pt()

    @app.post("/tasks")
    def create_task(name: str = Form(...), division: str = Form("general"), notes: str = Form(""), project_id: int | None = Form(None)):
        save_manual_task(name, division, notes, project_id=project_id)
        return RedirectResponse(_with_status("/pt", "Manual task created."), status_code=303)

    @app.post("/tasks/{task_id}/status")
    def update_task(task_id: int, status: str = Form(...)):
        save_task_status(task_id, status)
        return RedirectResponse(_with_status("/pt", f"Task {task_id} updated."), status_code=303)

    @app.get("/agents", response_class=HTMLResponse)
    def agents() -> str:
        return render_agents()

    @app.get("/agents/{agent_name}", response_class=HTMLResponse)
    def agent_detail(agent_name: str) -> str:
        return render_agent_update_history(agent_name)

    @app.post("/agents/run")
    def agents_run(
        market_scan_policy: str = Form("use_latest_scan"),
        stale_hours: float = Form(24.0),
    ):
        message = run_agent_cycle_from_browser(market_scan_policy, stale_hours)
        return RedirectResponse(_with_status("/agents", message), status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(run_id: str) -> str:
        return render_run_detail(run_id)

    @app.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
    def artifact_detail(artifact_id: int) -> str:
        return render_artifact_detail(artifact_id)

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request) -> str:
        return render_reports(filters=dict(request.query_params), status_message=request.query_params.get("status"))

    @app.get("/open-local")
    def open_local(path: str):
        open_local_path(path)
        return RedirectResponse("/", status_code=303)

    @app.get("/static/{filename}")
    def static_asset(filename: str):
        from fastapi import Response

        response = _static_response(filename)
        return Response(content=response.body, status_code=response.status, media_type=response.content_type)

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    provider = provider_diagnostics()
    print(f"Atlas Command Center running at http://{host}:{port}")
    print(
        "Development mode: local only. "
        f"Real data provider: {provider.status_label}. "
        f"Current provider: {provider.active_provider_name}."
    )
    if provider.score_calculator_ready:
        print("Score Calculator provider: ready.")
    else:
        print("Score Calculator setup hint:")
        print(provider.recommended_fix_command)
    print("No publish, email, trading, or client-file actions are enabled.")
    app = create_app()
    if app is not None:
        try:
            import uvicorn
        except ImportError:
            pass
        else:
            uvicorn.run(app, host=host, port=port)
            return

    server = ThreadingHTTPServer((host, port), _CommandCenterHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAtlas Command Center stopped.")
    finally:
        server.server_close()


def dispatch_request(method: str, path: str, body: str = "") -> WebResponse:
    parsed = urlparse(path)
    route = parsed.path
    query = parse_qs(parsed.query)
    form = _parse_form(body)
    form_values = parse_qs(body, keep_blank_values=True)

    if method == "GET" and route == "/":
        return WebResponse(200, render_dashboard(_first(query, "status")))
    if method == "GET" and route in {"/projects", "/tasks", "/pt"}:
        return WebResponse(200, render_pt(_first(query, "status"), {key: values[0] for key, values in query.items()}))
    if method == "GET" and route == "/atlas/morning-brief":
        return WebResponse(200, render_morning_brief(_first(query, "status")))
    if method == "GET" and route == "/atlas/morning-brief/history":
        return WebResponse(200, render_morning_brief_history(_first(query, "status")))
    if method == "GET" and route.startswith("/atlas/morning-brief/history/"):
        snapshot_id = unquote(route.removeprefix("/atlas/morning-brief/history/"))
        try:
            return WebResponse(200, render_morning_brief_snapshot(snapshot_id, _first(query, "status")))
        except KeyError:
            return _error_response(404, "Snapshot Not Found", f"No Morning Brief snapshot exists for {snapshot_id}.")
    if method == "GET" and route == "/atlas/inbox":
        return WebResponse(200, render_atlas_inbox(_first(query, "status")))
    if method == "GET" and route == "/atlas/wall":
        return WebResponse(200, render_atlas_wall(_first(query, "status")))
    if method == "GET" and route.startswith("/atlas/inbox/"):
        item_id = unquote(route.removeprefix("/atlas/inbox/").strip("/"))
        try:
            return WebResponse(200, render_atlas_inbox_detail(item_id, _first(query, "status")))
        except KeyError:
            return _error_response(404, "Inbox Item Not Found", f"No inbox item exists for {item_id}.")
    if method == "GET" and route == "/greenrock":
        return WebResponse(200, render_greenrock(_first(query, "status")))
    if method == "GET" and route == "/greenrock/report-workbench":
        return WebResponse(200, render_greenrock_report_workbench(_first(query, "status")))
    if method == "GET" and route == "/greenrock/picks":
        return WebResponse(200, render_greenrock_picks_board(_first(query, "status")))
    if method == "GET" and route == "/greenrock/discovery":
        return WebResponse(200, render_greenrock_discovery(_first(query, "status")))
    if method == "GET" and route == "/greenrock/scanner":
        return WebResponse(200, render_greenrock_scanner(_first(query, "status"), query))
    if method == "GET" and route == "/greenrock/universe":
        return WebResponse(200, render_greenrock_universe(_first(query, "status"), {key: values[0] for key, values in query.items()}))
    if method == "GET" and route == "/greenrock/market-pulse":
        return WebResponse(200, render_greenrock_market_pulse(_first(query, "status")))
    if method == "GET" and route == "/greenrock/market-pulse/stage/confirm":
        return WebResponse(200, render_market_pulse_stage_confirmation(_first(query, "status"), _first(query, "slate") or "market_pulse"))
    if method == "GET" and route == "/greenrock/market-pulse/report/confirm":
        return WebResponse(200, render_market_pulse_report_confirmation(_first(query, "status")))
    if method == "GET" and route == "/greenrock/watchlists":
        return WebResponse(200, render_greenrock_watchlists(_first(query, "status")))
    if method == "GET" and route == "/greenrock/staging":
        return WebResponse(200, render_greenrock_staging(_first(query, "status")))
    if method == "GET" and route == "/greenrock/staging/generate/confirm":
        return WebResponse(200, render_greenrock_staging_generation_confirmation(_first(query, "status")))
    if method == "GET" and route == "/greenrock/score":
        return WebResponse(200, render_greenrock_score(_first(query, "ticker") or ""))
    if method == "GET" and route == "/greenrock/final-reports":
        return WebResponse(200, render_greenrock_final_reports(_first(query, "status")))
    if method == "GET" and route.startswith("/greenrock/reports/") and route.endswith("/review"):
        run_id = unquote(route.removeprefix("/greenrock/reports/").removesuffix("/review"))
        try:
            return WebResponse(200, render_greenrock_report_review(run_id, _first(query, "status")))
        except KeyError:
            return _error_response(404, "Report Not Found", f"No GreenRock report exists for run {run_id}.")
    if method == "GET" and route == "/agents":
        return WebResponse(200, render_agents(_first(query, "status")))
    if method == "GET" and route.startswith("/agents/runs/"):
        run_id = unquote(route.removeprefix("/agents/runs/"))
        try:
            return WebResponse(200, render_agent_run_detail(run_id, _first(query, "status")))
        except KeyError:
            return _error_response(404, "Agent Run Not Found", f"No agent run exists for {run_id}.")
    if method == "GET" and route.startswith("/agents/"):
        agent_name = unquote(route.removeprefix("/agents/"))
        return WebResponse(200, render_agent_update_history(agent_name, _first(query, "status")))
    if method == "GET" and route == "/reports":
        return WebResponse(200, render_reports(_first(query, "status"), {key: values[0] for key, values in query.items()}))
    if method == "GET" and route == "/open-local":
        open_local_path(_first(query, "path"))
        return WebResponse(303, "", location="/")
    if method == "GET" and route.startswith("/static/"):
        return _static_response(route.removeprefix("/static/"))
    if method == "GET" and route.startswith("/runs/"):
        return WebResponse(200, render_run_detail(unquote(route.removeprefix("/runs/"))))
    if method == "GET" and route.startswith("/artifacts/"):
        artifact_id = _parse_int(route.removeprefix("/artifacts/"))
        if artifact_id is None:
            return _error_response(400, "Invalid Artifact", "Artifact ID must be a number.")
        try:
            return WebResponse(200, render_artifact_detail(artifact_id))
        except KeyError:
            return _error_response(404, "Artifact Not Found", f"No artifact exists for ID {artifact_id}.")
    if method == "GET" and route.startswith("/approvals/") and route.endswith("/confirm"):
        approval_id = _route_int_part(route, 2)
        if approval_id is None:
            return _error_response(400, "Invalid Approval", "Approval ID must be a number.")
        try:
            return WebResponse(
                200,
                render_approval_confirmation(
                    approval_id,
                    _first(query, "action") or "approve",
                    _first(query, "return_to") or "/greenrock",
                ),
            )
        except KeyError:
            return _error_response(404, "Approval Not Found", f"No approval exists for ID {approval_id}.")
    if method == "GET" and route.startswith("/approvals/"):
        approval_id = _parse_int(route.removeprefix("/approvals/"))
        if approval_id is None:
            return _error_response(400, "Invalid Approval", "Approval ID must be a number.")
        try:
            return WebResponse(200, render_approval_detail(approval_id))
        except KeyError:
            return _error_response(404, "Approval Not Found", f"No approval exists for ID {approval_id}.")
    if method == "POST" and route == "/tasks":
        save_manual_task(form.get("name", ""), form.get("division", "general"), form.get("notes", ""), _parse_int(form.get("project_id", "")))
        return WebResponse(303, "", location=_with_status("/pt", "Manual task created."))
    if method == "POST" and route == "/pt/tasks":
        save_manual_task(form.get("name", ""), form.get("division", "general"), form.get("notes", ""), _parse_int(form.get("project_id", "")))
        return WebResponse(303, "", location=_with_status("/pt", "Task created."))
    if method == "POST" and route == "/pt/projects":
        save_project(form.get("name", ""), form.get("division", "atlas-core"), form.get("status", "planned"))
        return WebResponse(303, "", location=_with_status("/pt", "Project created."))
    if method == "POST" and route.startswith("/pt/projects/") and route.endswith("/status"):
        project_id = _route_int_part(route, 3)
        if project_id is None:
            return _error_response(400, "Invalid Project", "Project ID must be a number.")
        save_project_status(project_id, form.get("status", "planned"))
        return WebResponse(303, "", location=_with_status("/pt", "Project stage updated."))
    if method == "POST" and route == "/greenrock/run-report":
        ok, message = run_greenrock_report_from_browser(form.get("data_mode", "mock"))
        return WebResponse(303, "", location=_with_status("/greenrock", message))
    if method == "POST" and route == "/atlas/morning-brief/snapshot":
        return WebResponse(303, "", location=_with_status("/atlas/morning-brief", save_morning_brief_snapshot_from_browser()))
    if method == "POST" and route == "/agents/run":
        return WebResponse(
            303,
            "",
            location=_with_status(
                "/agents",
                run_agent_cycle_from_browser(form.get("market_scan_policy", "use_latest_scan"), _float_form_value(form.get("stale_hours"), 24.0)),
            ),
        )
    if method == "POST" and route == "/atlas/wall/run":
        return WebResponse(303, "", location=_with_status("/atlas/wall", run_agent_cycle_from_browser("use_latest_scan", 24.0)))
    if method == "POST" and route.startswith("/atlas/inbox/") and route.endswith("/dismiss"):
        item_id = unquote(route.removeprefix("/atlas/inbox/").removesuffix("/dismiss").strip("/"))
        try:
            dismiss_atlas_inbox_item(item_id)
        except KeyError:
            return _error_response(404, "Inbox Item Not Found", f"No inbox item exists for {item_id}.")
        return WebResponse(303, "", location=_with_status("/atlas/inbox", "Inbox item dismissed."))
    if method == "POST" and route.startswith("/atlas/inbox/") and route.endswith("/complete"):
        item_id = unquote(route.removeprefix("/atlas/inbox/").removesuffix("/complete").strip("/"))
        try:
            complete_atlas_inbox_item(item_id)
        except KeyError:
            return _error_response(404, "Inbox Item Not Found", f"No inbox item exists for {item_id}.")
        return WebResponse(303, "", location=_with_status("/atlas/inbox", "Inbox item completed."))
    if method == "POST" and route == "/greenrock/report-workbench/action":
        message, target = run_greenrock_report_workbench_action(form.get("action", ""))
        return WebResponse(303, "", location=_with_status(target, message))
    if method == "POST" and route == "/greenrock/report-workbench/candidate-decision":
        message = save_candidate_decision_from_browser(
            form.get("ticker", ""),
            form.get("decision", ""),
            form.get("note", ""),
            form.get("related_scan_id", ""),
            form.get("related_daily_id", ""),
            form.get("related_report_run_id", ""),
        )
        return WebResponse(303, "", location=_with_status("/greenrock/report-workbench#candidate-review", message))
    if method == "POST" and route == "/greenrock/scanner/run":
        message = run_greenrock_scan_from_browser(form.get("population", "qqq"))
        return WebResponse(303, "", location=_with_status("/greenrock/scanner", message))
    if method == "POST" and route == "/greenrock/market-pulse/stage":
        return WebResponse(
            303,
            "",
                location=_with_status(
                    "/greenrock/market-pulse",
                    stage_market_pulse_from_browser(form.get("overwrite_staging") == "yes", form.get("slate_mode", "market_pulse")),
                ),
        )
    if method == "POST" and route == "/greenrock/market-pulse/report":
        message = generate_market_pulse_report_from_browser(form.get("allow_underfilled") == "yes")
        return WebResponse(303, "", location=_with_status(_review_path_from_status(message) or "/greenrock/market-pulse", message))
    if method == "POST" and route == "/greenrock/scanner/promote":
        return WebResponse(
            200,
            promote_greenrock_scan_ticker(
                scan_id=form.get("scan_id", ""),
                ticker=form.get("ticker", ""),
                list_key=form.get("list_key", ""),
            ),
        )
    if method == "POST" and route == "/greenrock/scanner/promote-batch":
        return WebResponse(
            200,
            promote_greenrock_scan_tickers(
                scan_id=form.get("scan_id", ""),
                tickers=tuple(value for value in form_values.get("tickers", ()) if value),
                list_key=form.get("list_key", ""),
            ),
        )
    if method == "POST" and route == "/greenrock/scanner/stage-batch":
        return WebResponse(
            200,
            stage_greenrock_scan_tickers(
                scan_id=form.get("scan_id", ""),
                tickers=tuple(value for value in form_values.get("tickers", ()) if value),
                bucket=form.get("bucket", "research"),
            ),
        )
    if method == "POST" and route == "/greenrock/staging/add":
        return WebResponse(
            200,
            stage_greenrock_candidate(
                ticker=form.get("ticker", ""),
                bucket=form.get("bucket", "research"),
                source_list=form.get("source_list", "manual"),
                notes=form.get("notes", ""),
            ),
        )
    if method == "POST" and route == "/greenrock/staging/move":
        return WebResponse(200, move_greenrock_staging_candidate(form.get("ticker", ""), form.get("bucket", "research")))
    if method == "POST" and route == "/greenrock/staging/remove":
        return WebResponse(200, remove_greenrock_staging_candidate(form.get("ticker", "")))
    if method == "POST" and route == "/greenrock/staging/notes":
        return WebResponse(200, save_greenrock_staging_notes(form.get("ticker", ""), form.get("notes", "")))
    if method == "POST" and route == "/greenrock/staging/trim":
        return WebResponse(200, trim_greenrock_staging_bucket(form.get("bucket", "")))
    if method == "POST" and route == "/greenrock/staging/enrich":
        return WebResponse(200, enrich_greenrock_staging_candidates())
    if method == "POST" and route == "/greenrock/staging/generate":
        return WebResponse(
            303,
            "",
            location=_with_status("/greenrock/staging", generate_greenrock_staging_report(form.get("allow_underfilled") == "yes")),
        )
    if method == "POST" and route == "/greenrock/watchlists/remove":
        return WebResponse(200, remove_greenrock_watchlist_ticker(form.get("ticker", ""), form.get("list_key", "")))
    if method == "POST" and route == "/greenrock/score":
        return WebResponse(
            200,
            render_greenrock_score(
                ticker=form.get("ticker", ""),
            ),
        )
    if method == "POST" and route == "/greenrock/score/save":
        return WebResponse(
            200,
            save_greenrock_score_ticker(
                ticker=form.get("ticker", ""),
                list_key=form.get("list_key", ""),
            ),
        )
    if method == "POST" and route.startswith("/tasks/") and route.endswith("/status"):
        task_id = _route_int_part(route, 2)
        if task_id is None:
            return _error_response(400, "Invalid Task", "Task ID must be a number.")
        try:
            save_task_status(task_id, form.get("status", "pending"))
        except (KeyError, ValueError) as error:
            return _error_response(400, "Task Update Blocked", str(error))
        return WebResponse(303, "", location=_with_status("/pt", f"Task {task_id} updated."))
    if method == "POST" and route.startswith("/approvals/") and route.endswith("/decide"):
        approval_id = _route_int_part(route, 2)
        if approval_id is None:
            return _error_response(400, "Invalid Approval", "Approval ID must be a number.")
        action = form.get("action", "approve")
        return_to = form.get("return_to", "/greenrock")
        try:
            decide_approval(approval_id, action)
        except KeyError:
            return _error_response(404, "Approval Not Found", f"No approval exists for ID {approval_id}.")
        except ValueError as error:
            return _error_response(400, "Approval Update Blocked", str(error))
        return WebResponse(303, "", location=_with_status(return_to, f"Approval {approval_id} {action}d."))
    if method == "POST" and route.startswith("/greenrock/approvals/") and route.endswith("/export-pdf"):
        parts = route.strip("/").split("/")
        if len(parts) != 4:
            return _error_response(404, "Route Not Found", "Use /greenrock/approvals/<approval_id>/export-pdf.")
        approval_id = _parse_int(parts[2])
        if approval_id is None:
            return _error_response(400, "Invalid Approval", "Approval ID must be a number.")
        try:
            export_greenrock_pdf(approval_id)
        except KeyError:
            return _error_response(404, "Approval Not Found", f"No approval exists for ID {approval_id}.")
        except ValueError as error:
            return _error_response(400, "PDF Export Blocked", str(error))
        return WebResponse(303, "", location=_with_status(form.get("return_to", "/greenrock"), f"PDF exported for approval {approval_id}."))

    return WebResponse(404, _page("Not Found", "<section class='panel'><h1>Not Found</h1></section>"))


def render_dashboard(status_message: str | None = None) -> str:
    context = _load_context()
    pending_approvals = [approval for approval in context["approvals"] if approval.status == ApprovalStatus.PENDING]
    reports_ready = _approved_reports_missing_pdf(context)
    completed_runs = [run for run in context["runs"] if run.status in {"completed", "approved", "awaiting_approval"}]
    failed_runs = [run for run in context["runs"] if run.status == "failed"]
    inbox_items = _build_inbox_items(context, pending_approvals, reports_ready, failed_runs)
    latest_source = _latest_report_data_source(context["latest_report"])
    brief = _morning_brief_data(context)
    cycle = agent_cycle_summary(get_settings().output_dir)
    agent_inbox_items = list_inbox_items(get_settings().output_dir)

    content = f"""
    {_status_banner(status_message)}
    {_branded_title_hero("Atlas Inbox", "Atlas OS Command Center", "What needs your attention", context)}
    <section class="panel warning-panel">
      <div class="section-head">
        <h2>Morning Brief</h2>
        <span class="badge">Operator attention layer</span>
      </div>
      <section class="board-meta">
        {_attention_card("green" if brief["scan_complete"] else "yellow", brief["scan_status"], "Scan Status", brief["latest_scan_id"] or "No latest scan")}
        {_attention_card("yellow" if brief["important_changes"] else "neutral", str(brief["important_changes"]), "Important Changes", "Memory movers and new leaders")}
        {_attention_card("red" if brief["pending_approvals"] else "neutral", str(brief["pending_approvals"]), "Pending Approvals", "Human review queue")}
        {_attention_card("neutral", "Open", "Morning Brief", "Daily local command view")}
      </section>
      <p><a class="button" href="/atlas/morning-brief">Open Morning Brief</a></p>
    </section>
    <section class="attention-grid">
      {_attention_card("red", str(len(pending_approvals)), "Pending Approvals", "Human review required")}
      {_attention_card("yellow", str(len(reports_ready)), "Reports Ready For PDF Export", "Approved locally, PDF not exported")}
      {_attention_card("green", str(len(completed_runs)), "Completed Workflows", "Finished or approval-gated runs")}
      {_attention_card("neutral", _safe(latest_source or "none"), "Latest Data Source", "Shown on the newest GreenRock draft")}
    </section>
    <section class="panel command-actions">
      <div class="section-head">
        <h2>Agent Cycle</h2>
        <span class="subtle">Safe local orchestration</span>
      </div>
      <section class="board-meta">
        {_attention_card("neutral", _safe(cycle["last_run"]), "Last Run", "Last Agent Cycle timestamp")}
        {_attention_card("green", str(cycle["completed"]), "Completed Agents", "Finished in latest cycle")}
        {_attention_card("red" if cycle["failed"] else "neutral", str(cycle["failed"]), "Failed Agents", "Needs inspection")}
        {_attention_card("yellow" if cycle["blocked"] else "neutral", str(cycle["blocked"]), "Blocked Agents", "Human gate or missing inputs")}
        {_attention_card("neutral", str(cycle["inbox_items_generated"]), "Inbox Items Generated", "Latest cycle")}
      </section>
      <div class="action-row">
        <form method="post" action="/agents/run" onsubmit="return confirm('Run the safe local Agent Cycle using latest scan only? This creates local records and inbox items only.');">
          <input type="hidden" name="market_scan_policy" value="use_latest_scan">
          <input type="hidden" name="stale_hours" value="24">
          <button type="submit">Run Agent Cycle</button>
        </form>
        <a class="button secondary" href="/agents">Open Agent Monitor</a>
        <a class="button secondary" href="/atlas/inbox">Open Atlas Inbox</a>
      </div>
    </section>
    {_future_integrations_panel()}
    <section class="panel">
      <div class="section-head">
        <h2>Atlas Inbox</h2>
        <span class="subtle">Checklist-style operator queue</span>
      </div>
      <div class="inbox-list">{''.join(_inbox_card(_inbox_item_to_card(item)) for item in agent_inbox_items[:8]) or ''.join(_inbox_card(item) for item in inbox_items)}</div>
    </section>
    <section class="nav-grid">
      {_nav_card("Projects & Tasks", "/pt", "Consolidated local project/task operating queue")}
      {_nav_card("GreenRock Analysts", "/greenrock", "Run latest draft, approvals, candidates")}
      {_nav_card("Universe Manager", "/greenrock/universe", "Provider health, master universe, and duplicate removal")}
      {_nav_card("Market Pulse", "/greenrock/market-pulse", "Top ranked opportunities by market archetype")}
      {_nav_card("GreenRock Picks Board", "/greenrock/picks", "Mega Rock, large-cap, and small/mid-cap picks")}
      {_nav_card("GreenRock Market Scanner", "/greenrock/scanner", "Population scans before report picks")}
      {_nav_card("Report Candidate Staging", "/greenrock/staging", "Final local curation before approval-gated drafts")}
      {_nav_card("Score Any Ticker", "/greenrock/score", "Preview GreenRock Score without report artifacts")}
      {_nav_card("Agent Monitor", "/agents", "Planned local agent activity HUD")}
      {_nav_card("Approvals", "/greenrock", "Pending report approval actions")}
      {_nav_card("Final PDF Archive", "/greenrock/final-reports", "Approved local PDFs preserved long-term")}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Recent Workflow Feed</h2>
        <span class="subtle">Latest run, approval, and PDF state</span>
      </div>
      {_workflow_feed(_primary_workflow_runs(context), context)}
    </section>
    """
    return _page("Atlas Command Center", content, active="/")


def render_morning_brief(status_message: str | None = None) -> str:
    context = _load_context()
    brief = _morning_brief_data(context)
    movers = brief["movers"]
    actions = "".join(_inbox_card(item) for item in brief["action_items"])
    latest_snapshot = latest_morning_brief_snapshot(get_settings().output_dir)
    daily_brief = latest_daily_brief(get_settings().output_dir)
    report_state = report_readiness(get_settings().output_dir, get_settings().db_path)
    content = f"""
    {_status_banner(status_message)}
    {_branded_title_hero("Morning Brief", "Atlas OS Command Center", "Local operator attention view. No email, publishing, trading, client files, or external calls.", context)}
    {_daily_intelligence_panel(daily_brief)}
    {_morning_brief_action_buttons(context, brief)}
    <section class="panel command-actions">
      <div class="section-head">
        <h2>Morning Brief Snapshots</h2>
        <span class="subtle">Local operating log</span>
      </div>
      <p class="subtle">Latest snapshot: {_safe(latest_snapshot["timestamp"] if latest_snapshot else "none saved yet")}</p>
      <div class="action-row">
        <form method="post" action="/atlas/morning-brief/snapshot" onsubmit="return confirm('Save a local Morning Brief snapshot?');">
          <button type="submit">Save Morning Brief Snapshot</button>
        </form>
        <a class="button secondary" href="/atlas/morning-brief/history">Morning Brief History</a>
      </div>
    </section>
    <section class="board-meta">
      {_attention_card("green" if brief["scan_complete"] else "yellow", brief["scan_status"], "Latest Scan", brief["latest_scan_id"] or "Run Market Pulse")}
      {_attention_card("neutral", str(brief["universe_size"]), "Universe Size", "Master Universe")}
      {_attention_card("green", str(brief["scored_count"]), "Scored Count", f"skipped/failures {brief['skipped_count']}/{brief['provider_failures']}")}
      {_attention_card("neutral", str(brief["high_confidence_count"]), "High Confidence", "Confidence >= 75")}
      {_attention_card("neutral", _safe(brief["last_agent_cycle"]), "Last Agent Cycle", "Safe local agents")}
    </section>
    {_report_readiness_block(report_state)}
    <section class="panel">
      <div class="section-head"><h2>Agent Health</h2><span class="subtle">Latest local cycle</span></div>
      <div class="agent-grid">{''.join(_agent_health_card(agent) for agent in brief["agent_health_cards"])}</div>
    </section>
    {_agent_cycle_diff_block(brief["agent_run_summary"].get("diff", {}))}
    <section class="panel">
      <div class="section-head"><h2>Agent Inbox Items</h2><span class="subtle">Open local items</span></div>
      <div class="inbox-list">{''.join(_inbox_card(_inbox_item_to_card(item)) for item in brief["agent_inbox_items"][:8]) or "<p class='empty'>No agent inbox items yet.</p>"}</div>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Research Priority Count</h2><span class="subtle">Latest scan</span></div>
      <div class="board-meta">{''.join(_attention_card("neutral", str(count), label, "ranked scan rows") for label, count in brief["priority_counts"].items())}</div>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Top Movers</h2><span class="subtle">Atlas Memory</span></div>
      <div class="watchlist-grid">
        {_memory_mover_block("Rank Improvers", movers["rank_improvers"][:3])}
        {_memory_mover_block("Score Improvers", movers["score_improvers"][:3])}
        {_memory_mover_block("Confidence Improvers", movers["confidence_improvers"][:3])}
        {_memory_mover_block("Evidence Improvers", movers["evidence_improvers"][:3])}
        {_memory_mover_block("Deteriorations", movers["deteriorations"][:3])}
      </div>
    </section>
    <section class="panel">
      <h2>New Archetype Leaders</h2>
      {_new_archetype_leaders(get_settings().output_dir)}
    </section>
    <section class="board-meta">
      {_attention_card("red" if brief["pending_approvals"] else "neutral", str(brief["pending_approvals"]), "Reports Awaiting Approval", "Approval-gated drafts")}
      {_attention_card("yellow" if brief["pdf_ready"] else "neutral", str(brief["pdf_ready"]), "PDFs Ready", "Approved, not exported")}
      {_attention_card("green" if brief["pdf_exported"] else "neutral", str(brief["pdf_exported"]), "PDFs Exported", "Local final PDFs")}
      {_attention_card("neutral", str(brief["important_changes"]), "Important Changes", "Memory POW count")}
    </section>
    <section class="panel">
      <div class="section-head"><h2>Atlas Inbox Action Items</h2><span class="subtle">Suggested operator actions</span></div>
      <div class="inbox-list">{actions}</div>
    </section>
    """
    return _page("Atlas Morning Brief", content, active="/")


def _daily_intelligence_panel(brief: dict | None) -> str:
    if not brief:
        return """
        <section class="panel">
          <div class="section-head"><h2>Daily Intelligence Brief</h2><span class="subtle">No daily cycle yet</span></div>
          <p class="empty">Run <a href="/agents">Agent Cycle</a> or use atlas daily to create the first Daily Intelligence Brief.</p>
        </section>
        """
    priorities = brief.get("research_priorities", ())[:5]
    updates = brief.get("agent_updates", ())[:6]
    actions = brief.get("operator_actions", ())[:5]
    return f"""
    <section class="panel daily-brief">
      <div class="section-head">
        <h2>Daily Intelligence Brief</h2>
        <span class="subtle">{_safe(brief.get("created_at", ""))} / cycle {_safe(brief.get("cycle_id", ""))}</span>
      </div>
      <section class="panel inner-panel">
        <h2>Executive Summary</h2>
        <p>{_safe(brief.get("executive_summary", ""))}</p>
      </section>
      <section class="watchlist-grid">
        <article>
          <h3>What Changed</h3>
          <ul>{''.join(f"<li>{_safe(item)}</li>" for item in brief.get("what_changed", ())[:6])}</ul>
        </article>
        <article>
          <h3>Operator Actions</h3>
          <ul>{''.join(f"<li><strong>{_safe(action.get('severity', 'info'))}</strong> {_safe(action.get('title', ''))}</li>" for action in actions) or "<li>No urgent local action items.</li>"}</ul>
        </article>
      </section>
      <section class="panel inner-panel">
        <div class="section-head"><h2>Today's Research Priorities</h2><span class="subtle">Max 5</span></div>
        <div class="inbox-list">{''.join(_daily_priority_card(priority) for priority in priorities) or "<p class='empty'>No current research priorities.</p>"}</div>
      </section>
      <section class="panel inner-panel">
        <div class="section-head"><h2>Agent Updates</h2><span class="subtle">Why the brief exists</span></div>
        <div class="agent-grid">{''.join(_daily_agent_update_card(update) for update in updates)}</div>
      </section>
    </section>
    """


def _daily_priority_card(priority: dict) -> str:
    return f"""
    <article class="inbox-card">
      <div><strong>{_safe(priority.get("ticker", ""))}</strong><span>{_safe(priority.get("priority", ""))}</span></div>
      <p>Rank {_safe(priority.get("rank", ""))} / change {_safe(str(priority.get("rank_change", 0)))} / score {_safe(priority.get("score", ""))} / confidence {_safe(priority.get("confidence", ""))} / evidence {_safe(priority.get("evidence", ""))}</p>
      <p>{_safe(priority.get("thesis", ""))}</p>
      <p class="subtle">Risk: {_safe(priority.get("risk", ""))}</p>
      <a class="button secondary" href="{_safe(priority.get("link", "/greenrock/market-pulse"))}">Open</a>
    </article>
    """


def _daily_agent_update_card(update: dict) -> str:
    return f"""
    <article class="agent-card { _safe(update.get("status", "completed")) }">
      <h2>{_safe(update.get("agent_name", ""))}</h2>
      <span class="badge">{_safe(update.get("severity", "info"))}</span>
      <p><strong>{_safe(update.get("headline", ""))}</strong></p>
      <p>{_safe(update.get("summary", ""))}</p>
      <p class="subtle">Reason: {_safe(update.get("recommended_operator_action", ""))}</p>
    </article>
    """


def _future_integrations_panel(compact: bool = False) -> str:
    if compact:
        return """
        <article class="wall-panel future-integrations">
          <h2>Future Integrations</h2>
          <p><strong>Slack:</strong> planned / not configured</p>
          <p><strong>Email:</strong> disabled</p>
          <p><strong>Publishing:</strong> disabled</p>
          <p><strong>Trading:</strong> disabled</p>
        </article>
        """
    return """
    <section class="panel">
      <div class="section-head"><h2>Future Integrations</h2><span class="subtle">Local-only placeholders</span></div>
      <section class="board-meta">
        <article class="attention-card neutral"><strong>planned / not configured</strong><span>Slack</span><p>No Slack token, app, webhook, or API action exists yet.</p></article>
        <article class="attention-card neutral"><strong>disabled</strong><span>Email</span><p>No send action is available.</p></article>
        <article class="attention-card neutral"><strong>disabled</strong><span>Publishing</span><p>No publish action is available.</p></article>
        <article class="attention-card neutral"><strong>disabled</strong><span>Trading</span><p>No broker/API order action is available.</p></article>
      </section>
    </section>
    """


def _report_readiness_block(readiness: dict) -> str:
    settings = get_settings()
    task_count = len(report_workbench_summary(settings.output_dir, settings.db_path, create_tasks=False)["tasks"])
    return f"""
    <section class="panel">
      <div class="section-head">
        <h2>GreenRock Report Readiness</h2>
        <span class="subtle">Approval-gated production workflow</span>
      </div>
      <section class="board-meta">
        {_attention_card(_readiness_color(readiness["state"]), readiness["state"], "Readiness State", readiness["next_operator_action"])}
        {_attention_card("neutral", str(task_count), "Report Tasks", "local agent task records")}
        {_attention_card("neutral", _safe(readiness.get("latest_report_run_id") or "none"), "Latest Report Run", readiness.get("latest_report_status", "none"))}
        {_attention_card("yellow" if readiness.get("pending_approvals") else "neutral", str(readiness.get("pending_approvals", 0)), "Pending Approval", str(readiness.get("pending_approval_id") or "none"))}
        {_attention_card("green" if readiness.get("final_pdf_complete") else ("yellow" if readiness.get("approved_pdf_ready") else "neutral"), readiness.get("pdf_status", "not_ready"), "PDF Status", "gated export")}
      </section>
      <p><a class="button secondary" href="/greenrock/report-workbench">Open Report Workbench</a></p>
    </section>
    """


def render_morning_brief_history(status_message: str | None = None) -> str:
    context = _load_context()
    snapshots = list_morning_brief_snapshots(get_settings().output_dir)
    rows = "".join(_morning_brief_snapshot_row(snapshot) for snapshot in snapshots)
    if not rows:
        rows = "<tr><td colspan='6' class='empty'>No Morning Brief snapshots saved yet.</td></tr>"
    content = f"""
    {_status_banner(status_message)}
    {_branded_title_hero("Morning Brief History", "Atlas OS Command Center", "Local operating log of saved Morning Brief snapshots.", context)}
    <section class="panel">
      <div class="section-head">
        <h2>Saved Snapshots</h2>
        <span class="subtle">{len(snapshots)} local snapshot(s)</span>
      </div>
      <table>
        <thead><tr><th>Timestamp</th><th>Scan ID</th><th>Scored</th><th>Top Mover</th><th>Pending</th><th>Open</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """
    return _page("Morning Brief History", content, active="/")


def render_morning_brief_snapshot(snapshot_id: str, status_message: str | None = None) -> str:
    context = _load_context()
    snapshot = load_morning_brief_snapshot(get_settings().output_dir, snapshot_id)
    mover_blocks = "".join(
        _snapshot_mover_block(label, snapshot.get("top_movers", {}).get(key, ()))
        for key, label in (
            ("rank_improvers", "Rank Improvers"),
            ("score_improvers", "Score Improvers"),
            ("confidence_improvers", "Confidence Improvers"),
            ("evidence_improvers", "Evidence Improvers"),
            ("deteriorations", "Deteriorations"),
        )
    )
    leaders = "".join(f"<li>{_safe(item)}</li>" for item in snapshot.get("new_archetype_leaders", ())) or "<li>No new archetype leaders captured.</li>"
    actions = "".join(f"<li>{_safe(item)}</li>" for item in snapshot.get("suggested_actions", ())) or "<li>No suggested actions captured.</li>"
    content = f"""
    {_status_banner(status_message)}
    {_branded_title_hero("Morning Brief Snapshot", "Atlas OS Command Center", "Saved local operating-log entry.", context)}
    <section class="board-meta">
      {_attention_card("neutral", snapshot.get("timestamp", ""), "Timestamp", snapshot.get("snapshot_id", ""))}
      {_attention_card("neutral", snapshot.get("latest_scan_id", "none"), "Latest Scan ID", "Saved state")}
      {_attention_card("green", str(snapshot.get("scored_count", 0)), "Scored Count", f"configured {snapshot.get('configured_count', 0)}")}
      {_attention_card("red" if snapshot.get("pending_approvals", 0) else "neutral", str(snapshot.get("pending_approvals", 0)), "Pending Approvals", "Saved approval count")}
    </section>
    <section class="board-meta">
      {_attention_card("yellow", str(snapshot.get("skipped_count", 0)), "Skipped", "Latest scan")}
      {_attention_card("yellow", str(snapshot.get("provider_failures", 0)), "Provider Failures", "Latest scan")}
      {_attention_card("yellow" if snapshot.get("pdf_ready", 0) else "neutral", str(snapshot.get("pdf_ready", 0)), "PDFs Ready", "Approved, not exported")}
      {_attention_card("green" if snapshot.get("pdf_exported", 0) else "neutral", str(snapshot.get("pdf_exported", 0)), "PDFs Exported", "Final local PDFs")}
    </section>
    <section class="panel">
      <div class="section-head"><h2>Top Movers</h2><span class="subtle">Captured from Atlas Memory</span></div>
      <div class="watchlist-grid">{mover_blocks}</div>
    </section>
    <section class="panel"><h2>New Archetype Leaders</h2><ul class="compact-list">{leaders}</ul></section>
    <section class="panel"><h2>Suggested Operator Actions</h2><ul class="compact-list">{actions}</ul></section>
    <section class="panel disclosure-panel"><h2>Snapshot Boundary</h2><p>This saved snapshot is local research context only. It created no email, publication, trading action, client file, or PDF export.</p></section>
    """
    return _page("Morning Brief Snapshot", content, active="/")


def render_projects(status_message: str | None = None) -> str:
    return render_pt(status_message)


def render_pt(status_message: str | None = None, filters: dict[str, str] | None = None) -> str:
    context = _load_context()
    filters = filters or {}
    projects = context["projects"]
    tasks = context["tasks"]
    selected_project = _parse_int(filters.get("project", "")) if filters.get("project") else None
    selected_status = filters.get("status", "")
    visible_tasks = tuple(
        task
        for task in tasks
        if (selected_project is None or task.project_id == selected_project)
        and (not selected_status or task.status == selected_status)
    )
    task_summary = _pt_task_summary(tasks)
    project_options = "".join(
        f"<option value='{project.id}' {'selected' if project.id == selected_project else ''}>{_safe(project.name)}</option>"
        for project in projects
    )
    project_cards = "".join(_pt_project_card(project, tasks) for project in projects)
    legacy_project_cards = "".join(_legacy_project_card(item, context) for item in PROJECTS)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">PT</p>
      <h1>Projects & Tasks</h1>
      <p>Manual Operator Queue plus Project Directory in one local operating surface. Default project: {_safe(DEFAULT_PROJECT_NAME)}.</p>
    </section>
    <section class="board-meta">
      {_attention_card("yellow" if task_summary["open"] else "neutral", str(task_summary["open"]), "Open Tasks", "pending, in progress, review")}
      {_attention_card("red" if task_summary["blocked"] else "neutral", str(task_summary["blocked"]), "Blocked Tasks", "manual blocked state")}
      {_attention_card("green", str(task_summary["completed"]), "Completed", "done")}
      {_attention_card("neutral", str(len(projects)), "Projects", "local project records")}
    </section>
    <section class="panel">
      <div class="section-head"><h2>Create Task</h2><span class="subtle">Every task is associated with a project</span></div>
      <form method="post" action="/pt/tasks" class="task-form">
        <input name="name" required placeholder="Task title">
        <select name="project_id">{project_options}</select>
        <select name="division">
          <option value="atlas-core">Atlas Core</option>
          <option value="greenrock">GreenRock</option>
          <option value="variance-capital">Variance Capital / The Bat Signal</option>
          <option value="greenrock-insurance">GreenRock Insurance</option>
        </select>
        <textarea name="notes" placeholder="Notes"></textarea>
        <button type="submit">Create</button>
      </form>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Task Filters</h2><span class="subtle">Project, status, and priority-ready structure</span></div>
      <form method="get" action="/pt" class="inline-form">
        <select name="project"><option value="">All Projects</option>{project_options}</select>
        <select name="status">
          <option value="">All Statuses</option>
          {''.join(f"<option value='{status}' {'selected' if status == selected_status else ''}>{_safe(_task_status_label(status))}</option>" for status in TASK_STATUSES)}
        </select>
        <button type="submit">Filter</button>
        <a class="button secondary" href="/pt">Clear</a>
      </form>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Task List</h2><span class="subtle">{len(visible_tasks)} visible</span></div>
      <section class="kanban">{''.join(_task_column(visible_tasks, status, title) for status, title in (("pending", "Backlog"), ("in_progress", "In Progress"), ("awaiting_review", "Awaiting Review"), ("done", "Completed")))}</section>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Create Project</h2><span class="subtle">Future division groups can use the same model</span></div>
      <form method="post" action="/pt/projects" class="task-form">
        <input name="name" required placeholder="Project name">
        <select name="division">
          <option value="atlas-core">Atlas Core</option>
          <option value="greenrock">GreenRock</option>
          <option value="variance-capital">Variance Capital / The Bat Signal</option>
          <option value="greenrock-insurance">GreenRock Insurance</option>
        </select>
        <select name="status">{''.join(f"<option value='{stage}'>{_safe(_project_stage_label(stage))}</option>" for stage in PROJECT_STAGES)}</select>
        <button type="submit">Create Project</button>
      </form>
    </section>
    <section class="project-grid">{project_cards}</section>
    <section class="panel">
      <div class="section-head"><h2>Project Directory</h2><span class="subtle">Division shortcuts remain available</span></div>
      <section class="project-grid">{legacy_project_cards}</section>
    </section>
    """
    return _page("Projects & Tasks", content, active="/pt")


def render_greenrock(status_message: str | None = None) -> str:
    context = _load_context()
    latest_run = context["latest_run"]
    latest_report = context["latest_report"]
    latest_pdf = context["latest_pdf"]
    universes = context["ticker_universes"]
    latest_source = _latest_report_data_source(latest_report)
    approvals = [
        approval
        for approval in context["approvals"]
        if approval.run_id and approval.run_id.startswith("greenrock-")
    ]
    latest_run_approvals = [
        approval
        for approval in approvals
        if latest_run and approval.run_id == latest_run.run_id
    ]
    latest_approval = latest_run_approvals[0] if latest_run_approvals else None
    artifacts = list_artifacts_for_run(context["connection"], latest_run.run_id) if latest_run else ()
    large_candidates = _candidate_rows(latest_run.output_paths.get("large_cap") if latest_run else None)
    small_candidates = _candidate_rows(latest_run.output_paths.get("small_cap") if latest_run else None)

    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      {_greenrock_brand_block()}
      <p class="eyebrow">GreenRock Analysts</p>
      <h1>Report Review Console</h1>
      <p>Discovery Scan, Review Results, Stage Candidates, Generate Draft Report, Human Approval, Export PDF.</p>
    </section>
    <section class="attention-grid">
      {_attention_card("neutral", _safe(latest_run.status if latest_run else "none"), "Latest Report Status", _safe(latest_run.run_id if latest_run else "No run yet"))}
      {_attention_card(_approval_color(latest_approval), _safe(latest_approval.status.value if latest_approval else "none"), "Latest Approval Status", "Human gate remains mandatory")}
      {_attention_card("green" if latest_pdf else "yellow", "exported" if latest_pdf else "not exported", "Latest PDF Status", _safe(latest_pdf.path if latest_pdf else "Approve first, then export locally"))}
      {_attention_card("neutral", _safe(latest_run.data_mode.upper() if latest_run else "NONE"), "Data Mode", _safe(latest_source or "No data source yet"))}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Report Actions</h2>
        <span class="subtle">Local review only. No publish or send controls.</span>
      </div>
      <div class="action-row">
        <form method="post" action="/greenrock/run-report" onsubmit="return confirm('Run a new local MOCK GreenRock report draft?');">
          <input type="hidden" name="data_mode" value="mock">
          <button type="submit">Run Sample/Mock Report</button>
        </form>
        <form method="post" action="/greenrock/run-report" onsubmit="return confirm('Run a new local REAL GreenRock report draft using the configured provider?');">
          <input type="hidden" name="data_mode" value="real">
          <button class="secondary" type="submit">Run Legacy Watchlist Report</button>
        </form>
        <a class="button" href="/greenrock/staging">Generate Draft From Staging</a>
        <a class="button secondary" href="/greenrock/picks">GreenRock Picks Board</a>
        <a class="button secondary" href="/greenrock/discovery">Discovery Workflow</a>
        <a class="button secondary" href="/greenrock/scanner">Market Scanner</a>
        <a class="button secondary" href="/greenrock/watchlists">Watchlists</a>
        <a class="button secondary" href="/greenrock/staging">Report Candidate Staging</a>
        <a class="button secondary" href="/greenrock/score">Score Any Ticker</a>
        {_review_report_action(latest_report)}
        {_path_action(latest_report.content_path if latest_report else None, "View Markdown report")}
        {_path_action(latest_pdf.path if latest_pdf else None, "Open PDF")}
        {_approval_button(latest_approval, "approve", "/greenrock")}
        {_approval_button(latest_approval, "reject", "/greenrock")}
        {_export_pdf_button(latest_approval, latest_pdf)}
        {_final_packet_hint(latest_approval)}
        <a class="button secondary" href="/greenrock/final-reports">Final PDF Archive</a>
      </div>
    </section>
    <section class="candidate-grid">
      <div class="panel">
        <h2>Top Large-Cap Candidates</h2>
        {_candidate_table(large_candidates)}
      </div>
      <div class="panel">
        <h2>Top Small/Mid-Cap Candidates</h2>
        {_candidate_table(small_candidates)}
      </div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>GreenRock Watchlists</h2>
        <span class="subtle">Mega Rock candidate pool, large-cap watchlist, and small/mid-cap watchlist</span>
      </div>
      <p class="subtle">Preferred report path: run a population scan, stage final candidates, then generate an approval-gated draft from staging. Legacy watchlist reports remain available for continuity.</p>
      {_universe_panels(universes)}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Approvals</h2>
        <span class="subtle">Browser actions require a confirmation page</span>
      </div>
      {_approvals_table(approvals[:12], actions=True)}
    </section>
    <section class="panel">
      <h2>Artifacts</h2>
      {_artifacts_table(artifacts)}
    </section>
    """
    return _page("GreenRock Command Center", content, active="/greenrock")


def render_greenrock_report_workbench(status_message: str | None = None) -> str:
    settings = get_settings()
    summary = report_workbench_summary(settings.output_dir, settings.db_path)
    readiness = summary["readiness"]
    tasks = summary["tasks"]
    timeline = summary["timeline"]
    candidate_review = summary["candidate_review"]
    latest_review = f"/greenrock/reports/{quote(readiness['latest_report_run_id'])}/review" if readiness.get("latest_report_run_id") else "/greenrock"
    pending_href = f"/approvals/{readiness['pending_approval_id']}" if readiness.get("pending_approval_id") else "/greenrock"
    task_rows = "".join(_report_task_row(task) for task in tasks) or "<tr><td colspan='6' class='empty'>No report tasks yet.</td></tr>"
    reasons = "".join(f"<li>{_safe(reason)}</li>" for reason in readiness["reasons"]) or "<li>none</li>"
    timeline_cards = "".join(_production_stage_card(stage, tasks) for stage in timeline)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">GreenRock Report Workbench</p>
      <h1>One approval-gated report workflow</h1>
      <p>Local-only production control for scan, Daily Intelligence, Analyst Slate, readiness, approvals, and PDF state.</p>
      <!-- compatibility: href='/greenrock/report-workbench'>Workbench</a> -->
    </section>
    <section class="board-meta">
      {_attention_card(_readiness_color(readiness["state"]), readiness["state"], "Readiness", readiness["next_operator_action"])}
      {_attention_card("green" if readiness["market_pulse_status"] == "available" else "yellow", readiness["latest_scan_id"], "Latest Scan", f"age {readiness['scan_age_hours']}h")}
      {_attention_card("green" if readiness["daily_status"] == "available" else "yellow", readiness["daily_id"], "Daily Intelligence", readiness["daily_status"])}
      {_attention_card("green" if readiness["analytics_complete"] else "yellow", str(readiness["staged_count"]), "Staged Slate", f"missing analytics {readiness['missing_analytics']}")}
      {_attention_card("yellow" if readiness["pending_approvals"] else "neutral", str(readiness["pending_approvals"]), "Pending Approvals", readiness["latest_report_status"])}
      {_attention_card("green" if readiness["final_pdf_complete"] else ("yellow" if readiness["approved_pdf_ready"] else "neutral"), readiness["pdf_status"], "PDF Status", "approval gate intact")}
    </section>
    <section class="panel">
      <div class="section-head"><h2>Next Operator Action</h2><span class="subtle">Agents recommend; operator decides</span></div>
      <div class="primary-action">
        <span class="badge attention">{_safe(readiness["state"])}</span>
        <strong>{_safe(readiness["next_operator_action"])}</strong>
        <ul>{reasons}</ul>
      </div>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Report Production Timeline</h2><span class="subtle">Derived from local records</span></div>
      <section class="workflow-stepper production-timeline">{timeline_cards}</section>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Readiness Clarity</h2><span class="subtle">Specific blockers beat generic status</span></div>
      <section class="detail-grid">
        {_detail_panel("Population Readiness", f"{'ready' if readiness['market_pulse_status'] == 'available' else 'blocked'}; {readiness['latest_scan_id']}")}
        {_detail_panel("Candidate Readiness", f"{readiness['staged_count']} staged; {'underfilled' if 'staging underfilled' in readiness['reasons'] else 'slate available'}")}
        {_detail_panel("Analytics Readiness", f"{'ready' if readiness['analytics_complete'] else 'blocked'}; missing {readiness['missing_analytics']}")}
        {_detail_panel("QA Readiness", f"{'blocked' if readiness['reasons'] else 'ready'}; {', '.join(readiness['reasons']) or 'no blockers'}")}
        {_detail_panel("Approval Readiness", f"{'awaiting review' if readiness['pending_approvals'] else 'waiting'}; {readiness.get('pending_approval_id') or 'none'}")}
        {_detail_panel("PDF Readiness", f"{readiness['pdf_status']}; export blocked before approval")}
      </section>
    </section>
    <section class="panel command-actions">
      <div class="section-head"><h2>Workbench Controls</h2><span class="subtle">Existing gates preserved</span></div>
      <div class="action-row">
        {_workbench_action_button("daily", "Run Daily Intelligence Cycle", "Run Daily Intelligence using latest scan by default?")}
        {_workbench_action_button("stage_slate", "Stage Analyst Slate", "Stage the Analyst Slate from latest Market Pulse? This only updates local staging.")}
        {_workbench_action_button("enrich", "Enrich Staged Candidates", "Refresh staged candidate analytics locally?")}
        <a class="button" href="/greenrock/staging/generate/confirm">Generate Draft From Staging</a>
        <a class="button secondary" href="{_safe(latest_review)}">Open Latest Review Center</a>
        <a class="button secondary" href="{_safe(pending_href)}">Review Pending Approvals</a>
        {_workbench_export_button(readiness)}
        <a class="button secondary" href="/greenrock/final-reports">Open Final Reports</a>
      </div>
      <p class="subtle">Buttons may prepare local staging or drafts, but approvals and PDF export remain explicit gated actions.</p>
    </section>
    <section class="panel" id="candidate-review">
      <div class="section-head"><h2>Candidate Review</h2><span class="subtle">Human Intelligence Layer; scores and ranks unchanged</span></div>
      {_candidate_review_panel(candidate_review, readiness)}
    </section>
    <section class="panel">
      <div class="section-head"><h2>Agent Recommendations</h2><span class="subtle">Local task chain</span></div>
      <table>
        <thead><tr><th>Agent</th><th>Task</th><th>Status</th><th>Output</th><th>Action</th><th>Updated</th></tr></thead>
        <tbody>{task_rows}</tbody>
      </table>
    </section>
    """
    return _page("GreenRock Report Workbench", content, active="/greenrock/report-workbench")


def _production_stage_card(stage: dict, tasks: list[dict]) -> str:
    task = next((item for item in tasks if item.get("agent_id") == _agent_id_for_label(stage.get("agent", ""))), None)
    status = stage.get("status", "Waiting")
    return f"""
    <article class="workflow-card { _safe(status.lower().replace(' ', '-')) }">
      <span class="badge { _safe(status.lower().replace(' ', '-')) }">{_safe(status)}</span>
      <h3>{_safe(stage.get("name", ""))}</h3>
      <p><strong>Agent:</strong> {_safe(stage.get("agent", ""))}</p>
      <p><strong>Source:</strong> {_safe(stage.get("source", ""))}</p>
      <p><strong>Timestamp:</strong> {_safe(stage.get("timestamp", "") or (task or {}).get("updated_at", ""))}</p>
      <p><strong>Blocking reason:</strong> {_safe(stage.get("blocking_reason", "") or "none")}</p>
      <p><strong>Next:</strong> <a href="{_safe(stage.get("target_url", "/greenrock/report-workbench"))}">{_safe(stage.get("next_action", ""))}</a></p>
      <p class="subtle">Latest task: {_safe((task or {}).get("status", "none"))} {_safe((task or {}).get("output_summary", ""))}</p>
    </article>
    """


def _agent_id_for_label(label: str) -> str:
    return label.lower().replace(" agent", "").replace(" ", "_")


def _candidate_review_panel(review: dict, readiness: dict) -> str:
    featured = "".join(_featured_candidate_card(item, readiness) for item in review["featured"])
    remaining = "".join(_remaining_candidate_row(item, readiness) for item in review["remaining"])
    if not remaining:
        remaining = "<tr><td colspan='9' class='empty'>No remaining staged candidates beyond featured archetype leaders.</td></tr>"
    return f"""
    <section class="panel inner-panel">
      <h3>Candidate Decision Semantics</h3>
      <ul class="compact-list">
        <li><strong>Accepted:</strong> accepted into the current report slate, still subject to analytics readiness, QA, and approval.</li>
        <li><strong>Research Needed:</strong> queues a local research task and keeps the candidate pending editorial decision.</li>
        <li><strong>Deferred:</strong> excludes from the current report cycle but preserves eligibility for later scans.</li>
        <li><strong>Excluded:</strong> excludes from the current report cycle only; it does not remove Universe, Scanner, Memory, or future rankings.</li>
      </ul>
    </section>
    <h3>Featured Archetype Leaders</h3>
    <section class="candidate-grid featured-leaders">{featured}</section>
    <h3>Remaining Report Slate</h3>
    <table>
      <thead><tr><th>Ticker</th><th>Archetype</th><th>Rank</th><th>Score</th><th>Confidence</th><th>Evidence</th><th>Priority</th><th>Guardrail</th><th>Decision</th></tr></thead>
      <tbody>{remaining}</tbody>
    </table>
    <p class="subtle">Candidate decisions are local operator notes. They do not alter GreenRock Score, canonical rank, staging, or report generation.</p>
    """


def _featured_candidate_card(item: dict, readiness: dict) -> str:
    if item.get("status") == "missing":
        return f"<article class='candidate-card muted'><h3>{_safe(item.get('archetype', ''))}</h3><p>No staged leader available.</p></article>"
    return f"""
    <article class="candidate-card">
      <div class="section-head"><h3>{_safe(item['archetype'])}: {_safe(item['ticker'])}</h3><span class="badge signal">{_safe(item.get('decision') or 'undecided')}</span></div>
      <p><strong>Rank:</strong> {_safe(item['rank'])} / <strong>Score:</strong> {_safe(item['score'])} / <strong>Confidence:</strong> {_safe(item['confidence'])} / <strong>Evidence:</strong> {_safe(item['evidence_agreement'])}</p>
      <p><strong>Movement:</strong> rank {_safe(item['rank_movement'])}; score {_safe(item['score_movement'])}; confidence {_safe(item['confidence_movement'])}</p>
      <p><strong>Bullish:</strong> {_safe(item['primary_bullish_evidence'])}</p>
      <p><strong>Caution:</strong> {_safe(item['primary_caution'])}</p>
      <p><strong>Guardrail:</strong> {_safe(item['guardrail'])} / <strong>Priority:</strong> {_safe(item['research_priority'])} / <strong>Staging:</strong> {_safe(item['staging_status'])}</p>
      {_candidate_decision_form(item['ticker'], readiness)}
    </article>
    """


def _remaining_candidate_row(item: dict, readiness: dict) -> str:
    return f"""
    <tr>
      <td>{_safe(item['ticker'])}</td>
      <td>{_safe(item['archetype'])}</td>
      <td>{_safe(item['rank'])}</td>
      <td>{_safe(item['score'])}</td>
      <td>{_safe(item['confidence'])}</td>
      <td>{_safe(item['evidence_agreement'])}</td>
      <td>{_safe(item['research_priority'])}</td>
      <td>{_safe(item['guardrail'])}</td>
      <td>{_safe(item.get('decision') or 'undecided')}{_candidate_decision_form(item['ticker'], readiness, compact=True)}</td>
    </tr>
    """


def _candidate_decision_form(ticker: str, readiness: dict, compact: bool = False) -> str:
    labels = {"accepted": "Accepted", "research": "Research Needed", "deferred": "Deferred", "excluded": "Excluded"}
    options = "".join(f"<option value='{decision}'>{labels.get(decision, decision.title())}</option>" for decision in CANDIDATE_DECISIONS)
    note_input = "" if compact else "<input type='text' name='note' placeholder='optional note'>"
    return f"""
    <form method="post" action="/greenrock/report-workbench/candidate-decision" class="candidate-decision-form">
      <input type="hidden" name="ticker" value="{_safe(ticker)}">
      <input type="hidden" name="related_scan_id" value="{_safe(readiness.get('latest_scan_id') or '')}">
      <input type="hidden" name="related_daily_id" value="{_safe(readiness.get('daily_id') or '')}">
      <input type="hidden" name="related_report_run_id" value="{_safe(readiness.get('latest_report_run_id') or '')}">
      <select name="decision">{options}</select>
      {note_input}
      <button type="submit">{'Save' if compact else 'Save Decision'}</button>
    </form>
    """


def _report_task_row(task: dict) -> str:
    return f"""
    <tr>
      <td>{_safe(task.get("agent_id", ""))}</td>
      <td>{_safe(task.get("title", ""))}</td>
      <td><span class="badge { _safe(task.get("status", "")) }">{_safe(task.get("status", ""))}</span></td>
      <td>{_safe(task.get("output_summary", ""))}</td>
      <td>{_safe(task.get("operator_action_required", ""))}</td>
      <td>{_safe(task.get("updated_at", ""))}</td>
    </tr>
    """


def _workbench_action_button(action: str, label: str, confirm: str) -> str:
    return f"""
    <form method="post" action="/greenrock/report-workbench/action" class="inline-form" onsubmit="return confirm('{_safe(confirm)}');">
      <input type="hidden" name="action" value="{_safe(action)}">
      <button type="submit">{_safe(label)}</button>
    </form>
    """


def _workbench_export_button(readiness: dict) -> str:
    if readiness.get("approved_pdf_ready"):
        return _workbench_action_button("export_pdf", "Export Approved PDF", "Export approved PDF locally? Approval must already be approved.")
    return '<span class="button disabled">Export Approved PDF: approval required</span>'


def _readiness_color(state: str) -> str:
    if state in {"Ready to Draft", "Final PDF Complete"}:
        return "green"
    if state in {"Needs Review", "Draft Awaiting Approval", "Approved, PDF Ready"}:
        return "yellow"
    return "red"


def render_greenrock_picks_board(status_message: str | None = None) -> str:
    context = _load_context()
    latest_run = context["latest_run"]
    latest_report = context["latest_report"]
    latest_source = _latest_report_data_source(latest_report)
    approvals = [approval for approval in context["approvals"] if latest_run and approval.run_id == latest_run.run_id]
    approval_status = approvals[0].status.value if approvals else "none"
    all_candidates = _candidate_rows(latest_run.output_paths.get("all") if latest_run else None, limit=None)
    mega_candidates = _candidate_rows(latest_run.output_paths.get("mega_rock") if latest_run else None, limit=1)
    large_candidates = _candidate_rows(latest_run.output_paths.get("large_cap") if latest_run else None, limit=11)
    small_candidates = _candidate_rows(latest_run.output_paths.get("small_cap") if latest_run else None, limit=11)
    mega_pick = _top_candidate(mega_candidates) or _top_candidate(all_candidates)
    slot_count = (1 if mega_pick else 0) + len(large_candidates) + len(small_candidates)
    warnings = _picks_board_warnings(mega_pick, large_candidates, small_candidates)
    data_mode = latest_run.data_mode.upper() if latest_run else "NONE"

    content = f"""
    {_status_banner(status_message)}
    <section class="hero picks-hero">
      <div>
        {_greenrock_brand_block()}
        <p class="eyebrow">GreenRock Analysts</p>
        <h1>Picks Board</h1>
        <p>Local research dashboard for the latest approval-gated report run.</p>
      </div>
      <div class="picks-stamp">
        <span class="badge data-mode">{_safe(data_mode)} DATA</span>
        <strong>{slot_count}/23</strong>
        <p>visible report slots</p>
      </div>
    </section>
    <section class="panel calculator-card">
      <div>
        <p class="eyebrow">Score Preview</p>
        <h2>GreenRock Score Calculator</h2>
        <p class="subtle">Score any ticker locally without creating a report, approval, or artifact.</p>
      </div>
      <a class="button" href="/greenrock/score">GreenRock Score Calculator</a>
      <a class="button secondary" href="/greenrock/discovery">Discovery Workflow</a>
    </section>
    <section class="board-meta">
      {_attention_card("neutral", _safe(latest_run.run_id if latest_run else "none"), "Latest Run", _safe(latest_source or "No data source yet"))}
      {_attention_card(_approval_color(approvals[0] if approvals else None), _safe(approval_status), "Approval Status", "Human gate remains mandatory")}
      {_attention_card("green" if latest_report else "yellow", _safe(data_mode), "Data Mode", "Mock vs real is explicitly labeled")}
      {_attention_card("neutral", f"{slot_count}/23", "Selected Pick Count", "Mega 1, large 11, small/mid 11")}
    </section>
    <section class="board-meta">
      {_attention_card("neutral", "Configured", "Source Universe / Watchlist", "Mega Rock pool, large-cap watchlist, small/mid watchlist")}
      {_attention_card("green", "$1T+", "Mega Rock Eligibility", "Market cap must be at least $1T")}
      {_attention_card("neutral", "Planned", "Full-Market Scanner", "Current real mode ranks configured watchlists")}
      {_attention_card("neutral", _safe(latest_source or "none"), "Data Source", "Provider label from latest report")}
    </section>
    {_picks_warning_panel(warnings)}
    <section class="mega-pick">
      <div class="section-head">
        <h2>Mega Rock Pick</h2>
        <span class="subtle">Mega Rock: {1 if mega_pick else 0}/1</span>
      </div>
      {_mega_pick_card(mega_pick)}
    </section>
    <section class="panel picks-panel">
      <div class="section-head">
        <h2>Large-Cap Picks</h2>
        <span class="subtle">Large Cap: {len(large_candidates)}/11</span>
      </div>
      {_picks_table(large_candidates)}
    </section>
    <section class="panel picks-panel">
      <div class="section-head">
        <h2>Small/Mid-Cap Picks</h2>
        <span class="subtle">Small/Mid: {len(small_candidates)}/11</span>
      </div>
      {_picks_table(small_candidates)}
    </section>
    <section class="panel disclosure-panel">
      <h2>Research Controls</h2>
      <p>This board is local-only, approval-gated, and not published externally. It is not a personalized recommendation or guarantee of future results.</p>
      <p class="subtle">Powered by Atlas OS</p>
    </section>
    """
    return _page("GreenRock Picks Board", content, active="/greenrock/picks")


def render_greenrock_discovery(status_message: str | None = None) -> str:
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero discovery-hero">
      {_greenrock_brand_block()}
      <p class="eyebrow">GreenRock Discovery Migration</p>
      <h1>GreenRock Discovery Workflow</h1>
      <p>Discovery moved into Scanner. The mature flow now operates inside Scanner, then continues through Market Pulse, Staging, Report Workbench, human approval, and final PDF controls.</p>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>GreenRock Discovery Flow</h2>
        <span class="subtle">Route preserved for compatibility</span>
      </div>
      <section class="workflow-stepper">
        <span>Discovery Scan</span>
        <span>Review Results</span>
        <span>Stage Candidates</span>
        <span>Generate Draft Report</span>
        <span>Human Approval</span>
        <span>Export PDF</span>
      </section>
      <div class="action-row">
        <a class="button" href="/greenrock/scanner">Open Scanner</a>
        <a class="button secondary" href="/greenrock/watchlists">View Watchlists</a>
        <a class="button secondary" href="/greenrock/staging">Report Candidate Staging</a>
        <a class="button secondary" href="/greenrock/picks">Picks Board</a>
        <a class="button secondary" href="/greenrock/score">Score Calculator</a>
      </div>
      <p class="subtle">No functionality was deleted. Discovery is no longer a primary navigation item because Scanner is the canonical discovery surface.</p>
    </section>
    """
    return _page("GreenRock Discovery Migration", content, active="/greenrock/scanner")


def render_greenrock_scanner(status_message: str | None = None, query: dict[str, list[str]] | None = None) -> str:
    settings = get_settings()
    scan = latest_scan(settings.output_dir)
    filters = _scan_filter_values(query or {})
    filtered_rows = _filter_scan_rows(scan.rows, filters) if scan else ()
    top_rows = filtered_rows[:25] if scan else ()
    population_buttons = "".join(
        f"""
        <form method="post" action="/greenrock/scanner/run">
          <input type="hidden" name="population" value="{_safe(name)}">
          <button type="submit">{_safe(label)}</button>
        </form>
        """
        for name, label in {**GREENROCK_POPULATION_LABELS, "all": "Master Universe"}.items()
    )
    latest = ""
    if scan:
        metadata = _scan_metadata(scan)
        latest = f"""
        <section class="board-meta">
          {_attention_card("neutral", _safe(scan.population), "Population Scanned", _safe(scan.scan_id))}
          {_attention_card("neutral", str(scan.configured_ticker_count), "Configured Tickers", "Before provider fetch")}
          {_attention_card("green", str(scan.fetched_ticker_count), "Fetched / Scored", _safe(scan.data_source))}
          {_attention_card("yellow", str(scan.skipped_ticker_count), "Skipped Tickers", "No usable provider data")}
        </section>
        <section class="board-meta">
          {_attention_card("yellow", str(scan.provider_failure_count), "Provider Failures", "Provider errors")}
          {_attention_card("neutral", str(scan.duplicates_removed), "Duplicates Removed", "Universe merge")}
          {_attention_card("green", str(len(scan.rows)), "Ranked Count", "Ranking Engine output")}
          {_attention_card("neutral", _safe(metadata["timestamp"]), "Scan Timestamp", "UTC")}
        </section>
        <section class="panel scanner-filter-panel">
          <div class="section-head">
            <h2>Quick Filters</h2>
            <span class="subtle">Narrow the discovery set before promotion.</span>
          </div>
          {_scan_filter_form(filters)}
        </section>
        <section class="panel picks-panel">
          <div class="section-head">
            <h2>Top Ranked Tickers</h2>
            <span class="subtle">Promoted tickers are now available for future GreenRock report generation and score review.</span>
          </div>
          {_scan_results_table(top_rows, scan.scan_id, batch=True)}
        </section>
        <section class="panel">
          <h2>Local Outputs</h2>
          <p class="path">{_safe(scan.results_path)}</p>
          <p class="path">{_safe(scan.summary_path)}</p>
        </section>
        """
    else:
        latest = """
        <section class="panel warning-panel">
          <h2>No Population Scan Yet</h2>
          <p>Run a local scan with one of the population buttons. Real provider setup is required.</p>
        </section>
        """
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">GreenRock Analysts</p>
      <h1>Market Scanner</h1>
      <p>Scanner = discovery engine. Review broad populations, then stage selected names into the final report slate or save them to local research queues.</p>
      <p><a class="button secondary" href="/greenrock/universe">Universe Manager</a> <a class="button secondary" href="/greenrock/market-pulse">Market Pulse</a> <a class="button secondary" href="/greenrock/report-workbench">Report Workbench</a></p>
    </section>
    <section class="panel discovery-flow-panel">
      <div class="section-head">
        <h2>GreenRock Discovery Flow</h2>
        <span class="subtle">Canonical research-to-report path</span>
      </div>
      <div class="workflow-stepper" aria-label="GreenRock discovery workflow">
        <span>Universe</span><span>Scan</span><span>Rank</span><span>Market Pulse</span><span>Human Review</span><span>Staging</span><span>Report Workbench</span><span>Approval</span><span>Final Report</span>
      </div>
      <p class="subtle">Scanner discovers and ranks. It does not directly generate client reports; staging and the approval-gated Workbench remain the report path.</p>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Run Population Scan</h2>
        <span class="subtle">Real provider required. Local output only.</span>
      </div>
      <div class="action-row">{population_buttons}</div>
      <p class="subtle">Primary workflow: Discovery Scan, Review Results, Stage Candidates, Generate Draft Report, Human Approval, Export PDF.</p>
    </section>
    {latest}
    <section class="panel">
      <h2>Data Quality Notes</h2>
      <p class="subtle">If the real provider is unavailable, scans fail safely with setup instructions and create no report, approval, email, or publication.</p>
    </section>
    """
    return _page("GreenRock Market Scanner", content, active="/greenrock/scanner")


def render_greenrock_universe(status_message: str | None = None, query: dict[str, str] | None = None) -> str:
    settings = get_settings()
    master = default_universe_manager(settings.output_dir).master_universe()
    filters = query or {}
    provider_cards = "".join(_universe_provider_card(provider) for provider in master.providers)
    bucket_counts: dict[str, int] = {}
    archetype_counts: dict[str, int] = {}
    for row in master.rows:
        bucket_counts[row.market_cap_bucket] = bucket_counts.get(row.market_cap_bucket, 0) + 1
        archetype_counts[row.market_archetype] = archetype_counts.get(row.market_archetype, 0) + 1
    buckets = "".join(
        _attention_card("neutral", str(count), f"{bucket.replace('_', ' ').title()} Bucket", "Master Universe")
        for bucket, count in sorted(bucket_counts.items())
    )
    archetypes = "".join(
        _attention_card("neutral", str(count), archetype or "Unknown", "Archetype")
        for archetype, count in sorted(archetype_counts.items())
    )
    failures = universe_health_rows(settings.output_dir)
    filtered_rows = _filter_master_universe_rows(master.rows, filters)
    page_size = 100
    page = max(1, _parse_int(filters.get("page", "1")) or 1)
    total_pages = max(1, (len(filtered_rows) + page_size - 1) // page_size)
    page = min(page, total_pages)
    page_rows = filtered_rows[(page - 1) * page_size : page * page_size]
    rows = "".join(_master_universe_row(row) for row in page_rows)
    if not rows:
        rows = "<tr><td colspan='6' class='empty'>No master universe tickers available yet.</td></tr>"
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">Atlas Research Pipeline</p>
      <h1>Universe Manager</h1>
      <p>Universe Manager owns research populations before scanner, ranking, staging, and reports.</p>
      <p><a class="button secondary" href="/greenrock/scanner">Open Scanner</a></p>
    </section>
    <section class="board-meta">
      {_attention_card("green", str(master.size), "Master Universe Size", "Unique tickers")}
      {_attention_card("neutral", str(master.duplicates_removed), "Duplicates Removed", "Provider overlap")}
      {_attention_card("neutral", _safe(master.last_refresh), "Last Refresh", "UTC")}
      {_attention_card("neutral", str(len(master.providers)), "Providers", "Registered sources")}
    </section>
    <section class="board-meta">{buckets}</section>
    <section class="board-meta">{archetypes}</section>
    <section class="panel warning-panel">
      <div class="section-head">
        <h2>Provider Failure Health</h2>
        <span class="badge pending">{len(failures)} provider failures</span>
      </div>
      {_provider_failure_summary(failures)}
    </section>
    <section class="watchlist-grid">{provider_cards}</section>
    <section class="panel picks-panel">
      <div class="section-head">
        <h2>Master Universe</h2>
        <span class="subtle">Showing {len(page_rows)} of {len(filtered_rows)} filtered rows from {master.size} total. Page {page} of {total_pages}.</span>
      </div>
      {_universe_filter_form(filters, master)}
      <table>
        <thead><tr><th>Ticker</th><th>Membership</th><th>Market Cap Bucket</th><th>Archetype</th><th>Sector</th><th>Health</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      {_pagination_links('/greenrock/universe', filters, page, total_pages)}
      <p class="path">{_safe(master.path)}</p>
    </section>
    <section class="panel">
      <h2>Pipeline</h2>
      <p class="subtle">Universe Providers -> Universe Builder -> Master Universe -> Evidence Engine -> Ranking Engine -> Staging -> GreenRock Reports.</p>
    </section>
    """
    return _page("GreenRock Universe", content, active="/greenrock/universe")


def render_greenrock_market_pulse(status_message: str | None = None) -> str:
    settings = get_settings()
    scan = latest_scan(settings.output_dir)
    if scan:
        normalized_rows = tuple(_pulse_row(row) for row in scan.rows)
        failures = load_scan_failures(settings.output_dir, scan.scan_id)
        grouped = {
            archetype: tuple(row for row in normalized_rows if row.get("market_archetype", "") == archetype)
            for archetype in MARKET_ARCHETYPES
        }
        cards = "".join(
            _attention_card("neutral", str(len(rows)), archetype, "Latest ranked scan")
            for archetype, rows in grouped.items()
        )
        sections = "".join(_market_pulse_section(archetype, rows[:8], scan.scan_id) for archetype, rows in grouped.items())
        diagnostics = f"""
        <section class="board-meta">
          {_attention_card("neutral", _safe(scan.scan_id), "Scan ID", "Latest successful scan")}
          {_attention_card("neutral", _safe(scan.population), "Population", "Scan source")}
          {_attention_card("neutral", str(scan.configured_ticker_count), "Configured Tickers", "Before provider fetch")}
          {_attention_card("green", str(scan.fetched_ticker_count), "Fetched / Scored", scan.data_source)}
        </section>
        <section class="board-meta">
          {_attention_card("yellow", str(scan.skipped_ticker_count), "Skipped Tickers", "No usable provider data")}
          {_attention_card("neutral", str(scan.duplicates_removed), "Duplicates Removed", "Universe merge")}
          {_attention_card("green", str(len(scan.rows)), "Ranked Count", "Ranking Engine output")}
          {_attention_card("yellow", str(len(failures)), "Provider Failures", "Open Universe Health for detail")}
        </section>
        <section class="panel warning-panel">
          <div class="section-head"><h2>Provider Failures</h2><span class="badge pending">{len(failures)} provider failures</span></div>
          {_provider_failure_summary(failures)}
        </section>
        """
        actions = _market_pulse_actions(settings.output_dir, scan.scan_id)
        pow_card = _memory_pow_card(settings.output_dir)
        changes = _market_pulse_memory_panel(settings.output_dir)
    else:
        cards = ""
        sections = "<section class='panel warning-panel'><h2>No Latest Scan</h2><p>Run a population scan first. Market Pulse does not create reports or approvals.</p></section>"
        diagnostics = ""
        actions = ""
        pow_card = ""
        changes = ""
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">GreenRock Market Pulse</p>
      <h1>Top Opportunities by Archetype</h1>
      <p>Scanner finds opportunities. Operators stage candidates. Reports still generate only from staging and remain approval-gated.</p>
      <p><a class="button secondary" href="/greenrock/scanner">Run Scanner</a> <a class="button secondary" href="/greenrock/staging">Open Staging</a></p>
    </section>
    {pow_card}
    {diagnostics}
    {changes}
    {actions}
    <section class="board-meta">{cards}</section>
    {sections}
    """
    return _page("GreenRock Market Pulse", content, active="/greenrock/market-pulse")


def render_market_pulse_stage_confirmation(status_message: str | None = None, slate_mode: str = "market_pulse") -> str:
    settings = get_settings()
    scan = latest_scan(settings.output_dir)
    existing = load_staged_candidates(settings.output_dir)
    analyst_mode = slate_mode == "analyst"
    button_label = "Generate Atlas Analyst Report Slate" if analyst_mode else "Stage Top Market Pulse Candidates"
    selector_description = (
        "one leader from each available archetype, then remaining report slate by rank"
        if analyst_mode
        else "top 1 Mega, top 11 Large, and top 11 combined Mid/Small/Micro"
    )
    if scan is None:
        preview = "<p>No successful scan found. Run a population scan first.</p>"
        form = '<a class="button secondary" href="/greenrock/market-pulse">Back</a>'
    else:
        selected = select_analyst_slate_candidates(scan) if analyst_mode else select_market_pulse_candidates(scan)
        preview_rows = "".join(
            "<tr>"
            f"<td>{_safe(row.get('symbol', ''))}</td>"
            f"<td>{_safe(row.get('market_archetype', ''))}</td>"
            f"<td>{_safe(bucket)}</td>"
            f"<td>{_safe(row.get('greenrock_score', ''))}</td>"
            f"<td>{_safe(row.get('research_priority', ''))}</td>"
            "</tr>"
            for row, bucket in selected[:30]
        )
        overwrite = "<input type='hidden' name='overwrite_staging' value='yes'>"
        overwrite_note = (
            f"<p class='warning-text'>Existing staging has {len(existing)} candidate(s). Confirming will replace them with this slate.</p>"
            if existing
            else "<p class='subtle'>Current staging is empty. Confirming will create a new report slate.</p>"
        )
        preview = f"""
        <p>Latest scan: <strong>{_safe(scan.scan_id)}</strong>. Candidate selection: {_safe(selector_description)}.</p>
        {overwrite_note}
        <table>
          <thead><tr><th>Ticker</th><th>Archetype</th><th>Stage Bucket</th><th>Score</th><th>Priority</th></tr></thead>
          <tbody>{preview_rows or "<tr><td colspan='5' class='empty'>No candidates available.</td></tr>"}</tbody>
        </table>
        """
        form = f"""
        <form method="post" action="/greenrock/market-pulse/stage" class="confirm-form">
          {overwrite}
          <input type="hidden" name="slate_mode" value="{'analyst' if analyst_mode else 'market_pulse'}">
          <button type="submit">{button_label}</button>
          <a class="button secondary" href="/greenrock/market-pulse">Cancel</a>
        </form>
        """
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">Confirm Market Pulse Staging</p>
      <h1>{button_label}?</h1>
      <p>This writes local staging only. It creates no report, approval, PDF, email, publication, trading action, or client file.</p>
    </section>
    <section class="panel warning-panel">
      {preview}
      {form}
    </section>
    """
    return _page("Confirm Market Pulse Staging", content, active="/greenrock/market-pulse")


def render_market_pulse_report_confirmation(status_message: str | None = None) -> str:
    settings = get_settings()
    rows = load_staged_candidates(settings.output_dir)
    scan = latest_scan(settings.output_dir)
    staged_count = len(tuple(row for row in rows if not scan or row.get("source_scan_id") == scan.scan_id))
    readiness = staging_report_readiness(settings.output_dir, allow_underfilled=True)
    warnings = "".join(f"<li>{_safe(warning)}</li>" for warning in readiness.warnings) or "<li>No readiness warnings.</li>"
    underfilled = any("underfilled" in warning for warning in readiness.warnings)
    allow_control = (
        """
        <label class="checkbox-line">
          <input type="checkbox" name="allow_underfilled" value="yes">
          Allow underfilled sections and show warnings in the draft
        </label>
        """
        if underfilled
        else "<input type='hidden' name='allow_underfilled' value='yes'>"
    )
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">Confirm Market Pulse Draft</p>
      <h1>Generate Draft From Staged Market Pulse?</h1>
      <p>This creates a normal local workflow run, report artifacts, and a pending approval. It does not publish, email, trade, create client files, or export a PDF.</p>
    </section>
    <section class="panel warning-panel">
      <p>Staged Market Pulse candidates from latest scan: <strong>{staged_count}</strong>.</p>
      <h2>Readiness Review</h2>
      <ul class="compact-list">{warnings}</ul>
      <form method="post" action="/greenrock/market-pulse/report" class="confirm-form">
        {allow_control}
        <button type="submit">Create Approval-Gated Draft</button>
        <a class="button secondary" href="/greenrock/market-pulse">Cancel</a>
      </form>
    </section>
    """
    return _page("Confirm Market Pulse Draft", content, active="/greenrock/market-pulse")


def render_greenrock_watchlists(status_message: str | None = None) -> str:
    settings = get_settings()
    metadata = _promotion_metadata_by_ticker(settings.output_dir)
    cards = "".join(_watchlist_overview_card(settings.output_dir, key, label, metadata) for key, label in GREENROCK_PLACEMENT_LABELS.items())
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">GreenRock Watchlists</p>
      <h1>Curated Research Queues</h1>
      <p>Promoted names live here locally for future score review and approval-gated report consideration.</p>
      <p><a class="button secondary" href="/greenrock/discovery">Discovery Workflow</a></p>
    </section>
    <section class="watchlist-grid">{cards}</section>
    <section class="panel">
      <h2>Report Flow Clarity</h2>
      <p class="subtle">Promotion is local research organization only. It does not generate a report, approval, PDF, email, publication, or client-facing artifact.</p>
    </section>
    """
    return _page("GreenRock Watchlists", content, active="/greenrock/watchlists")


def render_greenrock_staging(status_message: str | None = None) -> str:
    settings = get_settings()
    rows = load_staged_candidates(settings.output_dir)
    readiness = staging_report_readiness(settings.output_dir, allow_underfilled=True)
    analytics = staging_analytics_status(settings.output_dir)
    readiness_cards = "".join(_staging_readiness_card(item) for item in staging_readiness(settings.output_dir))
    generation_status = _staging_generation_status(readiness.warnings)
    bucket_sections = "".join(_staging_bucket_section(bucket, label, rows) for bucket, label in STAGING_BUCKET_LABELS.items())
    source_sections = _staging_source_sections(settings.output_dir)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">GreenRock Report Candidate Staging</p>
      <h1>Final Human Curation Before Reports</h1>
      <p>Stage names into the final report slate. Staging alone creates no reports, approvals, PDFs, emails, or publication artifacts.</p>
      <p><a class="button secondary" href="/greenrock/discovery">Discovery Workflow</a></p>
    </section>
    <section class="board-meta">{readiness_cards}{_staging_analytics_card(analytics)}</section>
    <section class="panel enrichment-panel">
      <div class="section-head">
        <h2>Staging Analytics Enrichment</h2>
        <span class="badge">{_safe(analytics.label)}</span>
      </div>
      {_staging_enrichment_status(analytics)}
      <form method="post" action="/greenrock/staging/enrich" onsubmit="return confirm('Refresh staged candidates with current GreenRock analytics?');">
        <button type="submit">Refresh / Enrich Staged Candidates</button>
      </form>
    </section>
    <section class="panel warning-panel">
      <div class="section-head">
        <h2>Generate Draft From Staging</h2>
        <span class="badge">Approval-gated</span>
      </div>
      {generation_status}
      <p class="subtle">Creates a normal local workflow run, report artifacts, and a pending approval. It does not publish, email, or export a PDF automatically.</p>
      <a class="button" href="/greenrock/staging/generate/confirm">Generate Draft From Staging</a>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Add Candidate Manually</h2>
        <span class="subtle">Metadata will hydrate from latest scan or promotion records when available.</span>
      </div>
      {_staging_add_form()}
    </section>
    {source_sections}
    <section class="staging-grid">{bucket_sections}</section>
    <section class="panel">
      <h2>Report Flow Clarity</h2>
      <p class="subtle">Preferred workflow: Scan, Stage, Generate Draft, Approve, Export PDF. Staging is the final local curation layer before an approval-gated report draft. It is not publication and not a personalized recommendation.</p>
    </section>
    """
    return _page("GreenRock Staging", content, active="/greenrock/staging")


def render_greenrock_staging_generation_confirmation(status_message: str | None = None) -> str:
    settings = get_settings()
    readiness = staging_report_readiness(settings.output_dir, allow_underfilled=True)
    warnings = "".join(f"<li>{_safe(warning)}</li>" for warning in readiness.warnings) or "<li>No readiness warnings.</li>"
    underfilled = any("underfilled" in warning for warning in readiness.warnings)
    allow_control = (
        """
        <label class="checkbox-line">
          <input type="checkbox" name="allow_underfilled" value="yes">
          Allow underfilled sections and show warnings in the draft
        </label>
        """
        if underfilled
        else "<input type='hidden' name='allow_underfilled' value='yes'>"
    )
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      {_greenrock_brand_block()}
      <p class="eyebrow">Confirm Staging Draft</p>
      <h1>Generate Draft From Staging?</h1>
      <p>This creates a local GreenRock workflow run, report artifacts, and a pending approval. It does not publish, email, or export a PDF.</p>
    </section>
    <section class="panel warning-panel">
      <h2>Readiness Review</h2>
      <ul class="compact-list">{warnings}</ul>
      <form method="post" action="/greenrock/staging/generate" class="confirm-form">
        {allow_control}
        <button type="submit">Create Approval-Gated Draft</button>
        <a class="button secondary" href="/greenrock/staging">Cancel</a>
      </form>
    </section>
    """
    return _page("Confirm Staging Draft", content, active="/greenrock/staging")


def run_greenrock_scan_from_browser(population: str) -> str:
    settings = get_settings()
    try:
        result = run_population_scan(settings.output_dir, population)
    except (MarketDataConfigurationError, ValueError) as error:
        return f"Population scan blocked: {error}"
    return f"Population scan complete: {result.scan_id}"


def stage_market_pulse_from_browser(overwrite_staging: bool = False, slate_mode: str = "market_pulse") -> str:
    settings = get_settings()
    try:
        if slate_mode == "analyst":
            result = stage_analyst_slate_candidates(settings.output_dir, overwrite=overwrite_staging)
        else:
            result = stage_top_market_pulse_candidates(settings.output_dir, overwrite=overwrite_staging)
    except ValueError as error:
        return f"Market Pulse staging blocked: {error}"
    counts = {
        "mega": sum(1 for row in result.staged_rows if row.get("staged_bucket") == "mega"),
        "large": sum(1 for row in result.staged_rows if row.get("staged_bucket") == "large"),
        "small_mid": sum(1 for row in result.staged_rows if row.get("staged_bucket") == "small_mid"),
    }
    label = "Atlas Analyst slate" if slate_mode == "analyst" else "Market Pulse"
    status = f"{label} staged {len(result.staged_rows)} candidates from {result.scan_id}: {counts['mega']} mega, {counts['large']} large, {counts['small_mid']} small/mid."
    if result.replaced_existing:
        status += " Existing staging was replaced after confirmation."
    if result.warnings:
        status += " Warnings: " + " ".join(result.warnings)
    status += " No report, approval, PDF, email, publication, trading action, or client file was created."
    return status


def generate_market_pulse_report_from_browser(allow_underfilled: bool = False) -> str:
    return generate_greenrock_staging_report(allow_underfilled=allow_underfilled)


def promote_greenrock_scan_ticker(scan_id: str, ticker: str, list_key: str) -> str:
    settings = get_settings()
    try:
        placement = promote_scan_ticker(settings.output_dir, scan_id, ticker, list_key)
        warnings = " ".join(placement.warnings)
        if placement.blocked:
            status = f"Promotion blocked: {warnings}"
        else:
            verb = "saved to" if placement.added else "already exists in"
            status = f"{placement.ticker} {verb} {placement.list_label}. {warnings}".strip()
    except ValueError as error:
        status = f"Promotion blocked: {error}"
    return render_greenrock_scanner(status)


def promote_greenrock_scan_tickers(scan_id: str, tickers: tuple[str, ...], list_key: str) -> str:
    settings = get_settings()
    if not tickers:
        return render_greenrock_scanner("Promotion blocked: choose at least one ticker.")
    statuses: list[str] = []
    warnings: list[str] = []
    for ticker in tickers:
        try:
            placement = promote_scan_ticker(settings.output_dir, scan_id, ticker, list_key)
            if placement.blocked:
                warnings.extend(placement.warnings)
            elif placement.added:
                statuses.append(f"{placement.ticker} saved to {placement.list_label}")
            else:
                statuses.append(f"{placement.ticker} already exists in {placement.list_label}")
            warnings.extend(placement.warnings)
        except ValueError as error:
            warnings.append(str(error))
    summary = f"Promotion summary: {len(statuses)} reviewed. " + "; ".join(statuses)
    if warnings:
        summary += " Warnings: " + " ".join(dict.fromkeys(warnings))
    return render_greenrock_scanner(summary)


def stage_greenrock_scan_tickers(scan_id: str, tickers: tuple[str, ...], bucket: str) -> str:
    settings = get_settings()
    if not tickers:
        return render_greenrock_scanner("Staging blocked: choose at least one ticker.")
    statuses: list[str] = []
    warnings: list[str] = []
    for ticker in tickers:
        try:
            row = add_staged_scan_candidate(settings.output_dir, scan_id, ticker, bucket)
            statuses.append(f"{row['ticker']} staged as {STAGING_BUCKET_LABELS[row['staged_bucket']]}")
        except ValueError as error:
            warnings.append(str(error))
    summary = f"Stage summary: {len(statuses)} staged. " + "; ".join(statuses)
    if warnings:
        summary += " Warnings: " + " ".join(dict.fromkeys(warnings))
    return render_greenrock_scanner(summary)


def stage_greenrock_candidate(ticker: str, bucket: str, source_list: str = "manual", notes: str = "") -> str:
    settings = get_settings()
    try:
        row = add_staged_candidate(settings.output_dir, ticker, bucket, source_list=source_list, notes=notes)
        status = f"{row['ticker']} staged as {STAGING_BUCKET_LABELS[row['staged_bucket']]}."
    except ValueError as error:
        status = f"Staging blocked: {error}"
    return render_greenrock_staging(status)


def move_greenrock_staging_candidate(ticker: str, bucket: str) -> str:
    settings = get_settings()
    try:
        row = move_staged_candidate(settings.output_dir, ticker, bucket)
        status = f"{row['ticker']} moved to {STAGING_BUCKET_LABELS[row['staged_bucket']]}."
    except ValueError as error:
        status = f"Staging move blocked: {error}"
    return render_greenrock_staging(status)


def remove_greenrock_staging_candidate(ticker: str) -> str:
    settings = get_settings()
    removed = remove_staged_candidate(settings.output_dir, ticker)
    status = f"{ticker.strip().upper()} removed from staging." if removed else f"{ticker.strip().upper()} was not staged."
    return render_greenrock_staging(status)


def save_greenrock_staging_notes(ticker: str, notes: str) -> str:
    settings = get_settings()
    try:
        row = update_staged_notes(settings.output_dir, ticker, notes)
        status = f"Notes updated for {row['ticker']}."
    except ValueError as error:
        status = f"Notes update blocked: {error}"
    return render_greenrock_staging(status)


def trim_greenrock_staging_bucket(bucket: str) -> str:
    settings = get_settings()
    try:
        trim_staged_bucket(settings.output_dir, bucket)
        label = STAGING_BUCKET_LABELS.get(bucket, bucket)
        status = f"{label} trimmed to top ranked staged candidates."
    except ValueError as error:
        status = f"Trim blocked: {error}"
    return render_greenrock_staging(status)


def enrich_greenrock_staging_candidates() -> str:
    settings = get_settings()
    try:
        result = enrich_staged_candidates(settings.output_dir)
    except MarketDataConfigurationError as error:
        return render_greenrock_staging(f"Provider required: {error}")
    status = f"Enrichment complete: {len(result.enriched)} enriched."
    if result.skipped:
        status += f" Skipped: {', '.join(result.skipped)}."
    if result.errors:
        status += " Warnings: " + " ".join(result.errors)
    status += " No report, approval, PDF, email, publication, or external action was created."
    return render_greenrock_staging(status)


def generate_greenrock_staging_report(allow_underfilled: bool = False) -> str:
    settings = get_settings()
    readiness = staging_report_readiness(settings.output_dir, allow_underfilled=allow_underfilled)
    if not readiness.can_generate:
        return "Staging draft blocked: sections are underfilled. Confirm allow-underfilled to generate with warnings."
    db_path = initialize_database(settings.db_path)
    try:
        with connect(db_path) as connection:
            workflow_run, _, approval = run_greenrock_staging_report_workflow(
                connection,
                settings.output_dir,
                allow_underfilled=allow_underfilled,
            )
    except ValueError as error:
        return f"Staging draft blocked: {error}"
    return (
        f"Staging-sourced draft created for {workflow_run.run_id}; "
        f"approval {approval.id if approval else 'none'} is pending. "
        f"Review at /greenrock/reports/{workflow_run.run_id}/review."
    )


def remove_greenrock_watchlist_ticker(ticker: str, list_key: str) -> str:
    settings = get_settings()
    try:
        placement = remove_ticker_from_greenrock_list(settings.output_dir, ticker, list_key)
        if placement.warnings:
            status = " ".join(placement.warnings)
        else:
            status = f"{placement.ticker} removed from {placement.list_label}."
    except ValueError as error:
        status = f"Watchlist removal blocked: {error}"
    return render_greenrock_watchlists(status)


def render_greenrock_score(
    ticker: str = "",
    status_message: str | None = None,
    save_status: str | None = None,
) -> str:
    settings = get_settings()
    provider = provider_diagnostics()
    result_html = ""
    preview = None
    cleaned_ticker = ticker.strip().upper()
    if cleaned_ticker:
        try:
            preview = calculate_score_preview(
                cleaned_ticker,
                data_mode="real",
                output_dir=settings.output_dir,
            )
            result_html = _score_preview_panel(preview) + _score_memory_panel(settings.output_dir, cleaned_ticker) + _score_report_history_panel(cleaned_ticker)
        except (MarketDataConfigurationError, ValueError) as error:
            result_html = _score_provider_setup_card(provider, str(error))
            result_html += _score_memory_panel(settings.output_dir, cleaned_ticker) + _score_report_history_panel(cleaned_ticker)

    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero score-tool-hero">
      <div>
        {_greenrock_brand_block()}
        <p class="eyebrow">GreenRock Analysts</p>
        <h1>GreenRock Score Calculator</h1>
        <p>Score any ticker against the GreenRock technical dislocation framework.</p>
        <span class="badge {'approved' if provider.score_calculator_ready else 'ready'}">Provider Status: {_safe(provider.status_label)}</span>
      </div>
      <form method="post" action="/greenrock/score" class="score-form">
        <input name="ticker" value="{_safe(cleaned_ticker)}" placeholder="Ticker" required>
        <button type="submit" class="logo-score-button" aria-label="Calculate GreenRock Score">
          {_greenrock_logo("score-button-logo")}
        </button>
      </form>
    </section>
    {_score_provider_setup_card(provider) if not provider.score_calculator_ready and not cleaned_ticker else ""}
    {result_html}
    <section class="panel">
      <div class="section-head">
        <h2>How the Score Ranks</h2>
        <span class="subtle">Rank bands are review aids, not recommendations</span>
      </div>
      {_score_rank_explainer(preview)}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>How the Score Works</h2>
        <span class="subtle">Current methodology weights total 100 points</span>
      </div>
      <p class="subtle">GreenRock Score is a technical dislocation ranking aid. It prioritizes review candidates; it is not investment advice, not a price forecast, not a guarantee, and not publication approval.</p>
      <div class="score-explainer">
        <div><strong>52-week low proximity</strong><span>20 pts</span><p>Rewards names trading close to the 52-week low.</p></div>
        <div><strong>Bollinger Band setup</strong><span>20 pts</span><p>Rewards price location nearer the lower 2.5σ band.</p></div>
        <div><strong>RSI</strong><span>15 pts</span><p>Rewards weaker momentum below the neutral threshold.</p></div>
        <div><strong>Volume acceleration</strong><span>15 pts</span><p>Rewards improving 10-day average volume.</p></div>
        <div><strong>Moving average structure</strong><span>20 pts</span><p>Rewards EMA/SMA and 50/150 DMA dislocation with early repair.</p></div>
        <div><strong>Bullish / Bearish Evidence</strong><span>10 pts</span><p>Shows setup support and research cautions in plain English.</p></div>
      </div>
      <p><a href="/open-local?path={quote(str(Path('docs/GREENROCK_SCORE_METHODOLOGY.md').resolve()))}">Open methodology notes</a></p>
    </section>
    {_save_ticker_panel(cleaned_ticker, save_status)}
    """
    return _page("GreenRock Score Calculator", content, active="/greenrock/score")


def save_greenrock_score_ticker(ticker: str, list_key: str) -> str:
    settings = get_settings()
    cleaned_ticker = ticker.strip().upper()
    save_status = ""
    try:
        preview = calculate_score_preview(cleaned_ticker, data_mode="real", output_dir=settings.output_dir)
        placement = add_ticker_to_greenrock_list(
            settings.output_dir,
            preview.candidate.symbol,
            list_key,
            market_cap_bucket=preview.candidate.market_cap_bucket,
        )
        warnings = " ".join(placement.warnings)
        if placement.blocked:
            save_status = f"Save blocked: {warnings}"
        else:
            verb = "saved to" if placement.added else "already exists in"
            save_status = f"{placement.ticker} {verb} {placement.list_label}. {warnings}".strip()
    except (MarketDataConfigurationError, ValueError) as error:
        save_status = f"Save blocked: {error}"
    return render_greenrock_score(ticker=cleaned_ticker, save_status=save_status)


def render_tasks(status_message: str | None = None) -> str:
    return render_pt(status_message)


def render_agents(status_message: str | None = None) -> str:
    summary = _agent_status_summary()
    agents = summary["agents"]
    runs = summary["runs"][:12]
    cycle = summary["cycle"]
    market_scan = summary["market_scan"]
    cards = "".join(_agent_health_card(agent) for agent in agents)
    history = "".join(_agent_run_row(run) for run in runs) or "<tr><td colspan='5' class='empty'>No agent runs yet.</td></tr>"
    diff = _agent_cycle_diff_block(cycle.get("diff", {}))
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact agent-hero">
      <p class="eyebrow">Agent Monitor</p>
      <h1>Atlas Agent Monitor</h1>
      <p>Local workflow operators only. No email, publishing, trading, client files, credentials, or external LLM/API calls.</p>
    </section>
    <section class="agent-grid">{cards}</section>
    <section class="panel command-actions">
      <div class="section-head">
        <h2>Run Agent Cycle</h2>
        <span class="subtle">Market -> Evidence -> Fundamental -> Memory -> Report -> QA -> Inbox</span>
      </div>
      <section class="board-meta">
        {_attention_card("neutral", _safe(cycle["last_run"]), "Last Run", "Last Agent Cycle")}
        {_attention_card("green", str(cycle["completed"]), "Completed", "Latest cycle")}
        {_attention_card("red" if cycle["failed"] else "neutral", str(cycle["failed"]), "Failed", "Latest cycle")}
        {_attention_card("yellow" if cycle["blocked"] else "neutral", str(cycle["blocked"]), "Blocked", "Latest cycle")}
        {_attention_card("neutral", str(cycle.get("inbox_items_generated", 0)), "Inbox Items", "Created or refreshed")}
      </section>
      <section class="panel inner-panel">
        <h2>Market Agent Scan Policy</h2>
        <section class="board-meta">
          {_attention_card("neutral", _safe(market_scan.get("policy", "use_latest_scan")), "Policy Used", "Latest cycle")}
          {_attention_card("neutral", _safe(market_scan.get("latest_scan_id", "none")), "Referenced Scan", "Latest cycle")}
          {_attention_card("green" if market_scan.get("fresh_data_pulled") else "neutral", "yes" if market_scan.get("fresh_data_pulled") else "no", "Fresh Data Pulled", "Latest cycle")}
          {_attention_card("neutral", _safe(str(market_scan.get("scan_age_hours", "unknown"))), "Scan Age Hours", f"Threshold {market_scan.get('stale_threshold_hours', 24)}h")}
        </section>
        <p class="subtle">{_safe(market_scan.get("reason", "Default safe mode uses the latest successful scan."))}</p>
        <form method="post" action="/agents/run" class="inline-form" onsubmit="const policy=this.market_scan_policy.value; return confirm(policy === 'use_latest_scan' ? 'Run Agent Cycle using latest scan only? No fresh market data will be pulled.' : 'Run Agent Cycle with policy ' + policy + '? This may pull fresh local market data; no email, publishing, trading, client files, or approval bypass will occur.');">
          <select name="market_scan_policy">
            <option value="use_latest_scan">Use Latest Scan</option>
            <option value="run_fresh_scan">Run Fresh Scan</option>
            <option value="run_if_stale">Run If Stale</option>
          </select>
          <input name="stale_hours" type="number" min="0" step="1" value="24" aria-label="Stale threshold hours">
          <button type="submit">Run Agent Cycle</button>
        </form>
      </section>
    </section>
    {diff}
    <section class="panel">
      <div class="section-head"><h2>Latest Run History</h2><span class="subtle">Local JSON records</span></div>
      <table>
        <thead><tr><th>Run</th><th>Agent</th><th>Status</th><th>Completed</th><th>Summary</th></tr></thead>
        <tbody>{history}</tbody>
      </table>
    </section>
    """
    return _page("Atlas Agent Monitor", content, active="/agents")


def render_agent_update_history(agent_name: str, status_message: str | None = None) -> str:
    settings = get_settings()
    agent_lookup = {agent.agent_id: agent.name for agent in list_agent_states(settings.output_dir)}
    clean_name = agent_lookup.get(agent_name, agent_name.replace("-", " "))
    updates = list_agent_updates(settings.output_dir, clean_name)
    if not updates:
        updates = list_agent_updates(settings.output_dir, agent_name)
    rows = "".join(
        f"""
        <tr>
          <td>{_safe(update.created_at)}</td>
          <td>{_safe(update.cycle_id)}</td>
          <td>{_safe(update.status)} / {_safe(update.severity)}</td>
          <td>{_safe(update.headline)}</td>
          <td>{_safe(update.summary)}</td>
        </tr>
        """
        for update in updates[:50]
    ) or "<tr><td colspan='5' class='empty'>No structured agent updates yet.</td></tr>"
    latest = updates[0] if updates else None
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact agent-hero">
      <p class="eyebrow">Agent Update History</p>
      <h1>{_safe(latest.agent_name if latest else clean_name.title())}</h1>
      <p>Local structured updates with provenance. No email, publishing, trading, client files, credentials, or external LLM/API calls.</p>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Latest Update</h2><span class="subtle">{_safe(latest.created_at if latest else "none")}</span></div>
      <p><strong>{_safe(latest.headline if latest else "No update yet.")}</strong></p>
      <p>{_safe(latest.summary if latest else "Run atlas daily to create structured daily agent updates.")}</p>
      <p class="subtle">Provenance: cycle {_safe(latest.cycle_id if latest else "none")} / scan {_safe(latest.related_scan_id if latest else "none")}</p>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Update History</h2><span class="subtle">Local JSON records</span></div>
      <table>
        <thead><tr><th>Created</th><th>Cycle</th><th>Status</th><th>Headline</th><th>Summary</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """
    return _page("Agent Update History", content, active="/agents")


def render_atlas_wall(status_message: str | None = None) -> str:
    context = _load_context()
    summary = _agent_status_summary()
    provider = provider_diagnostics()
    scan = latest_scan(get_settings().output_dir)
    movers = memory_movers(get_settings().output_dir)
    inbox_items = list_inbox_items(get_settings().output_dir)
    pending_approvals = [approval for approval in context["approvals"] if approval.status == ApprovalStatus.PENDING]
    pdf_ready = _approved_reports_missing_pdf(context)
    pdf_exported = [artifact for artifact in context["artifacts"] if artifact.artifact_type == "report_final_pdf"]
    latest_snapshot = latest_morning_brief_snapshot(get_settings().output_dir)
    daily = latest_daily_brief(get_settings().output_dir)
    report_state = report_readiness(get_settings().output_dir, get_settings().db_path)
    inbox_counts = {
        "critical": sum(1 for item in inbox_items if item.severity == "critical"),
        "warning": sum(1 for item in inbox_items if item.severity == "warning"),
        "action": sum(1 for item in inbox_items if item.severity == "action"),
    }
    top_mover = _wall_top_mover(movers)
    task_count = len(report_workbench_summary(get_settings().output_dir, get_settings().db_path, create_tasks=False)["tasks"])
    handoff = _wall_handoff_state(get_settings().output_dir, summary)
    handoff_class = "handoff-active" if handoff["active"] else "handoff-idle"
    content = f"""
    <section class="wall-hero">
      <div class="wall-brand">{_atlas_logo("wall-logo")}{_greenrock_logo("wall-logo")}<div><p>Atlas OS Wall</p><h1>Command Center</h1></div></div>
      <div class="wall-header-status">
        {_wall_status_pill(status_message)}
        <div class="provider-pill {_wall_color(provider.score_calculator_ready)}"><strong>{_safe(provider.status_label)}</strong><span>{_safe(provider.active_provider_name)}</span></div>
        <div class="wall-clock"><strong>{_safe(datetime.now().strftime("%Y-%m-%d"))}</strong><span>{_safe(datetime.now().strftime("%H:%M"))} local time</span></div>
      </div>
    </section>
    <section class="wall-actions wall-actions-top">
      <form method="post" action="/atlas/wall/run" onsubmit="return confirm('Run Agent Cycle using latest scan only? This creates local records and inbox items only.');">
        <input type="hidden" name="market_scan_policy" value="use_latest_scan">
        <input type="hidden" name="stale_hours" value="24">
        <button type="submit">Run Agent Cycle</button>
      </form>
      <a href="/atlas/morning-brief">Morning Brief</a>
      <a href="/atlas/inbox">Atlas Inbox</a>
      <a href="/greenrock/market-pulse">Market Pulse</a>
      <a href="/agents">Agents</a>
      <a href="/greenrock/report-workbench">Report Workbench</a>
    </section>
    <section class="wall-intel-row">
      <article class="wall-panel">
        <h2>Daily Intelligence</h2>
        <p><strong>Latest daily cycle:</strong> {_safe(daily.get("daily_id", "none") if daily else "none")}</p>
        <p>{_safe(daily.get("executive_summary", "Run atlas daily to create the first Daily Intelligence Brief.") if daily else "Run atlas daily to create the first Daily Intelligence Brief.")}</p>
      </article>
      <article class="wall-panel">
        <h2>Top Priorities</h2>
        <div class="wall-list">{''.join(_wall_priority_item(item) for item in (daily or {}).get("research_priorities", [])[:3]) or "<p>No daily priorities yet.</p>"}</div>
      </article>
      <article class="wall-panel">
        <h2>Cycle Signals</h2>
        <p><strong>Biggest rank mover:</strong> {_safe(top_mover)}</p>
        <p><strong>New archetype leader:</strong> {_safe(_wall_new_leader(daily))}</p>
        <p><strong>QA health:</strong> {_safe(_wall_qa_health(daily))}</p>
        <p><strong>Last successful scan:</strong> {_safe(scan.scan_id if scan else "none")}</p>
      </article>
      <article class="wall-panel">
        <h2>Atlas Inbox</h2>
        <div class="wall-counts">
          {_wall_count("Critical", inbox_counts["critical"], "red")}
          {_wall_count("Warning", inbox_counts["warning"], "yellow")}
          {_wall_count("Action", inbox_counts["action"], "green")}
        </div>
        <div class="wall-list">{''.join(_wall_inbox_item(item) for item in inbox_items[:3]) or "<p>No open inbox items.</p>"}{_wall_more(len(inbox_items), 3)}</div>
      </article>
    </section>
    <section class="wall-bottom-split">
      <section class="agent-room {handoff_class}">
        <div class="section-head"><h2>Agent Room</h2><span>local workflow operators</span></div>
        <div class="agent-room-line"></div>
        <section class="wall-agent-grid">{''.join(_wall_agent_card(agent, handoff["labels"].get(agent.agent_id, "")) for agent in summary["agents"])}</section>
      </section>
      <section class="system-status-panel">
        <div class="section-head"><h2>System Status</h2><span>local only / gates intact</span></div>
        <section class="wall-status-grid">
          {_wall_stat("Provider", provider.status_label, provider.active_provider_name, _wall_color(provider.score_calculator_ready))}
          {_wall_stat("Latest Cycle", _wall_short_timestamp(summary["cycle"].get("last_run", "none")), f"{summary['cycle'].get('completed', 0)} complete / {summary['cycle'].get('blocked', 0)} blocked", _wall_color(not summary["cycle"].get("failed", 0)), summary["cycle"].get("last_run", "none"))}
          {_wall_stat("Market Pulse", scan.scan_id if scan else "none", f"scored {len(scan.rows) if scan else 0} / skipped {scan.skipped_ticker_count if scan else 0}", _wall_color(bool(scan and scan.rows)))}
          {_wall_stat("Approvals", str(len(pending_approvals)), f"PDF ready {len(pdf_ready)} / exported {len(pdf_exported)}", "yellow" if pending_approvals or pdf_ready else "green")}
          {_wall_stat("Report Ready", report_state["state"], report_state["next_operator_action"], _readiness_color(report_state["state"]))}
          {_wall_stat("Report Tasks", str(task_count), f"run {_wall_short_id(report_state.get('latest_report_run_id') or 'none')}", "green", report_state.get('latest_report_run_id') or 'none')}
          {_wall_stat("Pending Approval", str(report_state.get("pending_approvals", 0)), str(report_state.get("pending_approval_id") or "none"), "yellow" if report_state.get("pending_approvals") else "green")}
          {_wall_stat("PDF Status", report_state.get("pdf_status", "not_ready"), "approval gate intact", "green" if report_state.get("final_pdf_complete") else ("yellow" if report_state.get("approved_pdf_ready") else "gray"))}
          {_wall_stat("Future Integrations", "Slack planned", "Email / publishing / trading disabled", "gray")}
        </section>
      </section>
    </section>
    """
    return _wall_page("Atlas Wall", content)


def render_agent_run_detail(run_id: str, status_message: str | None = None) -> str:
    run = get_agent_run(get_settings().output_dir, run_id)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Agent Run</p>
      <h1>{_safe(run.agent_id)}</h1>
      <p>{_safe(run.run_id)}</p>
    </section>
    <section class="panel">
      <h2>Run Summary</h2>
      <section class="board-meta">
        {_attention_card("neutral", _safe(run.status), "Status", "Local run record")}
        {_attention_card("neutral", _safe(run.started_at), "Started", "UTC")}
        {_attention_card("neutral", _safe(run.completed_at or "running"), "Completed", "UTC")}
      </section>
      <h2>Outputs</h2>
      <pre>{_safe(json.dumps(run.outputs, indent=2, sort_keys=True))}</pre>
      <h2>Warnings</h2>
      <pre>{_safe(json.dumps(run.warnings, indent=2))}</pre>
      <h2>Errors</h2>
      <pre>{_safe(json.dumps(run.errors, indent=2))}</pre>
    </section>
    """
    return _page("Agent Run", content, active="/agents")


def render_atlas_inbox(status_message: str | None = None) -> str:
    items = list_inbox_items(get_settings().output_dir)
    rows = "".join(_atlas_inbox_row(item) for item in items) or "<tr><td colspan='10' class='empty'>No open inbox items.</td></tr>"
    cycle = agent_cycle_summary(get_settings().output_dir)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Atlas Inbox</p>
      <h1>Local Operator Queue</h1>
      <p>Agent-created items only. Dismissal is local and reversible from the JSON record.</p>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Atlas OS Roles</h2><span class="subtle">No email or Slack integration in this phase</span></div>
      <section class="detail-grid">
        {_detail_panel("Morning Brief", "executive summary of what changed")}
        {_detail_panel("Atlas Inbox", "operator awareness and action queue")}
        {_detail_panel("Wall", "passive command-center display")}
        {_detail_panel("Workbench", "production workflow control surface")}
      </section>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Latest Cycle Summary</h2><span class="subtle">{_safe(cycle.get("cycle_id", "none"))}</span></div>
      <section class="board-meta">
        {_attention_card("green", str(cycle.get("completed", 0)), "Completed Agents", "Latest cycle")}
        {_attention_card("red" if cycle.get("failed", 0) else "neutral", str(cycle.get("failed", 0)), "Failed Agents", "Latest cycle")}
        {_attention_card("yellow" if cycle.get("blocked", 0) else "neutral", str(cycle.get("blocked", 0)), "Blocked Agents", "Latest cycle")}
        {_attention_card("neutral", str(cycle.get("inbox_items_generated", 0)), "New Inbox Items", "Latest cycle diff")}
      </section>
    </section>
    <section class="panel">
      <table>
        <thead><tr><th>Created</th><th>Updated</th><th>Severity</th><th>Status</th><th>Title</th><th>Detail</th><th>Why</th><th>Source</th><th>Project</th><th>Cycle</th><th>Action</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """
    return _page("Atlas Inbox", content, active="/atlas/inbox")


def render_atlas_inbox_detail(item_id: str, status_message: str | None = None) -> str:
    item = get_inbox_item(get_settings().output_dir, item_id)
    target_url = item.target_url or "/atlas/inbox"
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Atlas Inbox Item</p>
      <h1>{_safe(item.title)}</h1>
      <p>{_safe(item.detail)}</p>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Why This Item Exists</h2><span class="badge">{_safe(item.severity)}</span></div>
      <p>{_safe(item.created_reason or "Created by the local Inbox Agent from the latest cycle findings.")}</p>
      <dl class="detail-list">
        <div><dt>Status</dt><dd>{_safe(item.status)}</dd></div>
        <div><dt>Severity</dt><dd>{_safe(item.severity)}</dd></div>
        <div><dt>Created Date</dt><dd>{_safe(_date_part(item.created_at))}</dd></div>
        <div><dt>Created Time</dt><dd>{_safe(_time_part(item.created_at))}</dd></div>
        <div><dt>Updated</dt><dd>{_safe(item.updated_at)}</dd></div>
        <div><dt>Source Agent</dt><dd>{_safe(item.source_agent)}</dd></div>
        <div><dt>Project</dt><dd>{_safe(str(item.related_project_id) if item.related_project_id else "none")}</dd></div>
        <div><dt>Agent Run</dt><dd>{_agent_run_link(item.related_agent_run_id)}</dd></div>
        <div><dt>Cycle</dt><dd>{_safe(item.related_cycle_id or "none")}</dd></div>
        <div><dt>Scan</dt><dd>{_safe(item.related_scan_id or "none")}</dd></div>
        <div><dt>Report Run</dt><dd>{_report_run_link(item.related_report_run_id)}</dd></div>
        <div><dt>Approval</dt><dd>{_approval_link(item.related_approval_id)}</dd></div>
        <div><dt>Target</dt><dd><a href="{_safe(target_url)}">{_safe(target_url)}</a></dd></div>
      </dl>
      <div class="action-row">
        <form method="post" action="/atlas/inbox/{quote(item.item_id)}/dismiss" onsubmit="return confirm('Dismiss this local inbox item?');"><button type="submit">Dismiss</button></form>
        <form method="post" action="/atlas/inbox/{quote(item.item_id)}/complete" onsubmit="return confirm('Mark this local inbox item complete?');"><button type="submit">Complete</button></form>
      </div>
    </section>
    """
    return _page("Atlas Inbox Item", content, active="/atlas/inbox")


def render_reports(status_message: str | None = None, filters: dict[str, str] | None = None) -> str:
    context = _load_context()
    filters = filters or {}
    report_index = _report_metadata_index(context)
    visible_reports = _filter_report_index(report_index, filters)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Artifacts / Reports</p>
      <h1>Local Output Index</h1>
      <p>Run-specific files, report records, and local artifact paths.</p>
    </section>
    <section class="panel">
      <div class="section-head"><h2>Reports as Research Memory</h2><span class="subtle">Indexed from local report metadata and artifacts</span></div>
      <form method="get" action="/reports" class="inline-form report-filter-form">
        <input name="ticker" value="{_safe(filters.get('ticker', ''))}" placeholder="Ticker search">
        <select name="status"><option value="">Any report status</option>{_filter_options(("pending", "approved", "rejected", "draft", "awaiting_approval"), filters.get("status", ""))}</select>
        <select name="approval"><option value="">Any approval status</option>{_filter_options(("pending", "approved", "rejected", "none"), filters.get("approval", ""))}</select>
        <select name="data_mode"><option value="">Any data mode</option>{_filter_options(("mock", "real"), filters.get("data_mode", ""))}</select>
        <button type="submit">Filter</button>
        <a class="button secondary" href="/reports">Clear</a>
      </form>
      {_reports_index_table(visible_reports)}
    </section>
    <section class="panel">
      <h2>Final PDF Archive</h2>
      {_final_reports_table(_final_pdf_archive_rows(context))}
    </section>
    """
    return _page("Artifacts / Reports", content, active="/reports")


def render_greenrock_final_reports(status_message: str | None = None) -> str:
    context = _load_context()
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero">
      <p class="eyebrow">GreenRock Analysts</p>
      <h1>Final PDF Archive</h1>
      <p>Approved local PDFs are preserved as the long-term report archive.</p>
    </section>
    <section class="panel">
      <h2>Final Reports</h2>
      {_final_reports_table(_final_pdf_archive_rows(context))}
    </section>
    """
    return _page("GreenRock Final Reports", content, active="/reports")


def render_greenrock_report_review(run_id: str, status_message: str | None = None) -> str:
    context = _load_context()
    run = get_workflow_run(context["connection"], run_id)
    report = next((item for item in context["reports"] if item.run_id == run_id), None)
    if report is None:
        raise KeyError(f"Unknown GreenRock report: {run_id}")
    approval = next((item for item in context["approvals"] if item.id == report.approval_id), None)
    if approval is None:
        approval = next((item for item in context["approvals"] if item.run_id == run_id), None)
    pdf_artifact = _latest_pdf_for_run(context["connection"], run_id)
    markdown = _read_report_markdown(report.content_path)
    metadata = _report_review_metadata(markdown, run, report, approval, pdf_artifact)
    content = f"""
    {_status_banner(status_message)}
    {_branded_title_hero("Review Report Draft", "GreenRock Report Review Center", "One local review surface for the draft, source disclosure, staged evidence, approval status, and PDF controls.", context, metadata)}
    <section class="detail-grid report-review-meta">
      {_detail_panel("Run ID", run.run_id)}
      {_detail_panel("Data Mode", metadata["data_mode"])}
      {_detail_panel("Selection Mode", metadata["selection_mode"])}
      {_detail_panel("Candidate Source", metadata["candidate_source"])}
      {_detail_panel("Approval Status", metadata["approval_status"])}
      {_detail_panel("PDF Status", metadata["pdf_status"])}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Source Disclosure</h2>
        <span class="subtle">Scan IDs and source lists appear only when available</span>
      </div>
      {_source_disclosure_html(markdown, metadata)}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Review Controls</h2>
        <span class="subtle">Local only. No publishing, email, or external distribution.</span>
      </div>
      <div class="action-row">
        {_review_approval_controls(approval, run.run_id)}
        {_review_pdf_controls(approval, pdf_artifact, run.run_id)}
        {_path_action(report.content_path, "Open Markdown")}
      </div>
    </section>
    {_review_candidate_section("Featured Archetype Leaders", _extract_report_section(markdown, ("## Featured Archetype Leaders",)))}
    {_review_candidate_section("Remaining Ranked Candidates", _extract_report_section(markdown, ("## Remaining Ranked Candidates",)))}
    {_review_candidate_section("Staging Bucket Appendix", _extract_report_section(markdown, ("## Appendix: Staging Buckets", "## Mega Rock Candidate", "## Mega Rock Pick")))}
    {_review_candidate_section("Mega Rock", _extract_report_section(markdown, ("## Mega Rock Candidate", "## Mega Rock Pick")))}
    {_review_candidate_section("Large Cap", _extract_report_section(markdown, ("## Large Cap Candidates", "## Top Large-Cap Candidates")))}
    {_review_candidate_section("Small/Mid", _extract_report_section(markdown, ("## Small/Mid Candidates", "## Top Small/Mid-Cap Candidates")))}
    <section class="panel disclosure-panel">
      <h2>Review Boundary</h2>
      <p>This page reviews a local draft only. Approval updates local records; PDF export remains blocked until approval; nothing is published or emailed.</p>
    </section>
    """
    return _page("GreenRock Report Review", content, active="/greenrock")


def render_approval_confirmation(approval_id: int, action: str, return_to: str = "/greenrock") -> str:
    context = _load_context()
    approval = get_approval(context["connection"], approval_id)
    action = action if action in {"approve", "reject"} else "approve"
    verb = "Approve" if action == "approve" else "Reject"
    content = f"""
    <section class="hero compact">
      <p class="eyebrow">Human Approval Gate</p>
      <h1>{verb} Approval {approval.id}</h1>
      <p>This action updates local SQLite records only. It does not publish, send, or distribute anything.</p>
    </section>
    <section class="panel">
      {_approval_detail_block(approval)}
      <form method="post" action="/approvals/{approval.id}/decide" class="confirm-form">
        <input type="hidden" name="action" value="{action}">
        <input type="hidden" name="return_to" value="{_safe(_local_return_to(return_to))}">
        <button type="submit">{verb} locally</button>
        <a class="button secondary" href="{_safe(_local_return_to(return_to))}">Cancel</a>
      </form>
    </section>
    """
    return _page(f"{verb} Approval", content, active="/greenrock")


def render_approval_detail(approval_id: int) -> str:
    context = _load_context()
    approval = get_approval(context["connection"], approval_id)
    content = f"""
    <section class="hero compact">
      <p class="eyebrow">Approval Detail</p>
      <h1>Approval {approval.id}</h1>
      <p>Read-only local approval record.</p>
    </section>
    <section class="panel">{_approval_detail_block(approval)}</section>
    """
    return _page(f"Approval {approval.id}", content, active="/greenrock")


def render_run_detail(run_id: str) -> str:
    context = _load_context()
    run = get_workflow_run(context["connection"], run_id)
    approvals = [approval for approval in context["approvals"] if approval.run_id == run_id]
    artifacts = list_artifacts_for_run(context["connection"], run_id)
    content = f"""
    <section class="hero compact">
      <p class="eyebrow">Run Detail</p>
      <h1>{_safe(run.run_id)}</h1>
      <p>{_safe(run.workflow_name)}</p>
    </section>
    <section class="detail-grid">
      {_detail_panel("Status", run.status)}
      {_detail_panel("Division", run.division)}
      {_detail_panel("Started", run.started_at)}
      {_detail_panel("Completed", run.completed_at or "not completed")}
      {_detail_panel("Data Mode", run.data_mode.upper())}
    </section>
    <section class="panel"><h2>Approvals</h2>{_approvals_table(approvals, actions=True)}</section>
    <section class="panel"><h2>Artifacts</h2>{_artifacts_table(artifacts)}</section>
    """
    return _page(f"Run {run.run_id}", content, active="/")


def render_artifact_detail(artifact_id: int) -> str:
    context = _load_context()
    artifact = get_artifact(context["connection"], artifact_id)
    content = f"""
    <section class="hero compact">
      <p class="eyebrow">Artifact Detail</p>
      <h1>Artifact {artifact.id}</h1>
      <p>{_safe(artifact.artifact_type)}</p>
    </section>
    <section class="detail-grid">
      {_detail_panel("Run", artifact.run_id)}
      {_detail_panel("Type", artifact.artifact_type)}
      {_detail_panel("Created", artifact.created_at)}
      {_detail_panel("Path", artifact.path)}
    </section>
    <section class="panel">{_path_block(artifact.path, "Open local artifact")}</section>
    """
    return _page(f"Artifact {artifact.id}", content, active="/reports")


def _read_report_markdown(content_path: str | None) -> str:
    if not content_path:
        return ""
    path = Path(content_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _report_review_metadata(markdown: str, run, report, approval, pdf_artifact) -> dict[str, str]:
    source_lists = _markdown_field(markdown, "Source lists") or _markdown_field(markdown, "Staged Candidate Source")
    scan_ids = _markdown_field(markdown, "Scan IDs") or _markdown_field(markdown, "Source Scan ID")
    return {
        "data_mode": _markdown_field(markdown, "Data Mode") or run.data_mode.upper(),
        "selection_mode": _markdown_field(markdown, "Selection Mode") or "-",
        "candidate_source": _markdown_field(markdown, "Candidate Source") or "-",
        "approval_status": approval.status.value if approval else "none",
        "pdf_status": "exported" if pdf_artifact else "not exported",
        "source_lists": source_lists or "not listed",
        "scan_ids": scan_ids or "not listed",
        "report_status": report.status,
    }


def _source_disclosure_html(markdown: str, metadata: dict[str, str]) -> str:
    disclosure = _extract_report_section(markdown, ("## Candidate Source Disclosure",))
    rendered = _markdown_fragment_to_html(disclosure) if disclosure else ""
    fallback = f"""
    <dl class="detail-list">
      <div><dt>Candidate Source</dt><dd>{_safe(metadata["candidate_source"])}</dd></div>
      <div><dt>Source Lists</dt><dd>{_safe(metadata["source_lists"])}</dd></div>
      <div><dt>Scan IDs</dt><dd>{_safe(metadata["scan_ids"])}</dd></div>
    </dl>
    """
    return rendered or fallback


def _review_approval_controls(approval, run_id: str) -> str:
    review_path = f"/greenrock/reports/{quote(run_id)}/review"
    if not approval:
        return "<span class='button disabled'>No approval linked</span>"
    if approval.status != ApprovalStatus.PENDING:
        return f"<span class='button disabled'>Approval {approval.status.value}</span>"
    return (
        f"<a class='button' href='/approvals/{approval.id}/confirm?action=approve&return_to={quote(review_path)}'>Approve pending report</a>"
        f"<a class='button secondary' href='/approvals/{approval.id}/confirm?action=reject&return_to={quote(review_path)}'>Reject pending report</a>"
    )


def _review_pdf_controls(approval, pdf_artifact, run_id: str) -> str:
    review_path = f"/greenrock/reports/{quote(run_id)}/review"
    if pdf_artifact:
        return _open_link(pdf_artifact.path, "Open PDF")
    if not approval or approval.status != ApprovalStatus.APPROVED:
        return "<span class='button disabled'>PDF export blocked until approval</span>"
    return f"""
    <form method="post" action="/greenrock/approvals/{approval.id}/export-pdf" onsubmit="return confirm('Export approved report PDF locally?');">
      <input type="hidden" name="return_to" value="{_safe(review_path)}">
      <button type="submit">Export PDF</button>
    </form>
    """


def _review_candidate_section(title: str, section_markdown: str) -> str:
    if not section_markdown.strip():
        return f"""
        <section class="panel report-review-section">
          <h2>{_safe(title)}</h2>
          <p class="empty">No report section available.</p>
        </section>
        """
    return f"""
    <section class="panel report-review-section">
      <div class="section-head">
        <h2>{_safe(title)}</h2>
        <span class="subtle">Candidate table and evidence notes</span>
      </div>
      {_markdown_fragment_to_html(section_markdown)}
    </section>
    """


def _extract_report_section(markdown: str, headings: tuple[str, ...]) -> str:
    lines = markdown.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() in headings:
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        line = lines[index].strip()
        if line.startswith("## ") and line not in headings:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _markdown_fragment_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    parts: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("|"):
            table_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            parts.append(_markdown_table_html(table_lines))
            continue
        if line.startswith("### "):
            parts.append(f"<h3>{_safe(line[4:])}</h3>")
        elif line.startswith("- "):
            items = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(f"<li>{_clean_markdown_inline(lines[index].strip()[2:])}</li>")
                index += 1
            parts.append(f"<ul class='compact-list'>{''.join(items)}</ul>")
            continue
        elif line.startswith("> "):
            parts.append(f"<p class='subtle'><em>{_clean_markdown_inline(line[2:])}</em></p>")
        else:
            parts.append(f"<p>{_clean_markdown_inline(line)}</p>")
        index += 1
    return "".join(parts) or "<p class='empty'>No content available.</p>"


def _markdown_table_html(table_lines: list[str]) -> str:
    rows: list[list[str]] = []
    for raw in table_lines:
        cells = [cell.strip() for cell in raw.strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    header = "".join(f"<th>{_clean_markdown_inline(cell)}</th>" for cell in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{_clean_markdown_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in rows[1:]
    )
    return f"<div class='review-table-wrap'><table class='review-table'><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>"


def _markdown_field(markdown: str, label: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        bold_prefix = f"**{label}:**"
        bullet_prefix = f"- {label}:"
        if stripped.startswith(bold_prefix):
            return stripped.removeprefix(bold_prefix).strip()
        if stripped.startswith(bullet_prefix):
            return stripped.removeprefix(bullet_prefix).strip()
    return ""


def _clean_markdown_inline(text: str) -> str:
    cleaned = text.replace("**", "")
    return _safe(cleaned)


def decide_approval(approval_id: int, decision: str) -> None:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        if decision == "approve":
            approve_approval(connection, approval_id)
        elif decision == "reject":
            reject_approval(connection, approval_id)
        else:
            raise ValueError(f"Unsupported approval decision: {decision}")


def export_greenrock_pdf(approval_id: int):
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        approval = get_approval(connection, approval_id)
        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError("PDF export requires an approved report.")
        if not approval.artifact_path or not approval.run_id:
            raise ValueError("Approval is not linked to a report draft.")

        markdown_path = Path(approval.artifact_path)
        pdf_path = markdown_path.with_name("greenrock_report_final.pdf")
        render_markdown_report_to_pdf(markdown_path, pdf_path)

        existing_pdf = _latest_pdf_for_run(connection, approval.run_id)
        if existing_pdf:
            artifact = existing_pdf
            action = "artifact_updated"
        else:
            artifact = create_artifact(connection, approval.run_id, "report_final_pdf", pdf_path)
            action = "artifact_created"
        create_audit_log(
            connection,
            actor="command_center",
            action=action,
            detail=f"GreenRock final PDF: {pdf_path}",
            run_id=approval.run_id,
            artifact_id=artifact.id,
            approval_id=approval.id,
        )
        return artifact


def run_greenrock_report_from_browser(data_mode: str | None = None) -> tuple[bool, str]:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    selected_data_mode = _normalize_data_mode(data_mode or _default_greenrock_data_mode())
    try:
        with connect(db_path) as connection:
            workflow_run, _, approval = run_greenrock_screening_workflow(
                connection,
                settings.output_dir,
                include_report_draft=True,
                data_mode=selected_data_mode,
            )
    except MarketDataConfigurationError as error:
        return False, f"GreenRock {selected_data_mode.upper()} report blocked: {error}"
    return True, (
        f"GreenRock {workflow_run.data_mode.upper()} report draft created for {workflow_run.run_id}; "
        f"approval {approval.id if approval else 'none'} is pending."
    )


def save_manual_task(name: str, division: str, notes: str | None = None, project_id: int | None = None) -> None:
    if not name.strip():
        return
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        create_manual_task(connection, name, division, notes.strip() if notes else None, project_id=project_id)


def save_project(name: str, division: str, status: str) -> None:
    if not name.strip():
        return
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        create_project(connection, name, division, status)


def save_project_status(project_id: int, status: str) -> None:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        update_project_status(connection, project_id, status)


def save_task_status(task_id: int, status: str) -> None:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        update_manual_task_status(connection, task_id, status)


def open_local_path(path: str) -> bool:
    target = Path(unquote(path)).expanduser()
    if not target.exists() or sys.platform != "darwin":
        return False
    result = subprocess.run(["open", str(target)], check=False)
    return result.returncode == 0


def _load_context() -> dict:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    connection = connect(db_path)
    runs = list_workflow_runs(connection)
    reports = list_reports(connection)
    artifacts = list_artifacts(connection)
    ticker_universes = load_greenrock_universes(settings.output_dir)
    latest_run = next((run for run in runs if run.division == "greenrock"), None)
    latest_report = _latest_report_for_runs(reports, runs)
    latest_pdf = _latest_pdf_for_run(connection, latest_report.run_id if latest_report else None)
    return {
        "settings": settings,
        "connection": connection,
        "runs": runs,
        "approvals": list_approvals(connection),
        "artifacts": artifacts,
        "audit_logs": list_audit_logs(connection),
        "reports": reports,
        "projects": list_projects(connection),
        "tasks": list_manual_tasks(connection),
        "latest_run": latest_run,
        "latest_report": latest_report,
        "latest_pdf": latest_pdf,
        "ticker_universes": ticker_universes,
    }


def _default_greenrock_data_mode() -> str:
    configured = os.getenv("ATLAS_GREENROCK_DEFAULT_DATA_MODE", "mock").strip().lower()
    return configured if configured in {"mock", "real"} else "mock"


def _normalize_data_mode(value: str) -> str:
    selected = value.strip().lower()
    return selected if selected in {"mock", "real"} else "mock"


def _primary_workflow_runs(context) -> tuple:
    latest_run = context["latest_run"]
    final_pdf_run_ids = {
        artifact.run_id
        for artifact in context["artifacts"]
        if artifact.artifact_type == "report_final_pdf"
    }
    selected = []
    seen = set()
    for run in context["runs"]:
        if run == latest_run or run.run_id in final_pdf_run_ids or run.division != "greenrock":
            if run.run_id not in seen:
                selected.append(run)
                seen.add(run.run_id)
    return tuple(selected[:8])


def _visible_report_records(context) -> tuple:
    latest_report = context["latest_report"]
    visible = []
    if latest_report:
        visible.append(latest_report)
    final_pdf_run_ids = {
        artifact.run_id
        for artifact in context["artifacts"]
        if artifact.artifact_type == "report_final_pdf"
    }
    for report in context["reports"]:
        if report.run_id in final_pdf_run_ids and report not in visible:
            visible.append(report)
    return tuple(visible)


def _final_pdf_archive_rows(context) -> tuple[dict[str, str], ...]:
    approvals_by_run = {
        approval.run_id: approval
        for approval in context["approvals"]
        if approval.run_id and approval.status == ApprovalStatus.APPROVED
    }
    rows = []
    for artifact in context["artifacts"]:
        if artifact.artifact_type != "report_final_pdf":
            continue
        approval = approvals_by_run.get(artifact.run_id)
        rows.append(
            {
                "run_id": artifact.run_id,
                "approval_id": str(approval.id if approval else "unknown"),
                "path": artifact.path,
                "created_at": artifact.created_at,
            }
        )
    return tuple(rows)


def _build_inbox_items(context, pending_approvals, reports_ready, failed_runs) -> list[dict[str, str]]:
    items = []
    for approval in pending_approvals[:3]:
        items.append(
            {
                "title": "Review GreenRock Report",
                "detail": f"Approval {approval.id} is pending human review.",
                "href": f"/greenrock/reports/{quote(approval.run_id)}/review" if approval.run_id else f"/approvals/{approval.id}/confirm?action=approve",
                "status": "attention",
                "label": "pending approval",
            }
        )
    for report in reports_ready[:3]:
        items.append(
            {
                "title": "Approve PDF Export",
                "detail": f"Run {report.run_id} is approved and missing final PDF.",
                "href": f"/greenrock/reports/{quote(report.run_id)}/review" if report.run_id else "/greenrock",
                "status": "ready",
                "label": "ready for PDF",
            }
        )
    for task in [task for task in context["tasks"] if task.status in {"pending", "awaiting_review"}][:3]:
        items.append(
            {
                "title": task.name,
                "detail": f"{task.division} task is {task.status.replace('_', ' ')}.",
                "href": "/tasks",
                "status": "neutral",
                "label": "manual task",
            }
        )
    for run in failed_runs[:2]:
        items.append(
            {
                "title": "Review Failed Workflow",
                "detail": f"{run.workflow_name} failed for {run.run_id}.",
                "href": f"/runs/{quote(run.run_id)}",
                "status": "attention",
                "label": "failed workflow",
            }
        )
    placeholders = (
        ("Follow up Insurance Prospect", "Placeholder for GreenRock Insurance follow-up queue."),
        ("Complete Bat Signal Fixture Design", "Placeholder for Variance Capital workflow design."),
        ("Review Report Critique Notes", "Placeholder for product and compliance critique review."),
    )
    for title, detail in placeholders:
        if len(items) >= 6:
            break
        items.append(
            {
                "title": title,
                "detail": detail,
                "href": "/projects",
                "status": "placeholder",
                "label": "placeholder",
            }
        )
    return items


def _morning_brief_data(context) -> dict:
    settings = get_settings()
    scan = latest_scan(settings.output_dir)
    master = default_universe_manager(settings.output_dir).master_universe()
    movers = memory_movers(settings.output_dir)
    pending_approvals = [approval for approval in context["approvals"] if approval.status == ApprovalStatus.PENDING]
    pdf_ready = _approved_reports_missing_pdf(context)
    pdf_exported = [artifact for artifact in context["artifacts"] if artifact.artifact_type == "report_final_pdf"]
    cycle = agent_cycle_summary(settings.output_dir)
    agents = list_agent_states(settings.output_dir)
    agent_inbox_items = list_inbox_items(settings.output_dir)
    priority_counts: dict[str, int] = {}
    high_confidence = 0
    if scan:
        for row in scan.rows:
            priority = row.get("research_priority", "") or "Unassigned"
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
            if _as_float(row.get("greenrock_confidence", "")) >= 75:
                high_confidence += 1
    important_changes = sum(
        len(movers[key])
        for key in ("rank_improvers", "score_improvers", "confidence_improvers", "evidence_improvers", "deteriorations")
    )
    actions = []
    if not scan:
        actions.append("Run a Market Pulse scan to populate the Morning Brief.")
    if pending_approvals:
        actions.append(f"Review {len(pending_approvals)} pending approval(s).")
    if pdf_ready:
        actions.append(f"Export {len(pdf_ready)} approved report PDF(s) when ready.")
    if important_changes:
        actions.append("Review Atlas Memory movers before staging the next report slate.")
    if not actions:
        actions.append("No urgent local action items.")
    action_items = _morning_brief_action_items(context, scan, movers, pending_approvals, pdf_ready)
    return {
        "scan_complete": bool(scan and scan.rows),
        "scan_status": "complete" if scan and scan.rows else "not complete",
        "latest_scan_id": scan.scan_id if scan else "",
        "universe_size": master.size,
        "scored_count": len(scan.rows) if scan else 0,
        "skipped_count": scan.skipped_ticker_count if scan else 0,
        "provider_failures": scan.provider_failure_count if scan else 0,
        "high_confidence_count": high_confidence,
        "priority_counts": priority_counts or {"none": 0},
        "movers": movers,
        "important_changes": important_changes,
        "pending_approvals": len(pending_approvals),
        "pdf_ready": len(pdf_ready),
        "pdf_exported": len(pdf_exported),
        "actions": tuple(actions),
        "action_items": action_items,
        "last_agent_cycle": cycle["last_run"],
        "agent_run_summary": cycle,
        "agent_health_cards": agents,
        "agent_inbox_items": agent_inbox_items,
    }


def _morning_brief_action_items(context, scan, movers, pending_approvals, pdf_ready) -> tuple[dict[str, str], ...]:
    settings = get_settings()
    items: list[dict[str, str]] = []
    for approval in pending_approvals[:3]:
        items.append(
            {
                "title": "Pending approval",
                "detail": f"Approval {approval.id} is waiting for human review.",
                "href": f"/greenrock/reports/{quote(approval.run_id)}/review" if approval.run_id else f"/approvals/{approval.id}/confirm?action=approve",
                "status": "attention",
                "label": "approval gate",
            }
        )
    for report in context["reports"][:3]:
        if report.status in {"draft", "pending", "awaiting_approval"}:
            items.append(
                {
                    "title": "Report awaiting review",
                    "detail": f"{report.title} needs local review.",
                    "href": f"/greenrock/reports/{quote(report.run_id)}/review" if report.run_id else "/greenrock",
                    "status": "attention",
                    "label": "report review",
                }
            )
    for item in staging_readiness(settings.output_dir):
        if item.target is not None and item.count < item.target:
            items.append(
                {
                    "title": "Staging underfilled",
                    "detail": f"{item.label} has {item.count}/{item.target} candidates.",
                    "href": "/greenrock/staging",
                    "status": "ready",
                    "label": "staging",
                }
            )
    analytics = staging_analytics_status(settings.output_dir)
    if analytics.missing_count:
        items.append(
            {
                "title": "Missing analytics",
                "detail": f"{analytics.missing_count} staged candidate(s) need refreshed analytics.",
                "href": "/greenrock/staging",
                "status": "ready",
                "label": "enrichment",
            }
        )
    if scan and scan.provider_failure_count:
        items.append(
            {
                "title": "Provider failures",
                "detail": f"{scan.provider_failure_count} ticker(s) failed in the latest provider fetch.",
                "href": "/greenrock/universe",
                "status": "attention",
                "label": "health",
            }
        )
    for line in _new_archetype_leader_items(settings.output_dir)[:3]:
        items.append(
            {
                "title": "New archetype leader",
                "detail": line,
                "href": "/greenrock/market-pulse",
                "status": "ready",
                "label": "leader",
            }
        )
    for key, label in (
        ("rank_improvers", "Biggest rank mover"),
        ("score_improvers", "Biggest score mover"),
        ("confidence_improvers", "Biggest confidence mover"),
        ("evidence_improvers", "Biggest evidence mover"),
        ("deteriorations", "Biggest deterioration"),
    ):
        if movers[key]:
            item = movers[key][0]
            items.append(
                {
                    "title": label,
                    "detail": f"{item.ticker}: rank {item.previous.get('rank', '')}->{item.current.get('rank', '')}; score {item.previous.get('greenrock_score', '')}->{item.current.get('greenrock_score', '')}.",
                    "href": "/greenrock/market-pulse",
                    "status": "neutral" if key != "deteriorations" else "attention",
                    "label": "Atlas Memory",
                }
            )
    if pdf_ready:
        items.append(
            {
                "title": "PDF ready",
                "detail": f"{len(pdf_ready)} approved report(s) can be exported locally.",
                "href": f"/greenrock/reports/{quote(pdf_ready[0].run_id)}/review" if pdf_ready[0].run_id else "/greenrock/final-reports",
                "status": "ready",
                "label": "approved PDF",
            }
        )
    if not items:
        items.append(
            {
                "title": "No urgent local action items",
                "detail": "Run a Market Pulse scan or review staging when ready.",
                "href": "/greenrock/market-pulse",
                "status": "neutral",
                "label": "clear",
            }
        )
    return tuple(items[:12])


def _morning_brief_action_buttons(context, brief: dict) -> str:
    latest_report = context["latest_report"]
    latest_review = f"/greenrock/reports/{quote(latest_report.run_id)}/review" if latest_report and latest_report.run_id else ""
    readiness = staging_report_readiness(get_settings().output_dir, allow_underfilled=False)
    analytics = staging_analytics_status(get_settings().output_dir)
    staging_ready = readiness.can_generate and analytics.complete
    draft_button = (
        '<a class="button" href="/greenrock/staging/generate/confirm">Generate Draft From Staged Slate</a>'
        if staging_ready
        else '<span class="button disabled">Generate Draft From Staged Slate: staging not ready</span>'
    )
    latest_report_button = (
        f'<a class="button secondary" href="{latest_review}">Open latest GreenRock report review</a>'
        if latest_review
        else '<span class="button disabled">Open latest GreenRock report review: no report yet</span>'
    )
    return f"""
    <section class="panel command-actions">
      <div class="section-head">
        <h2>Morning Brief Actions</h2>
        <span class="subtle">Navigation only. Approval gates remain intact.</span>
      </div>
      <div class="primary-action">
        <span class="badge attention">Primary action</span>
        <strong>{_safe(brief["action_items"][0]["title"] if brief.get("action_items") else "No urgent local action items")}</strong>
        <p>{_safe(brief["action_items"][0]["detail"] if brief.get("action_items") else "No urgent local action items.")}</p>
      </div>
      <div class="action-row">
        <a class="button" href="/greenrock/market-pulse">Open latest Market Pulse</a>
        <a class="button secondary" href="/greenrock">Review pending approvals</a>
        {latest_report_button}
        <a class="button" href="/greenrock/market-pulse/stage/confirm?slate=analyst">Stage Analyst Slate from latest Market Pulse</a>
        {draft_button}
        <a class="button secondary" href="/greenrock/final-reports">Open PDF archive / final reports</a>
      </div>
      <p class="subtle">Scan status: {_safe(brief["scan_status"])}. Pending approvals: {_safe(brief["pending_approvals"])}.</p>
    </section>
    """


def save_morning_brief_snapshot_from_browser() -> str:
    settings = get_settings()
    saved = save_morning_brief_snapshot(settings.output_dir, settings.db_path)
    return (
        f"Morning Brief snapshot saved: {saved['snapshot_id']}. "
        "No email, publishing, trading, client-file, or external action was created."
    )


def run_agent_cycle_from_browser(market_scan_policy: str = "use_latest_scan", stale_hours: float = 24.0) -> str:
    settings = get_settings()
    runs = run_agent_cycle(settings.output_dir, settings.db_path, market_scan_policy=market_scan_policy, stale_hours=stale_hours)
    cycle = agent_cycle_summary(settings.output_dir)
    market_scan = cycle.get("market_scan", {})
    failed = sum(1 for run in runs if run.status == "failed")
    blocked = sum(1 for run in runs if run.status == "blocked")
    return (
        f"Agent Cycle complete: {len(runs)} local agent run(s), {failed} failed, {blocked} blocked. "
        f"Market scan policy: {market_scan.get('policy', market_scan_policy)}; "
        f"fresh data pulled: {'yes' if market_scan.get('fresh_data_pulled') else 'no'}. "
        "No email, publishing, trading, broker/API order, client-file, credential, or external LLM/API action was created."
    )


def run_greenrock_report_workbench_action(action: str) -> tuple[str, str]:
    settings = get_settings()
    normalized = action.strip()
    if normalized == "daily":
        brief = run_daily_cycle(settings.output_dir, settings.db_path, market_scan_policy="use_latest_scan")
        return (
            f"Daily Intelligence Cycle complete: {brief['daily_id']}. No email, publishing, trading, client-file, PDF export, approval, or external action was created.",
            "/greenrock/report-workbench",
        )
    if normalized == "stage_slate":
        return stage_market_pulse_from_browser(overwrite_staging=True, slate_mode="analyst"), "/greenrock/report-workbench"
    if normalized == "enrich":
        page = enrich_greenrock_staging_candidates()
        status = "Staged candidate enrichment attempted. Review staging for details. No approval, PDF, email, publishing, trading, client-file, or external action was created."
        if "Provider required" in page:
            status = "Staged candidate enrichment blocked: provider required. No report, approval, PDF, email, publishing, trading, client-file, or external action was created."
        return status, "/greenrock/report-workbench"
    if normalized == "export_pdf":
        readiness = report_readiness(settings.output_dir, settings.db_path)
        approval_id = readiness.get("approved_approval_id")
        if not approval_id:
            return "PDF export blocked: no approved report is ready for PDF export.", "/greenrock/report-workbench"
        try:
            export_greenrock_pdf(int(approval_id))
        except (KeyError, ValueError) as error:
            return f"PDF export blocked: {error}", "/greenrock/report-workbench"
        return f"Approved PDF exported for approval {approval_id}.", "/greenrock/final-reports"
    return "Unknown workbench action. No local changes were made.", "/greenrock/report-workbench"


def save_candidate_decision_from_browser(
    ticker: str,
    decision: str,
    note: str,
    related_scan_id: str,
    related_daily_id: str,
    related_report_run_id: str,
) -> str:
    try:
        record = record_candidate_decision(
            get_settings().output_dir,
            ticker,
            decision,
            note=note,
            related_scan_id="" if related_scan_id == "none" else related_scan_id,
            related_daily_id="" if related_daily_id == "none" else related_daily_id,
            related_report_run_id="" if related_report_run_id == "none" else related_report_run_id,
        )
    except ValueError as error:
        return f"Candidate decision blocked: {error}. No score, rank, staging, approval, PDF, email, publishing, trading, client-file, or external action was changed."
    if record.decision == "research":
        save_manual_task(
            f"Research needed for {record.ticker}",
            "greenrock",
            record.note or "Candidate Review marked Research Needed from Report Workbench.",
        )
    return (
        f"Candidate decision saved for {record.ticker}: {record.decision}. "
        "GreenRock Score, canonical rank, staging, approvals, and PDF gates were unchanged."
    )


def dismiss_atlas_inbox_item(item_id: str) -> None:
    dismiss_inbox_item(get_settings().output_dir, item_id)


def complete_atlas_inbox_item(item_id: str) -> None:
    complete_inbox_item(get_settings().output_dir, item_id)


def _morning_brief_snapshot_row(snapshot: dict) -> str:
    snapshot_id = snapshot.get("snapshot_id", "")
    return f"""
    <tr>
      <td>{_safe(snapshot.get("timestamp", ""))}</td>
      <td>{_safe(snapshot.get("latest_scan_id", "none"))}</td>
      <td>{_safe(snapshot.get("scored_count", 0))}</td>
      <td>{_safe(_snapshot_top_mover(snapshot))}</td>
      <td>{_safe(snapshot.get("pending_approvals", 0))}</td>
      <td><a class="button secondary" href="/atlas/morning-brief/history/{quote(snapshot_id)}">View Snapshot</a></td>
    </tr>
    """


def _snapshot_top_mover(snapshot: dict) -> str:
    movers = snapshot.get("top_movers", {})
    for key in ("rank_improvers", "score_improvers", "confidence_improvers", "evidence_improvers", "deteriorations"):
        rows = movers.get(key, ())
        if rows:
            return rows[0].get("summary", "none")
    return "none"


def _snapshot_mover_block(title: str, rows) -> str:
    if not rows:
        body = "<p class='subtle'>No movement captured.</p>"
    else:
        body = "<ul class='compact-list'>" + "".join(f"<li>{_safe(row.get('summary', ''))}</li>" for row in rows) + "</ul>"
    return f"<article class='watchlist-card'><h3>{_safe(title)}</h3>{body}</article>"


def _approved_reports_missing_pdf(context) -> list:
    ready = []
    for report in context["reports"]:
        if report.status != "approved" or not report.run_id:
            continue
        if _latest_pdf_for_run(context["connection"], report.run_id) is None:
            ready.append(report)
    return ready


def _latest_report_for_runs(reports, runs):
    reports_by_run = {}
    for report in reports:
        if report.run_id and report.run_id not in reports_by_run:
            reports_by_run[report.run_id] = report
    for workflow_run in runs:
        if workflow_run.division == "greenrock" and workflow_run.run_id in reports_by_run:
            return reports_by_run[workflow_run.run_id]
    return None


def _latest_pdf_for_run(connection, run_id: str | None):
    if not run_id:
        return None
    for artifact in list_artifacts_for_run(connection, run_id):
        if artifact.artifact_type == "report_final_pdf":
            return artifact
    return None


def _candidate_rows(path: str | None, limit: int | None = 6) -> list[dict[str, str]]:
    if not path:
        return []
    candidate_path = Path(path)
    if not candidate_path.exists():
        return []
    with candidate_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    rows.sort(key=_row_score, reverse=True)
    return rows[:limit] if limit is not None else rows


def _candidate_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<p class='empty'>No candidates available yet.</p>"
    body = "".join(
        "<tr>"
        f"<td>{_safe(row.get('symbol', ''))}</td>"
        f"<td>{_safe(row.get('company_name', ''))}</td>"
        f"<td>{_safe(row.get('score', ''))}</td>"
        f"<td><span class='badge signal'>{_safe(_candidate_signal(row))}</span></td>"
        "</tr>"
        for row in rows
    )
    return f"<table><thead><tr><th>Symbol</th><th>Name</th><th>GreenRock Score</th><th>Signal Label</th></tr></thead><tbody>{body}</tbody></table>"


def _universe_panels(universes: dict) -> str:
    labels = {
        "mega_rock": "Mega Rock Candidate Pool",
        "large_cap": "Large-Cap Watchlist",
        "small_mid_cap": "Small/Mid-Cap Watchlist",
    }
    panels = []
    for name, universe in universes.items():
        panels.append(
            f"""
            <div class="universe-panel">
              <h3>{_safe(labels.get(name, name))}</h3>
              <p class="subtle">{len(universe.tickers)} tickers at {_safe(universe.path)}</p>
              <div class="ticker-cloud">{''.join(f'<span>{_safe(ticker)}</span>' for ticker in universe.tickers)}</div>
            </div>
            """
        )
    return "<div class='universe-grid'>" + "".join(panels) + "</div>"


def _top_candidate(rows: list[dict[str, str]]) -> dict[str, str] | None:
    return rows[0] if rows else None


def _row_score(row: dict[str, str]) -> float:
    try:
        return float(row.get("score", "0"))
    except ValueError:
        return 0.0


def _mega_pick_card(row: dict[str, str] | None) -> str:
    if not row:
        return "<p class='empty'>No Mega Rock pick available yet. Run a GreenRock report first.</p>"
    return f"""
    <article class="mega-card" data-pick-slot="mega">
      <div>
        <span class="badge signal">{_safe(_candidate_signal(row))}</span>
        <h2>{_safe(row.get('symbol', ''))}</h2>
        <p>{_safe(row.get('company_name', ''))}</p>
      </div>
      <dl>
        <div><dt>GreenRock Score</dt><dd>{_safe(row.get('score', ''))}</dd></div>
        <div><dt>Price</dt><dd>{_format_currency(row.get('latest_close', ''))}</dd></div>
        <div><dt>Market Cap</dt><dd>{_format_market_cap(row.get('market_cap', ''))}</dd></div>
        <div><dt>RSI</dt><dd>{_safe(row.get('rsi_14', ''))}</dd></div>
        <div><dt>52-week Low Distance</dt><dd>{_format_percent(row.get('low_proximity', ''))}</dd></div>
        <div><dt>Bollinger Status</dt><dd>{_safe(_bollinger_status(row))}</dd></div>
        <div><dt>Volume Acceleration</dt><dd>{_safe(_volume_acceleration(row))}</dd></div>
        <div><dt>Finviz</dt><dd>{_finviz_link(row.get('symbol', ''))}</dd></div>
      </dl>
      <div class="screened-in">
        <h3>Why it screened in</h3>
        {_why_screened_in(row)}
      </div>
    </article>
    """


def _picks_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<p class='empty'>No picks available yet.</p>"
    body = "".join(
        "<tr data-pick-slot='candidate'>"
        f"<td><strong>{_safe(row.get('symbol', ''))}</strong><br>{_finviz_link(row.get('symbol', ''))}</td>"
        f"<td>{_safe(row.get('company_name', ''))}</td>"
        f"<td>{_format_market_cap(row.get('market_cap', ''))}</td>"
        f"<td>{_format_currency(row.get('latest_close', ''))}</td>"
        f"<td>{_safe(row.get('score', ''))}</td>"
        f"<td><span class='badge signal'>{_safe(_candidate_signal(row))}</span></td>"
        f"<td><span class='badge selection'>{_safe(row.get('selection_label', 'Strict Pass'))}</span></td>"
        f"<td>{_safe(row.get('guardrail', 'Insufficient Data'))}</td>"
        f"<td>{_safe(row.get('quick_ratio', 'unavailable'))}</td>"
        f"<td>{_safe(row.get('net_cash_debt', 'unavailable'))}</td>"
        f"<td>{_safe(row.get('share_change_percent', 'unavailable'))}</td>"
        f"<td>{_safe(row.get('evidence_agreement', ''))}</td>"
        f"<td>{_safe(row.get('top_bullish_signal', 'none'))}</td>"
        f"<td>{_safe(row.get('top_caution_signal', 'none'))}</td>"
        f"<td>{_safe(row.get('rsi_14', ''))}</td>"
        f"<td>{_format_percent(row.get('low_proximity', ''))}</td>"
        f"<td>{_safe(_bollinger_status(row))}</td>"
        f"<td>{_safe(_volume_acceleration(row))}</td>"
        f"<td>{_why_screened_in(row)}</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<table class='picks-table'><thead><tr><th>Ticker</th><th>Company</th><th>Market Cap</th>"
        "<th>Price</th><th>GreenRock Score</th><th>Signal</th><th>Selection</th><th>Guardrail</th><th>Quick Ratio</th>"
        "<th>Net Cash / Debt</th><th>Share Change</th><th>Evidence Agreement</th><th>Top Bullish Signal</th><th>Top Caution Signal</th>"
        "<th>RSI</th><th>52-week Low Distance</th>"
        "<th>Bollinger Band Status</th><th>Volume Acceleration</th><th>Why It Screened In</th></tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def _scan_metadata(scan) -> dict[str, str]:
    timestamp = "unknown"
    stamp = scan.scan_id.rsplit("-", maxsplit=1)[-1]
    try:
        timestamp = datetime.strptime(stamp, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    return {"timestamp": timestamp}


def _scan_filter_values(query: dict[str, list[str]]) -> dict[str, str]:
    return {
        "min_score": _first(query, "min_score"),
        "min_confidence": _first(query, "min_confidence"),
        "min_evidence": _first(query, "min_evidence"),
        "priority": _first(query, "priority"),
        "guardrail": _first(query, "guardrail"),
    }


def _filter_scan_rows(rows: tuple[dict[str, str], ...], filters: dict[str, str]) -> tuple[dict[str, str], ...]:
    def keep(row: dict[str, str]) -> bool:
        if filters["min_score"] and _as_float(row.get("greenrock_score", "")) < _as_float(filters["min_score"]):
            return False
        if filters["min_confidence"] and _as_float(row.get("greenrock_confidence", "")) < _as_float(filters["min_confidence"]):
            return False
        if filters["min_evidence"] and _as_float(row.get("evidence_agreement", "")) < _as_float(filters["min_evidence"]):
            return False
        if filters["priority"] and row.get("research_priority", "") != filters["priority"]:
            return False
        if filters["guardrail"] and row.get("fundamental_guardrail", "") != filters["guardrail"]:
            return False
        return True

    return tuple(row for row in rows if keep(row))


def _as_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _scan_filter_form(filters: dict[str, str]) -> str:
    priority_options = _select_options(
        ("", "Immediate Review", "This Week", "Interesting", "Monitor", "Ignore"),
        filters["priority"],
    )
    guardrail_options = _select_options(
        ("", "Supportive", "Mixed", "Caution", "Incomplete"),
        filters["guardrail"],
    )
    return f"""
    <form method="get" action="/greenrock/scanner" class="scanner-filter-form">
      <label>Minimum GreenRock Score<input name="min_score" type="number" min="0" max="100" step="1" value="{_safe(filters['min_score'])}"></label>
      <label>Minimum Confidence<input name="min_confidence" type="number" min="0" max="100" step="1" value="{_safe(filters['min_confidence'])}"></label>
      <label>Minimum Evidence Agreement<input name="min_evidence" type="number" min="0" max="100" step="1" value="{_safe(filters['min_evidence'])}"></label>
      <label>Research Priority<select name="priority">{priority_options}</select></label>
      <label>Guardrail label<select name="guardrail">{guardrail_options}</select></label>
      <button type="submit">Apply Filters</button>
      <a class="button secondary" href="/greenrock/scanner">Clear</a>
    </form>
    """


def _select_options(values: tuple[str, ...], selected: str) -> str:
    return "".join(
        f"<option value='{_safe(value)}' {'selected' if value == selected else ''}>{_safe(value or 'Any')}</option>"
        for value in values
    )


def _watchlist_tickers(output_dir: Path, list_key: str) -> tuple[str, ...]:
    path = placement_path(output_dir, list_key)
    if not path.exists():
        return ()
    tickers: list[str] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            ticker = row.get("ticker", "").strip().upper()
            if ticker:
                tickers.append(ticker)
    return tuple(dict.fromkeys(tickers))


def _promotion_metadata_by_ticker(output_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    metadata: dict[tuple[str, str], dict[str, str]] = {}
    for row in load_promotion_metadata(output_dir):
        ticker = row.get("ticker", "").upper()
        destination = row.get("destination_list", "")
        if ticker and destination:
            metadata[(destination, ticker)] = row
    return metadata


def _watchlist_overview_card(
    output_dir: Path,
    list_key: str,
    label: str,
    metadata: dict[tuple[str, str], dict[str, str]],
) -> str:
    tickers = _watchlist_tickers(output_dir, list_key)
    rows = "".join(
        _watchlist_ticker_row(list_key, ticker, metadata.get((list_key, ticker)))
        for ticker in tickers
    )
    if not rows:
        rows = "<tr><td colspan='5' class='empty'>No tickers saved yet.</td></tr>"
    return f"""
    <section class="panel watchlist-card">
      <div class="section-head">
        <h2>{_safe(label)}</h2>
        <span class="badge">{len(tickers)} tickers</span>
      </div>
      <table>
        <thead><tr><th>Ticker</th><th>Finviz</th><th>Source</th><th>Latest Promoted</th><th>Actions</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def _universe_provider_card(provider) -> str:
    color = "approved" if provider.health == "healthy" else "pending"
    return f"""
    <section class="panel watchlist-card">
      <div class="section-head">
        <h2>{_safe(provider_label(provider.name))}</h2>
        <span class="badge {color}">{_safe(provider.health)}</span>
      </div>
      <dl class="detail-list">
        <div><dt>Ticker Count</dt><dd>{provider.ticker_count}</dd></div>
        <div><dt>Status</dt><dd>{_safe(provider.status)}</dd></div>
        <div><dt>Last Refresh</dt><dd>{_safe(provider.last_refresh or 'not refreshed')}</dd></div>
        <div><dt>Source</dt><dd class="path">{_safe(provider.source)}</dd></div>
      </dl>
    </section>
    """


def _master_universe_row(row) -> str:
    return (
        "<tr>"
        f"<td><strong>{_safe(row.ticker)}</strong><br>{_finviz_link(row.ticker)}</td>"
        f"<td>{_safe(', '.join(provider_label(name) for name in row.provider_membership))}</td>"
        f"<td>{_safe(row.market_cap_bucket.replace('_', ' ').title())}</td>"
        f"<td>{_safe(row.market_archetype)}</td>"
        f"<td>{_safe(row.sector or '-')}</td>"
        f"<td>{_safe(row.health)}</td>"
        "</tr>"
    )


def _filter_master_universe_rows(rows, filters: dict[str, str]):
    provider = filters.get("provider", "").strip()
    bucket = filters.get("bucket", "").strip()
    archetype = filters.get("archetype", "").strip()
    search = filters.get("q", "").strip().upper()
    filtered = []
    for row in rows:
        if provider and provider not in row.provider_membership:
            continue
        if bucket and bucket != row.market_cap_bucket:
            continue
        if archetype and archetype != row.market_archetype:
            continue
        if search and search not in row.ticker:
            continue
        filtered.append(row)
    return tuple(filtered)


def _universe_filter_form(filters: dict[str, str], master) -> str:
    providers = tuple(provider.name for provider in master.providers)
    buckets = tuple(sorted({row.market_cap_bucket for row in master.rows if row.market_cap_bucket}))
    archetypes = tuple(sorted({row.market_archetype for row in master.rows if row.market_archetype}))
    return f"""
    <form method="get" action="/greenrock/universe" class="scanner-filter-form">
      <label>Ticker Search<input name="q" value="{_safe(filters.get('q', ''))}" placeholder="Ticker"></label>
      <label>Provider<select name="provider">{_select_options(('',) + providers, filters.get('provider', ''))}</select></label>
      <label>Market-Cap Bucket<select name="bucket">{_select_options(('',) + buckets, filters.get('bucket', ''))}</select></label>
      <label>Archetype<select name="archetype">{_select_options(('',) + archetypes, filters.get('archetype', ''))}</select></label>
      <button type="submit">Apply Filters</button>
      <a class="button secondary" href="/greenrock/universe">Clear</a>
    </form>
    """


def _pagination_links(base_path: str, filters: dict[str, str], page: int, total_pages: int) -> str:
    if total_pages <= 1:
        return ""
    previous_link = _page_link(base_path, filters, page - 1, "Previous") if page > 1 else "<span class='button disabled'>Previous</span>"
    next_link = _page_link(base_path, filters, page + 1, "Next") if page < total_pages else "<span class='button disabled'>Next</span>"
    return f"<div class='action-row'>{previous_link}<span class='subtle'>Page {page} of {total_pages}</span>{next_link}</div>"


def _page_link(base_path: str, filters: dict[str, str], page: int, label: str) -> str:
    query = {key: value for key, value in filters.items() if value and key != "status"}
    query["page"] = str(page)
    return f"<a class='button secondary' href='{base_path}?{urlencode(query)}'>{_safe(label)}</a>"


def _provider_failure_summary(failures: tuple[dict[str, str], ...]) -> str:
    if not failures:
        return "<p class='subtle'>No provider failures recorded for the latest successful scan.</p>"
    rows = "".join(
        "<tr>"
        f"<td><strong>{_safe(row.get('ticker', ''))}</strong></td>"
        f"<td>{_safe(row.get('failure_reason', ''))}</td>"
        f"<td>{_safe(row.get('provider_membership', ''))}</td>"
        f"<td>{_safe(row.get('suggested_action', 'review'))}</td>"
        "</tr>"
        for row in failures[:20]
    )
    extra = f"<p class='subtle'>Showing 20 of {len(failures)} provider failures. Use CLI universe health for the full list.</p>" if len(failures) > 20 else ""
    return f"""
    {extra}
    <table>
      <thead><tr><th>Ticker</th><th>Failure Reason</th><th>Membership</th><th>Suggested Action</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


def _market_pulse_actions(output_dir: Path, scan_id: str) -> str:
    staged_rows = load_staged_candidates(output_dir)
    market_pulse_rows = tuple(row for row in staged_rows if row.get("source_scan_id") == scan_id)
    report_button = (
        '<a class="button" href="/greenrock/market-pulse/report/confirm">Generate Draft From Staged Market Pulse</a>'
        if market_pulse_rows
        else ""
    )
    report_note = (
        f"<p class='subtle'>{len(market_pulse_rows)} staged candidate(s) from this scan are ready for the normal approval-gated draft workflow.</p>"
        if market_pulse_rows
        else "<p class='subtle'>Stage candidates first, then generate the approval-gated draft from staging.</p>"
    )
    return f"""
    <section class="panel warning-panel">
      <div class="section-head">
        <h2>Market Pulse To Report</h2>
        <span class="badge">Local approval-gated workflow</span>
      </div>
      <p>Stage top candidates from the latest scan, then create the normal draft report from staging. No PDF export happens until a human approves.</p>
      <p class="subtle">Atlas Analyst slate stages one leader from each available archetype, then fills the remaining report slate by rank.</p>
      <div class="action-row">
        <a class="button" href="/greenrock/market-pulse/stage/confirm">Stage Top Market Pulse Candidates</a>
        <a class="button" href="/greenrock/market-pulse/stage/confirm?slate=analyst">Generate Atlas Analyst Report Slate</a>
        {report_button}
      </div>
      {report_note}
    </section>
    """


def _memory_pow_card(output_dir: Path) -> str:
    movers = memory_movers(output_dir, limit=1)
    leader_html = _new_archetype_leader_line(output_dir)
    return f"""
    <section class="panel warning-panel">
      <div class="section-head">
        <h2>Atlas Memory: What Changed</h2>
        <span class="badge">POW</span>
      </div>
      <section class="board-meta">
        {_pow_metric("Biggest Rank Improver", _first_mover(movers["rank_improvers"], "rank"))}
        {_pow_metric("Biggest Score Improver", _first_mover(movers["score_improvers"], "score"))}
        {_pow_metric("Biggest Confidence Improver", _first_mover(movers["confidence_improvers"], "confidence"))}
        {_pow_metric("Biggest Evidence Improver", _first_mover(movers["evidence_improvers"], "evidence"))}
        {_pow_metric("Biggest Deterioration", _first_mover(movers["deteriorations"], "deterioration"))}
        {_pow_metric("New Archetype Leader", leader_html)}
      </section>
    </section>
    """


def _pow_metric(label: str, value: str) -> str:
    return _attention_card("neutral", value or "none", label, "Atlas Memory")


def _first_mover(rows, mode: str) -> str:
    if not rows:
        return "none"
    item = rows[0]
    if mode == "rank":
        return f"{item.ticker} {item.previous.get('rank', '')}->{item.current.get('rank', '')}"
    if mode == "score":
        return f"{item.ticker} {item.previous.get('greenrock_score', '')}->{item.current.get('greenrock_score', '')}"
    if mode == "confidence":
        return f"{item.ticker} {item.previous.get('confidence', '')}->{item.current.get('confidence', '')}"
    if mode == "evidence":
        return f"{item.ticker} {item.previous.get('evidence_agreement', '')}->{item.current.get('evidence_agreement', '')}"
    return f"{item.ticker} {movement_symbol(item)}"


def _new_archetype_leader_line(output_dir: Path) -> str:
    rows = load_memory_rows(output_dir)
    scan_ids = sorted({row["scan_id"] for row in rows}, reverse=True)
    if len(scan_ids) < 2:
        return "none"
    latest, previous = scan_ids[0], scan_ids[1]
    latest_leaders = _leaders_by_archetype(tuple(row for row in rows if row["scan_id"] == latest))
    previous_leaders = _leaders_by_archetype(tuple(row for row in rows if row["scan_id"] == previous))
    for archetype, leader in latest_leaders.items():
        old = previous_leaders.get(archetype, {}).get("ticker", "")
        if old != leader.get("ticker", ""):
            return f"{archetype}: {leader.get('ticker', '')}"
    return "none"


def _market_pulse_memory_panel(output_dir: Path) -> str:
    movers = memory_movers(output_dir)
    blocks = "".join(_memory_mover_block(title, rows) for title, rows in (
        ("Top Rank Improvers", movers["rank_improvers"]),
        ("Top Score Improvers", movers["score_improvers"]),
        ("Top Confidence Improvers", movers["confidence_improvers"]),
        ("Top Deteriorations", movers["deteriorations"]),
    ))
    if not blocks:
        blocks = "<p class='subtle'>No prior scan movement is available yet. Run another Market Pulse scan to build comparison history.</p>"
    return f"""
    <section class="panel">
      <div class="section-head">
        <h2>What Changed Since Last Scan</h2>
        <span class="subtle">Atlas Memory local scan history</span>
      </div>
      {_new_archetype_leaders(output_dir)}
      <div class="watchlist-grid">{blocks}</div>
    </section>
    """


def _memory_mover_block(title: str, rows) -> str:
    if not rows:
        body = "<p class='subtle'>No movement yet.</p>"
    else:
        body = "".join(
            f"<li><strong>{_safe(item.ticker)}</strong> {_safe(movement_symbol(item))} "
            f"rank {_safe(item.previous.get('rank', ''))}->{_safe(item.current.get('rank', ''))}; "
            f"score {_safe(item.previous.get('greenrock_score', ''))}->{_safe(item.current.get('greenrock_score', ''))}; "
            f"confidence {_safe(item.previous.get('confidence', ''))}->{_safe(item.current.get('confidence', ''))}</li>"
            for item in rows
        )
        body = f"<ul class='compact-list'>{body}</ul>"
    return f"<article class='watchlist-card'><h3>{_safe(title)}</h3>{body}</article>"


def _new_archetype_leaders(output_dir: Path) -> str:
    changes = _new_archetype_leader_items(output_dir)
    body = "".join(f"<li>{_safe(change)}</li>" for change in changes) or "<li>No new archetype leaders.</li>"
    return f"<section class='panel inner-panel'><h3>New Archetype Leaders</h3><ul class='compact-list'>{body}</ul></section>"


def _new_archetype_leader_items(output_dir: Path) -> tuple[str, ...]:
    rows = load_memory_rows(output_dir)
    scan_ids = sorted({row["scan_id"] for row in rows}, reverse=True)
    if len(scan_ids) < 2:
        return ()
    latest, previous = scan_ids[0], scan_ids[1]
    latest_leaders = _leaders_by_archetype(tuple(row for row in rows if row["scan_id"] == latest))
    previous_leaders = _leaders_by_archetype(tuple(row for row in rows if row["scan_id"] == previous))
    changes = tuple(
        f"{archetype}: {leader.get('ticker', '')} replaced {previous_leaders.get(archetype, {}).get('ticker', 'none')}"
        for archetype, leader in latest_leaders.items()
        if previous_leaders.get(archetype, {}).get("ticker") != leader.get("ticker")
    )
    return changes


def _leaders_by_archetype(rows: tuple[dict[str, str], ...]) -> dict[str, dict[str, str]]:
    leaders: dict[str, dict[str, str]] = {}
    for row in sorted(rows, key=lambda item: _parse_int(item.get("rank", "")) or 999999):
        archetype = row.get("market_archetype", "")
        if archetype and archetype not in leaders:
            leaders[archetype] = row
    return leaders


def _score_memory_panel(output_dir: Path, ticker: str) -> str:
    history = ticker_history(output_dir, ticker)
    if not history:
        return """
        <section class="panel warning-panel">
          <h2>Atlas Memory Snapshot</h2>
          <p>Run a Market Pulse scan to begin tracking this ticker.</p>
        </section>
        """
    latest = history[0]
    comparison = compare_ticker(output_dir, ticker)
    prior = comparison.previous if comparison else {}
    return f"""
    <section class="panel warning-panel">
      <div class="section-head">
        <h2>Atlas Memory Snapshot</h2>
        <span class="badge">Local scan movement</span>
      </div>
      <section class="board-meta">
        {_attention_card("neutral", latest.get("rank", "-"), "Latest Rank", latest.get("scan_id", ""))}
        {_attention_card("neutral", prior.get("rank", "-") if prior else "-", "Prior Rank", prior.get("scan_id", "") if prior else "No prior scan")}
        {_attention_card("neutral", _change_value(comparison.rank_change if comparison else None, "rank"), "Rank Movement", "lower rank is stronger")}
        {_attention_card("neutral", _change_value(comparison.score_change if comparison else None, "score"), "Score Movement", f"prior {prior.get('greenrock_score', '-') if prior else '-'}")}
        {_attention_card("neutral", _change_value(comparison.confidence_change if comparison else None, "confidence"), "Confidence Movement", f"prior {prior.get('confidence', '-') if prior else '-'}")}
        {_attention_card("neutral", _change_value(comparison.evidence_change if comparison else None, "evidence"), "Evidence Movement", f"prior {prior.get('evidence_agreement', '-') if prior else '-'}")}
        {_attention_card("neutral", _priority_change(comparison), "Priority Change", latest.get("research_priority", "-"))}
        {_attention_card("neutral", latest.get("scan_timestamp", "-"), "Last Seen Scan", latest.get("scan_id", ""))}
      </section>
      <p>{_safe(movement_explanation(comparison))}</p>
    </section>
    """


def _score_report_history_panel(ticker: str) -> str:
    rows = _report_history_for_ticker(ticker)
    if not rows:
        return f"""
        <section class="panel">
          <div class="section-head"><h2>Report History</h2><span class="subtle">{_safe(ticker)}</span></div>
          <p class="subtle">No prior indexed GreenRock reports contain this ticker yet.</p>
        </section>
        """
    latest = rows[0]
    role = "featured leader" if ticker.upper() in latest.get("featured", "").split(",") else "remaining candidate"
    return f"""
    <section class="panel">
      <div class="section-head"><h2>Report History</h2><span class="subtle">{len(rows)} indexed report(s)</span></div>
      <section class="board-meta">
        {_attention_card("neutral", latest["report_date"], "Most Recent Report", latest["title"])}
        {_attention_card("neutral", role, "Candidate Role", latest["report_type"])}
        {_attention_card("green" if latest["approval_status"] == "approved" else "yellow", latest["approval_status"], "Approval / Final Status", latest["pdf_status"])}
        {_attention_card("neutral", latest["run_id"], "Report Run", "local provenance")}
      </section>
      <p><a class="button secondary" href="/greenrock/reports/{quote(latest['run_id'])}/review">Open Report</a> <a class="button secondary" href="/reports?ticker={quote(ticker.upper())}">View All Reports for Ticker</a></p>
    </section>
    """


def _change_value(value, mode: str) -> str:
    if value is None:
        return "-"
    if mode == "rank":
        if value < 0:
            return f"improved {abs(value)}"
        if value > 0:
            return f"deteriorated {value}"
        return "unchanged"
    if value > 0:
        return f"+{value:.2f}"
    if value < 0:
        return f"{value:.2f}"
    return "unchanged"


def _priority_change(comparison) -> str:
    if comparison is None:
        return "-"
    previous = comparison.previous.get("research_priority", "")
    current = comparison.current.get("research_priority", "")
    return f"{previous}->{current}" if previous != current else "unchanged"


def _review_path_from_status(status: str) -> str | None:
    marker = "Review at "
    if marker not in status:
        return None
    tail = status.split(marker, maxsplit=1)[1].strip()
    return tail.rstrip(".") or None


def _pulse_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    if not normalized.get("market_archetype", "").strip():
        normalized["market_archetype"] = classify_market_archetype(
            normalized.get("symbol", ""),
            _as_float_or_none(normalized.get("market_cap", "")),
            tuple(item for item in normalized.get("universe_membership", "").split("|") if item),
        )
    return normalized


def _as_float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_pulse_section(archetype: str, rows: tuple[dict[str, str], ...], scan_id: str) -> str:
    body = "".join(
        "<tr>"
        f"<td>{_safe(row.get('rank', ''))}</td>"
        f"<td><strong>{_safe(row.get('symbol', ''))}</strong><br>{_finviz_link(row.get('symbol', ''))}</td>"
        f"<td>{_safe(row.get('greenrock_score', ''))}{_pulse_prior_value(row, 'greenrock_score')}</td>"
        f"<td>{_safe(row.get('greenrock_confidence', ''))}{_pulse_prior_value(row, 'confidence')}</td>"
        f"<td>{_safe(row.get('evidence_agreement', ''))}</td>"
        f"<td>{_safe(row.get('research_priority', ''))}</td>"
        f"<td>{_pulse_movement_cell(row)}</td>"
        f"<td>{_staging_add_form(row.get('symbol', ''), 'latest_scan', compact=True)}</td>"
        "</tr>"
        for row in rows
    )
    if not body:
        body = f"<tr><td colspan='8' class='empty'>No scored names in this archetype: {_safe(archetype)}.</td></tr>"
    return f"""
    <section class="panel picks-panel">
      <div class="section-head">
        <h2>{_safe(archetype)}</h2>
        <span class="subtle">Latest scan: {_safe(scan_id)}</span>
      </div>
      <table>
        <thead><tr><th>Rank</th><th>Ticker</th><th>Score</th><th>Confidence</th><th>Evidence</th><th>Priority</th><th>Movement</th><th>Stage</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _pulse_movement_cell(row: dict[str, str]) -> str:
    settings = get_settings()
    comparison = compare_ticker(settings.output_dir, row.get("symbol", ""))
    symbol = movement_symbol(comparison)
    if comparison is None:
        return f"{_safe(symbol)} unchanged<br><span class='subtle'>no prior scan</span>"
    return (
        f"{_safe(symbol)} rank {comparison.previous.get('rank', '')}->{comparison.current.get('rank', '')}"
        f"<br><span class='subtle'>prior score {comparison.previous.get('greenrock_score', '')}; prior confidence {comparison.previous.get('confidence', '')}</span>"
    )


def _pulse_prior_value(row: dict[str, str], field: str) -> str:
    settings = get_settings()
    comparison = compare_ticker(settings.output_dir, row.get("symbol", ""))
    if comparison is None:
        return ""
    value = comparison.previous.get(field, "")
    return f"<br><span class='subtle'>prior {value}</span>" if value else ""


def _watchlist_ticker_row(list_key: str, ticker: str, metadata: dict[str, str] | None) -> str:
    source = f"scan:{metadata.get('scan_id', '')}" if metadata else "manual/local"
    promoted_at = metadata.get("promoted_at", "") if metadata else ""
    return (
        "<tr>"
        f"<td><strong>{_safe(ticker)}</strong></td>"
        f"<td>{_finviz_link(ticker)}</td>"
        f"<td>{_safe(source)}</td>"
        f"<td>{_safe(promoted_at or 'not recorded')}</td>"
        f"<td>{_watchlist_remove_form(list_key, ticker)}</td>"
        "</tr>"
    )


def _watchlist_remove_form(list_key: str, ticker: str) -> str:
    return f"""
    <form method="post" action="/greenrock/watchlists/remove" onsubmit="return confirm('Remove this ticker from the selected GreenRock watchlist?');">
      <input type="hidden" name="list_key" value="{_safe(list_key)}">
      <input type="hidden" name="ticker" value="{_safe(ticker)}">
      <button type="submit" class="secondary">Remove</button>
    </form>
    """


def _staging_readiness_card(item) -> str:
    target = str(item.target) if item.target is not None else "Review"
    color = {
        "Ready": "green",
        "Underfilled": "yellow",
        "Overfilled": "red",
        "Needs Review": "neutral",
    }.get(item.status, "neutral")
    guidance = ""
    if item.status == "Overfilled" and item.target is not None:
        guidance = (
            f"<p class='subtle'>Select final {item.target}. "
            f"{_trim_bucket_form(item.bucket)}</p>"
        )
    elif item.status == "Underfilled" and item.target is not None:
        guidance = "<p class='subtle'>Add staged candidates or generate with underfilled warnings.</p>"
    return f"""
    <article class="attention-card {color}">
      <strong>{item.count}/{_safe(target)}</strong>
      <h2>{_safe(item.label)}</h2>
      <p>{_safe(item.status)}</p>
      {guidance}
    </article>
    """


def _staging_analytics_card(status) -> str:
    color = "green" if status.complete else "yellow"
    detail = "All staged candidates have analytics." if status.complete else f"{status.missing_count} candidate(s) missing analytics."
    return _attention_card(color, status.label, "Analytics Completeness", detail)


def _trim_bucket_form(bucket: str) -> str:
    return f"""
    <form method="post" action="/greenrock/staging/trim" class="inline-trim-form" onsubmit="return confirm('Trim this bucket to the top ranked staged candidates?');">
      <input type="hidden" name="bucket" value="{_safe(bucket)}">
      <button type="submit" class="secondary">Trim to Top Ranked</button>
    </form>
    """


def _staging_generation_status(warnings: tuple[str, ...]) -> str:
    if not warnings:
        return "<p class='subtle'>Staging targets are ready for an approval-gated draft.</p>"
    items = "".join(f"<li>{_safe(warning)}</li>" for warning in warnings)
    return f"""
    <div class="setup-box">
      <p>Readiness warnings will be included in the draft if generated.</p>
      <ul class="compact-list">{items}</ul>
    </div>
    """


def _staging_enrichment_status(status) -> str:
    if status.complete:
        return "<p class='subtle'>Complete: staged candidates have Score, Confidence, Evidence Agreement, Guardrail, Research Priority, and signal fields.</p>"
    tickers = ", ".join(status.missing_tickers) if status.missing_tickers else "none"
    return f"""
    <div class="setup-box">
      <p>Missing analytics: {status.missing_count} of {status.total} staged candidate(s).</p>
      <p class="subtle">Provider required when fields are missing. Configure real market data or run <code>atlas greenrock staging enrich</code>.</p>
      <p class="path">{_safe(tickers)}</p>
    </div>
    """


def _staging_add_form(ticker: str = "", source_list: str = "manual", compact: bool = False) -> str:
    bucket_options = _staging_bucket_options("research")
    compact_class = " staging-add-form compact-add" if compact else " staging-add-form"
    return f"""
    <form method="post" action="/greenrock/staging/add" class="{compact_class}">
      <input name="ticker" value="{_safe(ticker)}" placeholder="Ticker" required>
      <input type="hidden" name="source_list" value="{_safe(source_list)}">
      <select name="bucket">{bucket_options}</select>
      <input name="notes" placeholder="Operator notes">
      <button type="submit">Stage</button>
    </form>
    """


def _staging_bucket_options(selected: str) -> str:
    return "".join(
        f"<option value='{_safe(bucket)}' {'selected' if bucket == selected else ''}>{_safe(label)}</option>"
        for bucket, label in STAGING_BUCKET_LABELS.items()
    )


def _staging_bucket_section(bucket: str, label: str, rows: tuple[dict[str, str], ...]) -> str:
    bucket_rows = tuple(row for row in rows if row.get("staged_bucket") == bucket)
    target = STAGING_BUCKET_TARGETS.get(bucket)
    target_copy = f"Target: {target}" if target is not None else "Research review bucket"
    body = "".join(_staging_candidate_row(row) for row in bucket_rows)
    if not body:
        body = "<tr><td colspan='11' class='empty'>No staged tickers in this bucket.</td></tr>"
    return f"""
    <section class="panel staging-bucket">
      <div class="section-head">
        <h2>{_safe(label)}</h2>
        <span class="badge">{len(bucket_rows)} staged | {_safe(target_copy)}</span>
      </div>
      <table class="staging-table">
        <thead><tr><th>Ticker</th><th>Score</th><th>Confidence</th><th>Evidence</th><th>Guardrail</th><th>Priority</th><th>Top Bullish</th><th>Top Caution</th><th>Source</th><th>Notes</th><th>Actions</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _staging_candidate_row(row: dict[str, str]) -> str:
    ticker = row.get("ticker", "")
    source = _safe(row.get("source_list", "") or "manual")
    if row.get("source_scan_id"):
        source = f"{source}<br><span class='subtle'>{_safe(row.get('source_scan_id', ''))}</span>"
    missing_badge = "<br><span class='badge pending'>Missing analytics</span>" if row_missing_analytics(row) else "<br><span class='badge approved'>Analytics complete</span>"
    return (
        "<tr>"
        f"<td><strong>{_safe(ticker)}</strong><br>{_finviz_link(ticker)}{missing_badge}</td>"
        f"<td>{_safe(row.get('greenrock_score', ''))}</td>"
        f"<td>{_safe(row.get('confidence', ''))}</td>"
        f"<td>{_safe(row.get('evidence_agreement', ''))}</td>"
        f"<td>{_safe(row.get('guardrail', ''))}</td>"
        f"<td>{_safe(row.get('research_priority', ''))}</td>"
        f"<td>{_safe(row.get('top_bullish_signal', ''))}</td>"
        f"<td>{_safe(row.get('top_caution_signal', ''))}</td>"
        f"<td>{source}</td>"
        f"<td>{_staging_notes_form(row)}</td>"
        f"<td>{_staging_action_forms(row)}</td>"
        "</tr>"
    )


def _staging_notes_form(row: dict[str, str]) -> str:
    return f"""
    <form method="post" action="/greenrock/staging/notes" class="staging-notes-form">
      <input type="hidden" name="ticker" value="{_safe(row.get('ticker', ''))}">
      <textarea name="notes" placeholder="Notes">{_safe(row.get('notes', ''))}</textarea>
      <button type="submit" class="secondary">Save</button>
    </form>
    """


def _staging_action_forms(row: dict[str, str]) -> str:
    ticker = row.get("ticker", "")
    bucket_options = _staging_bucket_options(row.get("staged_bucket", "research"))
    return f"""
    <div class="staging-actions">
      <form method="post" action="/greenrock/staging/move">
        <input type="hidden" name="ticker" value="{_safe(ticker)}">
        <select name="bucket">{bucket_options}</select>
        <button type="submit">Move</button>
      </form>
      <form method="post" action="/greenrock/staging/remove" onsubmit="return confirm('Remove this ticker from staging?');">
        <input type="hidden" name="ticker" value="{_safe(ticker)}">
        <button type="submit" class="secondary">Remove</button>
      </form>
    </div>
    """


def _staging_source_sections(output_dir: Path) -> str:
    watchlist_rows = []
    for list_key, label in GREENROCK_PLACEMENT_LABELS.items():
        for ticker in _watchlist_tickers(output_dir, list_key):
            watchlist_rows.append((ticker, label, list_key))
    watchlist_body = "".join(
        "<tr>"
        f"<td><strong>{_safe(ticker)}</strong><br>{_finviz_link(ticker)}</td>"
        f"<td>{_safe(label)}</td>"
        f"<td>{_staging_add_form(ticker, list_key, compact=True)}</td>"
        "</tr>"
        for ticker, label, list_key in watchlist_rows[:80]
    )
    if not watchlist_body:
        watchlist_body = "<tr><td colspan='3' class='empty'>No watchlist tickers available yet.</td></tr>"

    scan = latest_scan(output_dir)
    scan_body = ""
    if scan:
        scan_body = "".join(
            "<tr>"
            f"<td><strong>{_safe(row.get('symbol', ''))}</strong><br>{_finviz_link(row.get('symbol', ''))}</td>"
            f"<td>{_safe(row.get('greenrock_score', ''))}</td>"
            f"<td>{_safe(row.get('greenrock_confidence', ''))}</td>"
            f"<td>{_safe(row.get('evidence_agreement', ''))}</td>"
            f"<td>{_safe(row.get('fundamental_guardrail', ''))}</td>"
            f"<td>{_staging_add_form(row.get('symbol', ''), 'latest_scan', compact=True)}</td>"
            "</tr>"
            for row in scan.rows[:25]
        )
    if not scan_body:
        scan_body = "<tr><td colspan='6' class='empty'>No latest population scan available yet.</td></tr>"

    scan_label = f"Latest Population Scan: {scan.scan_id}" if scan else "Latest Population Scan"
    return f"""
    <section class="candidate-grid">
      <div class="panel picks-panel">
        <div class="section-head">
          <h2>Stage From Watchlists</h2>
          <span class="subtle">Promoted and curated lists</span>
        </div>
        <table><thead><tr><th>Ticker</th><th>Source List</th><th>Stage</th></tr></thead><tbody>{watchlist_body}</tbody></table>
      </div>
      <div class="panel picks-panel">
        <div class="section-head">
          <h2>{_safe(scan_label)}</h2>
          <span class="subtle">Top ranked scan rows</span>
        </div>
        <table><thead><tr><th>Ticker</th><th>Score</th><th>Confidence</th><th>Evidence</th><th>Guardrail</th><th>Stage</th></tr></thead><tbody>{scan_body}</tbody></table>
      </div>
    </section>
    """


def _scan_results_table(rows, scan_id: str = "", batch: bool = False) -> str:
    if not rows:
        return "<p class='empty'>No scan rows available yet.</p>"
    options = "".join(
        f"<option value='{_safe(key)}'>{_safe(label)}</option>"
        for key, label in GREENROCK_PLACEMENT_LABELS.items()
    )
    body = "".join(_scan_results_row(row, scan_id, options, batch) for row in rows)
    select_column = "<th>Select</th>" if batch else ""
    promote_column = "" if batch else "<th>Promote</th>"
    table = (
        f"<table class='picks-table'><thead><tr>{select_column}<th>Rank</th><th>Ticker</th><th>Company</th>"
        "<th>Archetype</th><th>Score</th><th>Confidence</th><th>Evidence Agreement</th><th>Guardrail</th><th>Priority</th>"
        f"<th>Top Bullish Signal</th><th>Top Caution Signal</th><th>Data Quality Warnings</th>{promote_column}</tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )
    if not batch:
        return table
    bucket_options = _staging_bucket_options("research")
    return f"""
    <form method="post" action="/greenrock/scanner/promote-batch" class="scan-review-form" onsubmit="return confirm('Apply the selected scanner action to these tickers?');">
      <input type="hidden" name="scan_id" value="{_safe(scan_id)}">
      <div class="promotion-bar">
        <span class="subtle">Promote these tickers to [list]?</span>
        <select name="list_key">{options}</select>
        <button type="submit">Promote Selected</button>
        <span class="subtle">Stage selected candidates as</span>
        <select name="bucket">{bucket_options}</select>
        <button type="submit" formaction="/greenrock/scanner/stage-batch">Stage Selected Candidates</button>
      </div>
      {table}
    </form>
    """


def _scan_results_row(row: dict[str, str], scan_id: str, options: str, batch: bool) -> str:
    select_cell = f"<td><input type='checkbox' name='tickers' value='{_safe(row.get('symbol', ''))}'></td>" if batch else ""
    promote_cell = ""
    if not batch:
        promote_cell = (
            "<td>"
            "<form method='post' action='/greenrock/scanner/promote' class='inline-promote-form'>"
            f"<input type='hidden' name='scan_id' value='{_safe(scan_id)}'>"
            f"<input type='hidden' name='ticker' value='{_safe(row.get('symbol', ''))}'>"
            f"<select name='list_key'>{options}</select>"
            "<button type='submit'>Promote</button>"
            "</form>"
            "</td>"
        )
    return (
        "<tr>"
        f"{select_cell}"
        f"<td>{_safe(row.get('rank', ''))}</td>"
        f"<td><strong>{_safe(row.get('symbol', ''))}</strong><br>{_finviz_link(row.get('symbol', ''))}</td>"
        f"<td>{_safe(row.get('company_name', ''))}</td>"
        f"<td>{_safe(row.get('market_archetype', ''))}</td>"
        f"<td>{_safe(row.get('greenrock_score', ''))}</td>"
        f"<td>{_safe(row.get('greenrock_confidence', ''))}</td>"
        f"<td>{_safe(row.get('evidence_agreement', ''))}</td>"
        f"<td>{_safe(row.get('fundamental_guardrail', ''))}</td>"
        f"<td>{_safe(row.get('research_priority', ''))}</td>"
        f"<td>{_safe(row.get('top_bullish_signal', ''))}</td>"
        f"<td>{_safe(row.get('top_caution_signal', ''))}</td>"
        f"<td>{_safe(row.get('data_quality_warnings', ''))}</td>"
        f"{promote_cell}"
        "</tr>"
    )


def _score_preview_panel(preview) -> str:
    candidate = preview.candidate
    indicators = candidate.indicators
    warnings = preview.data_quality_warnings or ("none",)
    component_cards = "".join(_score_component_card(component) for component in preview.component_explanations)
    bullish_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.bullish_evidence)
    bearish_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.bearish_evidence)
    neutral_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.neutral_evidence)
    evidence_rows = "".join(
        "<tr>"
        f"<td>{_safe(item.name)}</td>"
        f"<td>{_safe(item.category)}</td>"
        f"<td><span class='badge evidence-{_safe(item.direction)}'>{_safe(item.direction)}</span></td>"
        f"<td>{_safe(item.strength)}</td>"
        f"<td>{item.numeric_contribution:+.2f}</td>"
        f"<td>{_safe(item.explanation)}</td>"
        "</tr>"
        for item in preview.evidence_items
    )
    watch_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.watch_next)
    confidence_driver_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.confidence_drivers)
    confidence_drag_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.confidence_drags)
    guardrail = preview.fundamental_guardrails
    fundamental_bullish_items = "".join(f"<li>{_safe(item)}</li>" for item in guardrail.bullish_evidence)
    fundamental_bearish_items = "".join(f"<li>{_safe(item)}</li>" for item in guardrail.bearish_evidence)
    fundamental_warning_items = "".join(f"<li>{_safe(item)}</li>" for item in guardrail.warnings) or "<li>none</li>"
    warning_items = "".join(f"<li>{_safe(warning)}</li>" for warning in warnings)
    return f"""
    <section class="panel score-result">
      <div class="section-head">
        <h2>{_safe(candidate.symbol)} Score Preview</h2>
        <span class="badge data-mode">{_safe(preview.data_mode.upper())} DATA</span>
      </div>
      <div class="score-intel-grid">
        <div class="score-gauge score-card">
          <strong>{candidate.score:.2f}</strong>
          <p>GreenRock Score</p>
        </div>
        <div class="score-gauge confidence-card">
          <strong>{preview.confidence_score:.2f}</strong>
          <p>GreenRock Confidence</p>
          <span class="confidence-band">{_safe(preview.confidence_band)}</span>
        </div>
        <div class="priority-card">
          <strong>{preview.evidence_agreement_score:.2f}</strong>
          <p>Evidence Agreement</p>
        </div>
        <div class="priority-card">
          <span class="badge priority">{_safe(preview.research_priority)}</span>
          <p>Research Priority</p>
          <span class="badge signal">{_safe(score_signal(candidate))}</span>
          <span class="badge selection">{_safe(candidate.selection_label)}</span>
        </div>
      </div>
      <section class="analyst-summary">
        <h2>Analyst Summary</h2>
        <p>{_safe(preview.analyst_summary)}</p>
        <p class="summary-action">{_finviz_button(candidate.symbol)}</p>
      </section>
      {_score_why_this_score(preview)}
      <section class="panel inner-panel confidence-explain-card">
        <div class="section-head">
          <h2>Why Confidence Is This Level</h2>
          <span class="badge confidence-badge">{preview.confidence_score:.2f} - {_safe(preview.confidence_band)}</span>
        </div>
        <div class="confidence-explain-grid">
          <div>
            <h3>Positive Confidence Drivers</h3>
            <ul class="compact-list">{confidence_driver_items}</ul>
          </div>
          <div>
            <h3>Confidence Drags</h3>
            <ul class="compact-list">{confidence_drag_items}</ul>
          </div>
        </div>
      </section>
      <div class="detail-grid">
        {_detail_panel("Company", candidate.company_name)}
        {_detail_panel("Market Cap", _format_market_cap(str(candidate.market_cap)))}
        {_detail_panel("Price", _format_currency(str(indicators.latest_close)))}
        {_detail_panel("RSI", f"{indicators.rsi_14:.2f}")}
        {_detail_panel("Bollinger Band Position", _score_bollinger_position(candidate))}
        {_detail_panel("52-week Low Distance", f"{indicators.low_proximity:.1%}")}
        {_detail_panel("Volume Acceleration", _score_volume_acceleration(candidate))}
        {_detail_panel("Moving Average Structure", _score_moving_average_structure(candidate))}
        {_detail_panel("Data Source", preview.data_source)}
        {_detail_panel("Selection Mode", preview.selection_mode)}
      </div>
      <section class="panel inner-panel evidence-engine-card">
        <div class="section-head">
          <h2>Evidence Engine</h2>
          <span class="badge confidence-badge">Agreement {preview.evidence_agreement_score:.2f}</span>
        </div>
        <p class="subtle">{_safe(preview.score_confidence_divergence)}</p>
        <div class="evidence-grid">
          <div>
            <h3>Bullish Evidence</h3>
            <ul class="compact-list">{bullish_items}</ul>
          </div>
          <div>
            <h3>Bearish Evidence</h3>
            <ul class="compact-list">{bearish_items}</ul>
          </div>
        </div>
        <h3>Neutral / Watch Items</h3>
        <ul class="compact-list">{neutral_items}</ul>
        <table class="evidence-table">
          <thead><tr><th>Signal</th><th>Category</th><th>Direction</th><th>Strength</th><th>Contribution</th><th>Explanation</th></tr></thead>
          <tbody>{evidence_rows}</tbody>
        </table>
      </section>
      <section class="panel inner-panel fundamental-guardrail-card">
        <div class="section-head">
          <h2>Fundamental Guardrails</h2>
          <span class="badge guardrail-badge">{_safe(guardrail.label)}</span>
        </div>
        <div class="detail-grid">
          {_detail_panel("Net Cash / Debt", _format_net_cash_debt(guardrail.net_cash))}
          {_detail_panel("Net Cash Per Share", _format_currency(str(guardrail.net_cash_per_share)) if guardrail.net_cash_per_share is not None else "unavailable")}
          {_detail_panel("Quick Ratio", f"{guardrail.quick_ratio:.2f}" if guardrail.quick_ratio is not None else "unavailable")}
          {_detail_panel("Share Change", f"{guardrail.shares_outstanding_change_percent:.2%}" if guardrail.shares_outstanding_change_percent is not None else "unavailable")}
          {_detail_panel("Confidence Impact", f"{guardrail.confidence_impact:+.1f} pts")}
          {_detail_panel("Score Adjustment", f"{preview.fundamental_guardrail_adjustment:+.1f} pts")}
        </div>
        <div class="evidence-grid">
          <div>
            <h3>Bullish Fundamental Evidence</h3>
            <ul class="compact-list">{fundamental_bullish_items}</ul>
          </div>
          <div>
            <h3>Bearish Fundamental Evidence</h3>
            <ul class="compact-list">{fundamental_bearish_items}</ul>
          </div>
        </div>
        <h3>Fundamental Data Warnings</h3>
        <ul class="compact-list">{fundamental_warning_items}</ul>
      </section>
      <div class="evidence-grid">
        <section class="panel inner-panel evidence-card bullish-card">
          <h2>Bullish Evidence</h2>
          <ul class="compact-list">{bullish_items}</ul>
        </section>
        <section class="panel inner-panel evidence-card bearish-card">
          <h2>Bearish Evidence</h2>
          <ul class="compact-list">{bearish_items}</ul>
        </section>
      </div>
      <section class="panel inner-panel watch-next-card">
        <h2>What to Watch Next</h2>
        <ul class="compact-list">{watch_items}</ul>
      </section>
      <section class="panel inner-panel score-breakdown-card">
        <h2>Score Breakdown</h2>
        <p class="subtle">Each card shows the raw metric, component score, weight, and plain-English rationale before the final 100-point cap.</p>
        <div class="score-breakdown-grid">{component_cards}</div>
      </section>
      <section class="panel inner-panel price-target-panel">
        <div class="section-head">
          <h2>1-Year Statistical Price Targets</h2>
          <span class="subtle">Statistical targets, not forecasts or guarantees</span>
        </div>
        {_price_target_table(preview)}
      </section>
      <section class="panel inner-panel">
        <h2>Data Quality Warnings</h2>
        <ul class="compact-list">{warning_items}</ul>
      </section>
      <p>{_finviz_link(candidate.symbol)}</p>
    </section>
    """


def _score_provider_setup_card(provider, detail: str | None = None) -> str:
    detail_html = f"<p class='subtle'>{_safe(detail)}</p>" if detail else ""
    return f"""
    <section class="panel setup-card">
      <div class="section-head">
        <h2>Real Data Setup</h2>
        <span class="badge ready">Provider Status: {_safe(provider.status_label)}</span>
      </div>
      <p>The Score Calculator is real-data-only for operators. Real ticker scoring needs the local market-data provider enabled on this machine.</p>
      {detail_html}
      <div class="setup-box">
        <p>One-copy local setup:</p>
        <pre>{_safe(provider.recommended_fix_command)}</pre>
      </div>
      <p class="subtle">This configures the provider name and optional package locally. Do not commit credentials. No report, approval, artifact, email, publication, trading action, client file, or external LLM/API action is created.</p>
    </section>
    """


def _score_why_this_score(preview) -> str:
    positive_components = sorted(
        (component for component in preview.component_explanations if component.component_score > 0),
        key=lambda component: component.component_score,
        reverse=True,
    )[:3]
    positive_items = "".join(
        f"<li>{_safe(component.name)}: {component.component_score:.2f} pts. {_safe(component.explanation)}</li>"
        for component in positive_components
    ) or "<li>No positive score drivers are currently active.</li>"

    drags: list[str] = []
    for component in preview.component_explanations:
        if component.component_score <= 0:
            drags.append(f"{component.name}: no current score contribution. {component.explanation}")
    if preview.evidence_score_adjustment < 0:
        drags.append(f"Evidence adjustment: {preview.evidence_score_adjustment:+.2f} pts from mixed or weak agreement.")
    if preview.fundamental_guardrail_adjustment < 0:
        drags.append(
            f"Fundamental guardrail: {preview.fundamental_guardrail_adjustment:+.2f} pts from {preview.fundamental_guardrails.label}."
        )
    for warning in preview.data_quality_warnings[:2]:
        drags.append(f"Data quality warning: {warning}")
    drag_items = "".join(f"<li>{_safe(item)}</li>" for item in drags[:4]) or "<li>No major score drags beyond the current component mix.</li>"
    adjustment = (
        f"Base technical {preview.base_technical_score:.2f} "
        f"{preview.fundamental_guardrail_adjustment:+.2f} guardrail "
        f"{preview.evidence_score_adjustment:+.2f} evidence "
        f"= final {preview.candidate.score:.2f}."
    )
    return f"""
      <section class="panel inner-panel score-why-card">
        <div class="section-head">
          <h2>Why This Score?</h2>
          <span class="badge signal">{preview.candidate.score:.2f}</span>
        </div>
        <div class="confidence-explain-grid">
          <div>
            <h3>Top Positive Score Drivers</h3>
            <ul class="compact-list">{positive_items}</ul>
          </div>
          <div>
            <h3>Top Score Drags</h3>
            <ul class="compact-list">{drag_items}</ul>
          </div>
        </div>
        <p class="subtle">Score adjustment summary: {_safe(adjustment)} Confidence is explained separately below.</p>
      </section>
    """


def _save_ticker_panel(ticker: str, save_status: str | None = None) -> str:
    options = "".join(
        f"<option value='{_safe(key)}'>{_safe(label)}</option>"
        for key, label in GREENROCK_PLACEMENT_LABELS.items()
    )
    status = f"<p class='save-status'>{_safe(save_status)}</p>" if save_status else ""
    disabled = "disabled" if not ticker else ""
    return f"""
    <section class="panel save-list-panel">
      <div class="section-head">
        <h2>Add Ticker to GreenRock List</h2>
        <span class="subtle">Local storage only</span>
      </div>
      <form method="post" action="/greenrock/score/save" class="save-list-form">
        <input name="ticker" value="{_safe(ticker)}" placeholder="Ticker" required>
        <select name="list_key">{options}</select>
        <button type="submit" {disabled}>Save Ticker to List</button>
      </form>
      {status}
      <p class="subtle">Saving writes only to local GreenRock lists. It does not publish, create a report, open an approval, or create an artifact.</p>
    </section>
    """


def _score_rank_explainer(preview) -> str:
    rows = (
        ("Exceptional", "85-100", 85.0, 100.0),
        ("Strong", "70-84", 70.0, 84.999),
        ("Watchlist", "55-69", 55.0, 69.999),
        ("Low Priority", "below 55", 0.0, 54.999),
    )
    score = preview.candidate.score if preview else None
    ticker = preview.candidate.symbol if preview else ""
    cards = []
    for label, score_range, lower, upper in rows:
        placement = ""
        active_class = ""
        if score is not None and lower <= score <= upper:
            placement = f" ({_safe(ticker)}: {score:.1f})"
            active_class = " active-rank"
        cards.append(
            f"""
            <div class="rank-band{active_class}">
              <strong>{_safe(label)}: {_safe(score_range)}{placement}</strong>
              <p>{_safe(_rank_band_description(label))}</p>
            </div>
            """
        )
    return f"<div class='rank-grid'>{''.join(cards)}</div>"


def _rank_band_description(label: str) -> str:
    return {
        "Exceptional": "Highest-priority technical dislocation setups for deeper review.",
        "Strong": "Compelling setups with meaningful GreenRock criteria support.",
        "Watchlist": "Visible but less complete setups that may need more confirmation.",
        "Low Priority": "Weak or incomplete setups under current GreenRock criteria.",
    }[label]


def _score_component_card(component) -> str:
    return f"""
    <article class="component-card">
      <div class="component-topline">
        <h3>{_safe(component.name)}</h3>
        <span>{component.component_score:.2f} / {component.weight}</span>
      </div>
      <dl>
        <div><dt>Raw metric</dt><dd>{_safe(component.raw_metric)}</dd></div>
        <div><dt>Component score</dt><dd>{component.component_score:.2f}</dd></div>
        <div><dt>Weight</dt><dd>{component.weight} pts</dd></div>
      </dl>
      <p>{_safe(component.explanation)}</p>
    </article>
    """


def _price_target_table(preview) -> str:
    candidate = preview.candidate
    targets_unavailable = preview.all_time_high is None or all(target.price is None for target in preview.price_targets)
    if targets_unavailable:
        warning_items = "".join(f"<li>{_safe(warning)}</li>" for warning in preview.price_target_warnings)
        return f"""
        <div class="warning-panel target-warning">
          <p>Price targets cannot be calculated cleanly for this ticker.</p>
          <ul class="compact-list">{warning_items or '<li>All-time high or standard deviation data is unavailable.</li>'}</ul>
        </div>
        """

    warning_html = ""
    if preview.price_target_warnings:
        warning_html = "<ul class='compact-list target-notes'>" + "".join(
            f"<li>{_safe(warning)}</li>" for warning in preview.price_target_warnings
        ) + "</ul>"
    rows = [
        ("Current Price", candidate.indicators.latest_close, ""),
        ("All-Time High", preview.all_time_high, "ath-row"),
    ]
    rows.extend((target.label, target.price, f"target-{target.relation_to_ath}") for target in preview.price_targets)
    body = "".join(
        f"<tr class='{_safe(css_class)}'><td>{_safe(label)}</td><td>{_format_currency(str(price)) if price is not None else 'unavailable'}</td></tr>"
        for label, price, css_class in rows
    )
    return f"""
    <dl class="target-assumptions">
      <div><dt>Historical lookback</dt><dd>{_safe(preview.price_target_lookback)}</dd></div>
      <div><dt>Horizon</dt><dd>{_safe(preview.price_target_horizon)}</dd></div>
      <div><dt>Data source</dt><dd>{_safe(preview.data_source)}</dd></div>
      <div><dt>Disclosure</dt><dd>These are statistical targets, not forecasts or guarantees.</dd></div>
    </dl>
    <table class="price-target-table">
      <thead><tr><th>Target</th><th>Price</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    {warning_html}
    """


def _picks_board_warnings(
    mega_pick: dict[str, str] | None,
    large_candidates: list[dict[str, str]],
    small_candidates: list[dict[str, str]],
) -> list[str]:
    warnings = []
    if mega_pick is None:
        warnings.append("Mega Rock section has 0/1 picks.")
    if len(large_candidates) < 11:
        warnings.append(f"Large-cap section has {len(large_candidates)}/11 picks.")
    if len(small_candidates) < 11:
        warnings.append(f"Small/mid-cap section has {len(small_candidates)}/11 picks.")
    return warnings


def _picks_warning_panel(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{_safe(warning)}</li>" for warning in warnings)
    return f"""
    <section class="panel warning-panel">
      <h2>Data Quality Warning</h2>
      <p>Some Picks Board sections did not fill their target slots. Review universe coverage, provider availability, and screening criteria.</p>
      <ul class="compact-list">{items}</ul>
    </section>
    """


def _why_screened_in(row: dict[str, str]) -> str:
    rules = [rule.replace("_", " ") for rule in row.get("passed_rules", "").split(";") if rule]
    if not rules:
        return "<span class='subtle'>Screening rationale unavailable.</span>"
    return "<ul class='compact-list'>" + "".join(f"<li>{_safe(rule)}</li>" for rule in rules[:4]) + "</ul>"


def _bollinger_status(row: dict[str, str]) -> str:
    try:
        price = float(row.get("latest_close", "0"))
        lower = float(row.get("bollinger_lower", "0"))
        upper = float(row.get("bollinger_upper", "0"))
    except ValueError:
        return "unavailable"
    if price < lower:
        return "Below lower 2.5σ band"
    lower_distance = abs(price - lower)
    upper_distance = abs(upper - price)
    if lower_distance < upper_distance:
        return "Closer to lower band"
    return "Closer to upper band"


def _volume_acceleration(row: dict[str, str]) -> str:
    try:
        current = float(row.get("volume_avg_10", "0"))
        previous = float(row.get("previous_volume_avg_10", "0"))
    except ValueError:
        return "unavailable"
    if previous <= 0:
        return "unavailable"
    change = (current - previous) / previous
    return f"{change:.1%}"


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
    return f"{(indicators.volume_avg_10 - indicators.previous_volume_avg_10) / indicators.previous_volume_avg_10:.1%}"


def _score_moving_average_structure(candidate) -> str:
    return (
        f"8 EMA {'below' if 'ema8_below_sma10' in candidate.passed_rules else 'not below'} 10 SMA; "
        f"50 DMA {'below' if 'dma50_below_dma150' in candidate.passed_rules else 'not below'} 150 DMA; "
        f"50 DMA ROC {'improving' if 'dma50_roc_improving_vs_dma150' in candidate.passed_rules else 'not improving'} vs 150 DMA"
    )


def _format_market_cap(value: str) -> str:
    try:
        amount = float(value)
    except ValueError:
        return _safe(value)
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount:,.0f}"


def _format_currency(value: str) -> str:
    try:
        amount = float(value)
    except ValueError:
        return _safe(value)
    return f"${amount:,.2f}"


def _format_net_cash_debt(value: float | None) -> str:
    if value is None:
        return "unavailable"
    label = "Net Cash" if value >= 0 else "Net Debt"
    return f"{label} ${abs(value) / 1_000_000_000:.2f}B"


def _format_percent(value: str) -> str:
    try:
        amount = float(value)
    except ValueError:
        return _safe(value)
    return f"{amount:.1%}"


def _finviz_link(symbol: str) -> str:
    clean_symbol = symbol.strip().replace(".", "-")
    if not clean_symbol:
        return ""
    href = f"https://finviz.com/quote.ashx?t={quote(clean_symbol)}"
    return f"<a href='{href}' target='_blank' rel='noopener noreferrer'>Finviz</a>"


def _finviz_button(symbol: str) -> str:
    clean_symbol = symbol.strip().replace(".", "-")
    if not clean_symbol:
        return ""
    href = f"https://finviz.com/quote.ashx?t={quote(clean_symbol)}"
    return f"<a class='button secondary' href='{href}' target='_blank' rel='noopener noreferrer'>Finviz</a>"


def _workflow_feed(runs, context) -> str:
    if not runs:
        return "<p class='empty'>No workflow runs found.</p>"
    rows = []
    for run in runs:
        approvals = [approval for approval in context["approvals"] if approval.run_id == run.run_id]
        approval_status = approvals[0].status.value if approvals else "none"
        pdf_status = "exported" if _latest_pdf_for_run(context["connection"], run.run_id) else "not exported"
        report = next((item for item in context["reports"] if item.run_id == run.run_id), None)
        data_source = _latest_report_data_source(report) or "-"
        run_href = f"/greenrock/reports/{quote(run.run_id)}/review" if report else f"/runs/{quote(run.run_id)}"
        rows.append(
            "<tr>"
            f"<td><a href='{run_href}'>{_safe(run.run_id)}</a></td>"
            f"<td>{_safe(run.workflow_name)}</td>"
            f"<td><span class='badge {run.status}'>{_safe(run.status)}</span></td>"
            f"<td>{_safe(run.started_at)}</td>"
            f"<td>{_safe(run.completed_at or '-')}</td>"
            f"<td>{_safe(run.data_mode.upper())}</td>"
            f"<td>{_safe(data_source)}</td>"
            f"<td>{_safe(approval_status)}</td>"
            f"<td>{_safe(pdf_status)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Created</th>"
        "<th>Completed</th><th>Data Mode</th><th>Data Source</th><th>Approval</th><th>PDF</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _candidate_signal(row: dict[str, str]) -> str:
    if row.get("signal_label"):
        return row["signal_label"]
    try:
        return signal_label(float(row.get("score", "0")))
    except ValueError:
        return ""


def _latest_report_data_source(report) -> str | None:
    if not report or not report.content_path:
        return None
    path = Path(report.content_path)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("**Data Source:**"):
            return line.removeprefix("**Data Source:**").strip()
    return None


def _approvals_table(approvals, actions: bool) -> str:
    if not approvals:
        return "<p class='empty'>No approvals found.</p>"
    action_header = "<th>Action</th>" if actions else ""
    body = "".join(
        "<tr>"
        f"<td><a href='/approvals/{approval.id}'>{approval.id}</a></td>"
        f"<td><span class='badge {_safe(approval.status.value)}'>{_safe(approval.status.value)}</span></td>"
        f"<td>{_safe(approval.artifact_type)}</td><td>{_review_run_link(approval.run_id)}</td>"
        f"<td class='path'>{_safe(approval.artifact_path or '-')}</td>"
        f"{_approval_actions(approval) if actions else ''}"
        "</tr>"
        for approval in approvals
    )
    return (
        "<table><thead><tr><th>ID</th><th>Status</th><th>Type</th><th>Run</th>"
        f"<th>Path</th>{action_header}</tr></thead><tbody>{body}</tbody></table>"
    )


def _approval_actions(approval) -> str:
    if approval.status != ApprovalStatus.PENDING:
        return f"<td>{_review_run_button(approval.run_id, 'Review Report')}</td>"
    review_path = f"/greenrock/reports/{quote(approval.run_id)}/review" if approval.run_id else "/greenrock"
    return f"""
    <td class="actions">
      <a class="button" href="/approvals/{approval.id}/confirm?action=approve&return_to={quote(review_path)}">Approve</a>
      <a class="button secondary" href="/approvals/{approval.id}/confirm?action=reject&return_to={quote(review_path)}">Reject</a>
      {_review_run_button(approval.run_id, 'Review')}
    </td>
    """


def _reports_table(reports) -> str:
    if not reports:
        return "<p class='empty'>No reports found.</p>"
    rows = "".join(
        "<tr>"
        f"<td>{report.id}</td><td>{_safe(report.title)}</td><td>{_safe(report.status)}</td>"
        f"<td>{_review_run_link(report.run_id)}</td><td class='path'>{_safe(report.content_path or '-')}</td>"
        f"<td>{_review_run_button(report.run_id, 'Review')}</td>"
        "</tr>"
        for report in reports[:30]
    )
    return "<table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Run</th><th>Path</th><th>Review</th></tr></thead><tbody>" + rows + "</tbody></table>"


def _report_metadata_index(context) -> list[dict[str, str]]:
    approvals_by_id = {approval.id: approval for approval in context["approvals"]}
    artifacts_by_run = {}
    for artifact in context["artifacts"]:
        artifacts_by_run.setdefault(artifact.run_id, []).append(artifact)
    rows = []
    for report in context["reports"]:
        approval = approvals_by_id.get(report.approval_id) if report.approval_id else None
        markdown = _read_report_markdown(report.content_path)
        metadata = _basic_report_metadata(markdown)
        tickers = _report_tickers(markdown, artifacts_by_run.get(report.run_id or "", []))
        pdf_status = "exported" if any(item.artifact_type == "report_final_pdf" for item in artifacts_by_run.get(report.run_id, [])) else "not_exported"
        rows.append(
            {
                "report_id": str(report.id),
                "run_id": report.run_id or "",
                "report_date": metadata.get("date", report.created_at[:10]),
                "created_at": report.created_at,
                "title": report.title,
                "report_type": report.report_type or "greenrock",
                "data_mode": metadata.get("data_mode", ""),
                "source": metadata.get("candidate_source", report.report_type or ""),
                "status": report.status,
                "approval_status": approval.status.value if approval else "none",
                "pdf_status": pdf_status,
                "tickers": ",".join(tickers),
                "featured": ",".join(_report_featured_tickers(markdown)),
                "source_scan_ids": metadata.get("scan_ids", ""),
                "path": report.content_path or "",
                "lifecycle_key": _report_lifecycle_key(report.report_type or "greenrock", metadata, tickers),
            }
        )
    return rows


def _filter_report_index(rows: list[dict[str, str]], filters: dict[str, str]) -> list[dict[str, str]]:
    if filters.get("show_all", "").strip() != "1":
        rows = _collapse_default_report_lifecycle_rows(rows)
    ticker = filters.get("ticker", "").strip().upper()
    status = filters.get("status", "").strip()
    approval = filters.get("approval", "").strip()
    data_mode = filters.get("data_mode", "").strip()
    visible = []
    for row in rows:
        if ticker and ticker not in row["tickers"].split(","):
            continue
        if status and row["status"] != status:
            continue
        if approval and row["approval_status"] != approval:
            continue
        if data_mode and row["data_mode"].lower() != data_mode:
            continue
        visible.append(row)
    return visible


def _collapse_default_report_lifecycle_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    visible = []
    seen_draft_keys = set()
    for row in rows:
        if not _is_draft_lifecycle_row(row):
            visible.append(row)
            continue
        key = row.get("lifecycle_key", "") or row.get("run_id", "")
        if key in seen_draft_keys:
            continue
        seen_draft_keys.add(key)
        visible.append(row)
    return visible


def _is_draft_lifecycle_row(row: dict[str, str]) -> bool:
    if row.get("pdf_status") == "exported":
        return False
    if row.get("approval_status") in {"approved", "rejected"}:
        return False
    status = row.get("status", "")
    return status in {"blocked_for_approval", "awaiting_approval", "draft", "pending"} or row.get("approval_status") in {"pending", "none"}


def _reports_index_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<p class='empty'>No indexed reports match the current filters.</p>"
    body = "".join(
        "<tr>"
        f"<td>{_safe(row['report_date'])}</td>"
        f"<td>{_safe(row['created_at'])}</td>"
        f"<td>{_safe(row['title'])}<br><span class='subtle'>{_safe(row['report_type'])} / {_safe(row['source'])}</span></td>"
        f"<td>{_safe(row['data_mode'] or '-')}</td>"
        f"<td>{_safe(row['status'])}</td>"
        f"<td>{_safe(row['approval_status'])}</td>"
        f"<td>{_safe(row['pdf_status'])}</td>"
        f"<td>{_safe(row['tickers'][:120] or '-')}</td>"
        f"<td>{_review_run_button(row['run_id'], 'Open Report')}</td>"
        "</tr>"
        for row in rows
    )
    return "<table><thead><tr><th>Report Date</th><th>Generated</th><th>Report</th><th>Data Mode</th><th>Status</th><th>Approval</th><th>PDF</th><th>Tickers</th><th>Open</th></tr></thead><tbody>" + body + "</tbody></table>"


def _filter_options(values: tuple[str, ...], selected: str) -> str:
    return "".join(f"<option value='{value}' {'selected' if value == selected else ''}>{_safe(value.replace('_', ' ').title())}</option>" for value in values)


def _basic_report_metadata(markdown: str) -> dict[str, str]:
    metadata = {
        "date": _markdown_field(markdown, "Date"),
        "data_mode": _markdown_field(markdown, "Data Mode"),
        "candidate_source": _markdown_field(markdown, "Candidate Source"),
    }
    scan_lines = [line.removeprefix("- Scan IDs:").strip() for line in markdown.splitlines() if line.startswith("- Scan IDs:")]
    metadata["scan_ids"] = scan_lines[0] if scan_lines else ""
    return metadata


def _report_lifecycle_key(report_type: str, metadata: dict[str, str], tickers: tuple[str, ...]) -> str:
    return "|".join(
        (
            report_type,
            metadata.get("data_mode", "").lower(),
            metadata.get("candidate_source", "").lower(),
            metadata.get("scan_ids", ""),
            ",".join(tickers),
        )
    )


def _report_tickers(markdown: str, artifacts: list | tuple = ()) -> tuple[str, ...]:
    tickers = set()
    tickers.update(_report_table_tickers(markdown))
    tickers.update(_report_featured_tickers(markdown))
    tickers.update(_artifact_candidate_tickers(artifacts))
    return tuple(sorted(tickers))


def _artifact_candidate_tickers(artifacts: list | tuple) -> set[str]:
    tickers = set()
    for artifact in artifacts:
        if not str(getattr(artifact, "artifact_type", "")).endswith("_csv"):
            continue
        path = Path(getattr(artifact, "path", ""))
        if not path.exists():
            continue
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    for field in ("symbol", "ticker", "Symbol", "Ticker"):
                        value = row.get(field)
                        if value and _is_candidate_symbol(value):
                            tickers.add(value.strip().upper())
        except (OSError, csv.Error):
            continue
    return tickers


def _report_table_tickers(markdown: str) -> set[str]:
    tickers = set()
    table_header: list[str] | None = None
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            table_header = None
            continue
        cells = [_clean_table_cell(cell) for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        lowered = [cell.lower() for cell in cells]
        if "symbol" in lowered or "ticker" in lowered:
            table_header = lowered
            continue
        if table_header and set(cells) <= {"", "---", ":---", "---:"}:
            continue
        if not table_header:
            continue
        for field in ("symbol", "ticker"):
            if field in table_header:
                index = table_header.index(field)
                if index < len(cells) and _is_candidate_symbol(cells[index]):
                    tickers.add(cells[index].upper())
    return tickers


def _clean_table_cell(value: str) -> str:
    return value.strip().strip("*`[]")


def _is_candidate_symbol(value: str) -> bool:
    clean = value.strip().upper()
    return 1 <= len(clean) <= 8 and clean.isalnum() and any(char.isalpha() for char in clean)


def _report_featured_tickers(markdown: str) -> tuple[str, ...]:
    featured = []
    in_featured = False
    for line in markdown.splitlines():
        if line.startswith("## Featured Archetype Leaders"):
            in_featured = True
            continue
        if in_featured and line.startswith("## "):
            break
        if in_featured and line.startswith("### ") and ":" in line:
            featured.append(line.split(":", 1)[1].strip().split()[0].upper())
    return tuple(featured)


def _report_history_for_ticker(ticker: str) -> list[dict[str, str]]:
    context = _load_context()
    return _filter_report_index(_report_metadata_index(context), {"ticker": ticker.upper()})


def _final_reports_table(rows: tuple[dict[str, str], ...]) -> str:
    if not rows:
        return "<p class='empty'>No final PDFs exported yet.</p>"
    body = "".join(
        "<tr>"
        f"<td>{_safe(row['run_id'])}</td>"
        f"<td>{_safe(row['approval_id'])}</td>"
        f"<td class='path'>{_safe(row['path'])}</td>"
        f"<td>{_safe(row['created_at'])}</td>"
        f"<td>{_open_link(row['path'], 'Open PDF')}</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>Run</th><th>Approval</th><th>PDF Path</th>"
        "<th>Exported</th><th>Local</th></tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def _artifacts_table(artifacts) -> str:
    if not artifacts:
        return "<p class='empty'>No artifacts found.</p>"
    body = "".join(
        "<tr>"
        f"<td><a href='/artifacts/{artifact.id}'>{artifact.id}</a></td><td>{_safe(artifact.artifact_type)}</td>"
        f"<td>{_safe(artifact.run_id)}</td><td class='path'>{_safe(artifact.path)}</td>"
        f"<td>{_open_link(artifact.path, 'Open')}</td>"
        "</tr>"
        for artifact in artifacts
    )
    return "<table><thead><tr><th>ID</th><th>Type</th><th>Run</th><th>Path</th><th>Local</th></tr></thead><tbody>" + body + "</tbody></table>"


def _audit_table(events) -> str:
    if not events:
        return "<p class='empty'>No audit events found.</p>"
    body = "".join(
        "<tr>"
        f"<td>{event.id}</td><td>{_safe(event.actor)}</td><td>{_safe(event.action)}</td><td>{_safe(event.run_id or '-')}</td>"
        "</tr>"
        for event in events
    )
    return "<table><thead><tr><th>ID</th><th>Actor</th><th>Action</th><th>Run</th></tr></thead><tbody>" + body + "</tbody></table>"


def _task_column(tasks, status: str, title: str) -> str:
    matching = [task for task in tasks if task.status == status]
    cards = "".join(_task_card(task) for task in matching) or "<p class='empty'>No tasks.</p>"
    return f"<div class='kanban-column'><h2>{_safe(title)} <span>{len(matching)}</span></h2>{cards}</div>"


def _pt_task_summary(tasks) -> dict[str, int]:
    return {
        "open": len([task for task in tasks if task.status != "done"]),
        "blocked": len([task for task in tasks if task.status == "awaiting_review"]),
        "completed": len([task for task in tasks if task.status == "done"]),
    }


def _pt_project_card(project, tasks) -> str:
    project_tasks = [task for task in tasks if task.project_id == project.id]
    open_tasks = [task for task in project_tasks if task.status != "done"]
    blocked = [task for task in project_tasks if task.status == "awaiting_review"]
    completed = [task for task in project_tasks if task.status == "done"]
    latest = max((task.updated_at for task in project_tasks), default=project.created_at)
    stage_options = "".join(
        f"<option value='{stage}' {'selected' if stage == project.status else ''}>{_safe(_project_stage_label(stage))}</option>"
        for stage in PROJECT_STAGES
    )
    open_list = "".join(f"<li>{_safe(task.name)}</li>" for task in open_tasks[:3]) or "<li>No open tasks</li>"
    return f"""
    <article class="project-card">
      <span class="badge { _safe(project.status) }">{_safe(_project_stage_label(project.status))}</span>
      <h2>{_safe(project.name)}</h2>
      <p>{_safe(project.division)}</p>
      <dl>
        <div><dt>Open tasks</dt><dd>{len(open_tasks)}</dd></div>
        <div><dt>Blocked tasks</dt><dd>{len(blocked)}</dd></div>
        <div><dt>Completed tasks</dt><dd>{len(completed)}</dd></div>
        <div><dt>Latest activity</dt><dd>{_safe(latest)}</dd></div>
      </dl>
      <p><strong>Next milestone:</strong> {_safe(open_tasks[0].name if open_tasks else "none")}</p>
      <ul class="compact-list">{open_list}</ul>
      <form method="post" action="/pt/projects/{project.id}/status" class="inline-form">
        <select name="status">{stage_options}</select>
        <button type="submit">Update Stage</button>
      </form>
      <p><a href="/pt?project={project.id}">Filter tasks</a></p>
    </article>
    """


def _legacy_project_card(item, context) -> str:
    name, division, description, href, status = item
    latest_run = next((run for run in context["runs"] if run.division == division), None)
    task_count = len([task for task in context["tasks"] if task.division == division])
    return f"""
    <a class="project-card" href="{href}">
      <span class="badge {status}">{_safe(status)}</span>
      <h2>{_safe(name)}</h2>
      <p>{_safe(description)}</p>
      <dl>
        <div><dt>Latest run</dt><dd>{_safe(latest_run.run_id if latest_run else "none")}</dd></div>
        <div><dt>Manual tasks</dt><dd>{task_count}</dd></div>
      </dl>
    </a>
    """


def _task_card(task) -> str:
    buttons = "".join(
        f"<button {'disabled' if task.status == status else ''} name='status' value='{status}'>{_task_status_label(status)}</button>"
        for status in TASK_STATUSES
    )
    return f"""
    <article class="task-card">
      <h3>{_safe(task.name)}</h3>
      <p>{_safe(task.notes or "No notes")}</p>
      <div class="task-meta"><span>{_safe(task.division)} / project {task.project_id or "default"}</span><span>{_safe(task.updated_at)}</span></div>
      <form method="post" action="/tasks/{task.id}/status" class="task-moves">{buttons}</form>
    </article>
    """


def _approval_detail_block(approval) -> str:
    return f"""
    <dl class="detail-list">
      <div><dt>Status</dt><dd>{_safe(approval.status.value)}</dd></div>
      <div><dt>Run ID</dt><dd>{_safe(approval.run_id or "-")}</dd></div>
      <div><dt>Artifact ID</dt><dd>{_safe(approval.artifact_id or "-")}</dd></div>
      <div><dt>Artifact Type</dt><dd>{_safe(approval.artifact_type)}</dd></div>
      <div><dt>Artifact Path</dt><dd class="path">{_safe(approval.artifact_path or "-")}</dd></div>
      <div><dt>Requested</dt><dd>{_safe(approval.requested_at or "-")}</dd></div>
      <div><dt>Decided</dt><dd>{_safe(approval.decided_at or "-")}</dd></div>
    </dl>
    """


def _detail_panel(title: str, value: str) -> str:
    return f"<div class='panel detail-panel'><h2>{_safe(title)}</h2><p>{_safe(value)}</p></div>"


def _float_form_value(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _agent_status_summary() -> dict:
    settings = get_settings()
    cycle = agent_cycle_summary(settings.output_dir)
    return {
        "agents": list_agent_states(settings.output_dir),
        "runs": list_agent_runs(settings.output_dir),
        "cycle": cycle,
        "market_scan": cycle.get("market_scan", {}),
    }


def _attention_card(color: str, value: str, label: str, note: str) -> str:
    return f"<article class='attention-card {color}'><strong>{value}</strong><h2>{_safe(label)}</h2><p>{_safe(note)}</p></article>"


def _inbox_card(item: dict[str, str]) -> str:
    meta = item.get("meta", item.get("label", ""))
    return f"""
    <a class="inbox-card {item['status']}" href="{item['href']}">
      <span class="check"></span>
      <div>
        <h3>{_safe(item['title'])}</h3>
        <p>{_safe(item['detail'])}</p>
        <p class="subtle">{_safe(meta)}</p>
      </div>
      <span class="badge">{_safe(item['label'])}</span>
    </a>
    """


def _inbox_item_to_card(item) -> dict[str, str]:
    status = "attention" if item.severity in {"warning", "critical", "action"} else "neutral"
    return {
        "title": item.title,
        "detail": item.detail,
        "href": item.target_url or "/atlas/inbox",
        "status": status,
        "label": f"{item.severity} / {item.status}",
        "meta": (
            f"Created {_date_part(item.created_at)} {_time_part(item.created_at)}; "
            f"updated {_date_part(item.updated_at)} {_time_part(item.updated_at)}; "
            f"source {item.source_agent}; project {item.related_project_id or 'none'}; cycle {item.related_cycle_id or 'none'}"
        ),
    }


def _agent_health_card(agent) -> str:
    return _agent_card(agent, "monitor")


def _agent_card(agent, variant: str = "monitor", handoff_label: str = "") -> str:
    status = agent.status or "idle"
    update = latest_agent_update(get_settings().output_dir, agent.name)
    headline = update.headline if update else "No structured update yet"
    summary = update.summary if update else (agent.output_summary or agent.last_message or "No output summary yet.")
    timestamp = update.created_at if update else (agent.last_run_at or "none")
    if variant == "wall":
        color = "green"
        if agent.status in {"failed"} or agent.health == "red":
            color = "red"
        elif agent.status in {"blocked"} or agent.health in {"yellow", "blocked"}:
            color = "yellow"
        elif agent.status in {"idle"}:
            color = "gray"
        return f"""
        <article class="wall-agent agent-card {status} {color}">
          <span class="handoff-plane" title="{_safe(handoff_label or 'No recent proven handoff')}"></span>
          <div class="wall-agent-head">
            <span class="agent-ring wall-agent-ring {color}"></span>
            <div class="wall-agent-title">
              <h2>{_safe(agent.name)}</h2>
              <span class="badge {status}">{_safe(status)} / {_safe(agent.health)}</span>
            </div>
            <a class="wall-agent-action" href="/agents/{quote(agent.agent_id)}">Update</a>
          </div>
          <p class="wall-agent-time" title="{_safe(timestamp)}">{_safe(_wall_short_timestamp(timestamp))}</p>
          <p class="wall-agent-headline" title="{_safe(headline)}">{_safe(headline)}</p>
          <p class="wall-agent-summary" title="{_safe(summary)}">{_safe(summary)}</p>
          <div class="loadbar wall-loadbar"><span></span></div>
        </article>
        """
    update_block = ""
    if update:
        update_block = f"""
        <p><strong>Latest update:</strong> {_safe(update.headline)}</p>
        <p class="subtle">{_safe(update.created_at)} / cycle {_safe(update.cycle_id)}</p>
        <p>{_safe(update.summary)}</p>
        """
    return f"""
    <article class="agent-card {status}">
      <div class="agent-ring"></div>
      <h2>{_safe(agent.name)}</h2>
      <p>{_safe(agent.division)}</p>
      <p>{_safe(agent.responsibility)}</p>
      <span class="badge {status}">{_safe(status)} / {_safe(agent.health)}</span>
      <p><strong>Current task:</strong> {_safe(agent.current_task or "none")}</p>
      <p>{_safe(agent.last_message)}</p>
      {update_block}
      <a class="button secondary" href="/agents/{quote(agent.agent_id)}">Update History</a>
      <div class="loadbar"><span></span></div>
    </article>
    """


def _wall_color(ok: bool) -> str:
    return "green" if ok else "yellow"


def _wall_status_pill(message: str | None) -> str:
    if not message:
        return ""
    compact = message.split(". ", 1)[0].strip()
    return f"<div class='wall-status-pill' title='{_safe(message)}'>{_safe(compact)}</div>"


def _wall_handoff_state(output_dir: Path, summary: dict) -> dict:
    labels: dict[str, str] = {}
    tasks = list_agent_tasks(output_dir)
    for task in tasks[:12]:
        if task.related_scan_id and task.agent_id == "market":
            labels["market"] = f"Scan {task.related_scan_id} delivered for evidence review"
        if task.related_scan_id and task.agent_id == "evidence":
            labels["evidence"] = f"Evidence reviewed scan {task.related_scan_id}"
        if task.related_report_run_id and task.agent_id == "qa":
            labels["qa"] = f"QA reviewed report {task.related_report_run_id}"
        if task.related_approval_id and task.agent_id == "inbox":
            labels["inbox"] = f"Approval {task.related_approval_id} surfaced to operator queue"
    active = bool(labels) and summary["cycle"].get("completed", 0) and not summary["cycle"].get("failed", 0)
    return {"active": active, "labels": labels}


def _wall_short_timestamp(value: str | None) -> str:
    if not value or value == "none":
        return "none"
    normalized = str(value).replace("Z", "+00:00")
    if "T" in normalized:
        date_part, time_part = normalized.split("T", 1)
        return f"{date_part} {time_part[:5]}"
    return str(value)[:16]


def _wall_short_id(value: str) -> str:
    if not value or value == "none":
        return "none"
    return value if len(value) <= 16 else value[:13] + "..."


def _wall_stat(label: str, value: str, note: str, color: str, title: str | None = None) -> str:
    return f"""
    <article class="wall-stat {color}">
      <span>{_safe(label)}</span>
      <strong title="{_safe(title or str(value))}">{_safe(str(value))}</strong>
      <p>{_safe(note)}</p>
    </article>
    """


def _wall_agent_card(agent, handoff_label: str = "") -> str:
    return _agent_card(agent, "wall", handoff_label)


def _wall_count(label: str, value: int, color: str) -> str:
    return f"<div class='wall-count {color}'><strong>{value}</strong><span>{_safe(label)}</span></div>"


def _wall_inbox_item(item) -> str:
    return f"""
    <div class="wall-inbox-item">
      <strong>{_safe(item.title)}</strong>
      <span>{_safe(item.severity)} / {_safe(item.status)} / created {_safe(_date_part(item.created_at))} {_safe(_time_part(item.created_at))}</span>
      <span>updated {_safe(_date_part(item.updated_at))} {_safe(_time_part(item.updated_at))} / source {_safe(item.source_agent)} / project {_safe(str(item.related_project_id) if item.related_project_id else "none")} / cycle {_safe(item.related_cycle_id or "none")}</span>
    </div>
    """


def _wall_priority_item(item: dict) -> str:
    return f"""
    <div class="wall-inbox-item">
      <strong>{_safe(item.get("ticker", ""))} / Rank {_safe(item.get("rank", ""))}</strong>
      <span>Score {_safe(item.get("score", ""))} / Confidence {_safe(item.get("confidence", ""))} / Evidence {_safe(item.get("evidence", ""))}</span>
    </div>
    """


def _wall_more(total: int, shown: int) -> str:
    remaining = max(0, total - shown)
    return f"<p class='wall-more'>+{remaining} more</p>" if remaining else ""


def _wall_new_leader(daily: dict | None) -> str:
    if not daily:
        return "none"
    for item in daily.get("what_changed", ()):
        if "leader" in str(item).lower():
            return str(item)
    return "none"


def _wall_qa_health(daily: dict | None) -> str:
    if not daily:
        return "no daily cycle"
    for update in daily.get("agent_updates", ()):
        if update.get("agent_name") == "QA Agent":
            return f"{update.get('severity', 'info')}: {update.get('headline', '')}"
    return "QA update unavailable"


def _wall_top_mover(movers) -> str:
    for key in ("rank_improvers", "score_improvers", "confidence_improvers", "evidence_improvers", "deteriorations"):
        rows = movers.get(key, ())
        if rows:
            item = rows[0]
            return f"{item.ticker}: {key.replace('_', ' ')}"
    return "none"


def _wall_opportunity(row: dict[str, str] | None) -> str:
    if not row:
        return "none"
    return f"{row.get('symbol', '')} score {row.get('greenrock_score', '')} confidence {row.get('greenrock_confidence', '')}"


def _agent_run_row(run) -> str:
    return f"""
    <tr>
      <td><a href="/agents/runs/{quote(run.run_id)}">{_safe(run.run_id)}</a></td>
      <td>{_safe(run.agent_id)}</td>
      <td>{_safe(run.status)}</td>
      <td>{_safe(run.completed_at or "")}</td>
      <td>{_safe(run.outputs.get("summary", ""))}</td>
    </tr>
    """


def _atlas_inbox_row(item) -> str:
    return f"""
    <tr>
      <td>{_safe(_date_part(item.created_at))}<br>{_safe(_time_part(item.created_at))}</td>
      <td>{_safe(item.updated_at)}</td>
      <td>{_safe(item.severity)}</td>
      <td>{_safe(item.status)}</td>
      <td><a href="/atlas/inbox/{quote(item.item_id)}">{_safe(item.title)}</a></td>
      <td>{_safe(item.detail)}</td>
      <td>{_safe(item.created_reason or "Created by the local Inbox Agent.")}</td>
      <td>{_safe(item.source_agent)}</td>
      <td>{_safe(str(item.related_project_id) if item.related_project_id else "none")}</td>
      <td>{_safe(item.related_cycle_id or "none")}<br><a href="{_safe(item.target_url)}">Open target</a></td>
      <td>
        <form method="post" action="/atlas/inbox/{quote(item.item_id)}/dismiss" onsubmit="return confirm('Dismiss this local inbox item?');">
          <button type="submit">Dismiss</button>
        </form>
        <form method="post" action="/atlas/inbox/{quote(item.item_id)}/complete" onsubmit="return confirm('Mark this local inbox item complete?');">
          <button type="submit">Complete</button>
        </form>
      </td>
    </tr>
    """


def _date_part(timestamp: str) -> str:
    return timestamp.split("T", 1)[0] if timestamp else "none"


def _time_part(timestamp: str) -> str:
    if not timestamp or "T" not in timestamp:
        return "none"
    return timestamp.split("T", 1)[1].replace("+00:00", " UTC")


def _agent_cycle_diff_block(diff: dict) -> str:
    if not diff:
        return "<section class='panel'><h2>Cycle Diff</h2><p class='empty'>No cycle diff available yet.</p></section>"
    new_items = diff.get("new_inbox_items", []) or []
    resolved = diff.get("resolved_or_dismissed_items", []) or []
    return f"""
    <section class="panel">
      <div class="section-head"><h2>Cycle Diff</h2><span class="subtle">What changed since the prior cycle</span></div>
      <section class="board-meta">
        {_attention_card("neutral", str(len(new_items)), "New Inbox Items", "Created this cycle")}
        {_attention_card("green", str(len(resolved)), "Resolved/Dismissed", "Closed since cycle start")}
        {_attention_card("red" if diff.get("new_provider_failures", 0) else "neutral", str(diff.get("new_provider_failures", 0)), "New Provider Failures", "Compared with prior cycle")}
        {_attention_card("yellow" if diff.get("changed_approval_counts", 0) else "neutral", str(diff.get("changed_approval_counts", 0)), "Approval Count Change", "Pending approvals")}
      </section>
      <h3>Scan / Memory Changes</h3>
      <ul class="compact-list">{''.join(f"<li>{_safe(item)}</li>" for item in diff.get("new_scan_memory_changes", []) or ["No scan or memory change detected."])}</ul>
      <h3>Report Readiness Changes</h3>
      <ul class="compact-list">{''.join(f"<li>{_safe(item)}</li>" for item in diff.get("new_report_readiness_changes", []) or ["No report readiness change detected."])}</ul>
    </section>
    """


def _agent_run_link(run_id: str | None) -> str:
    return f'<a href="/agents/runs/{quote(run_id)}">{_safe(run_id)}</a>' if run_id else "none"


def _report_run_link(run_id: str | None) -> str:
    return f'<a href="/greenrock/reports/{quote(run_id)}/review">{_safe(run_id)}</a>' if run_id else "none"


def _approval_link(approval_id) -> str:
    return f'<a href="/approvals/{approval_id}">{_safe(approval_id)}</a>' if approval_id else "none"


def _nav_card(title: str, href: str, detail: str) -> str:
    return f"<a class='nav-card' href='{href}'><h2>{_safe(title)}</h2><p>{_safe(detail)}</p></a>"


def _path_block(path: str | None, label: str) -> str:
    if not path:
        return "<p class='empty'>Not available yet.</p>"
    return f"<p class='path'>{_safe(path)}</p>{_open_link(path, label)}"


def _path_action(path: str | None, label: str) -> str:
    if not path:
        return f"<span class='button disabled'>{_safe(label)} unavailable</span>"
    return _open_link(path, label)


def _review_report_action(report) -> str:
    if not report or not report.run_id:
        return "<span class='button disabled'>Review Report unavailable</span>"
    return _review_run_button(report.run_id, "Review Report")


def _review_run_link(run_id: str | None) -> str:
    if not run_id:
        return "-"
    return f"<a href='/greenrock/reports/{quote(run_id)}/review'>{_safe(run_id)}</a>"


def _review_run_button(run_id: str | None, label: str) -> str:
    if not run_id:
        return f"<span class='button disabled'>{_safe(label)} unavailable</span>"
    return f"<a class='button secondary' href='/greenrock/reports/{quote(run_id)}/review'>{_safe(label)}</a>"


def _open_link(path: str, label: str) -> str:
    if not path:
        return ""
    return f"<a class='button secondary' href='/open-local?path={quote(path)}'>{_safe(label)}</a>"


def _approval_button(approval, action: str, return_to: str) -> str:
    label = "Approve pending report" if action == "approve" else "Reject pending report"
    if not approval or approval.status != ApprovalStatus.PENDING:
        return f"<span class='button disabled'>{label}</span>"
    return f"<a class='button {'secondary' if action == 'reject' else ''}' href='/approvals/{approval.id}/confirm?action={action}&return_to={quote(return_to)}'>{label}</a>"


def _export_pdf_button(approval, latest_pdf) -> str:
    if latest_pdf:
        return "<span class='button disabled'>PDF already exported</span>"
    if not approval or approval.status != ApprovalStatus.APPROVED:
        return "<span class='button disabled'>Export PDF after approval</span>"
    return f"""
    <form method="post" action="/greenrock/approvals/{approval.id}/export-pdf" onsubmit="return confirm('Export approved report PDF locally?');">
      <button type="submit">Export PDF after approval</button>
    </form>
    """


def _final_packet_hint(approval) -> str:
    if not approval or approval.status != ApprovalStatus.APPROVED:
        return "<span class='button disabled'>Final Packet after approval</span>"
    return f"<span class='button muted-button'>CLI: atlas greenrock final-packet {approval.id}</span>"


def _approval_color(approval) -> str:
    if approval is None:
        return "neutral"
    if approval.status == ApprovalStatus.PENDING:
        return "red"
    if approval.status == ApprovalStatus.APPROVED:
        return "green"
    return "yellow"


def _status_banner(message: str | None) -> str:
    if not message:
        return ""
    return f"<section class='status-banner'>{_safe(message)}</section>"


def _task_status_label(status: str) -> str:
    return {
        "pending": "Backlog",
        "in_progress": "In Progress",
        "awaiting_review": "Review",
        "done": "Done",
    }[status]


def _project_stage_label(status: str) -> str:
    return status.replace("_", " ").title()


def _nav_active(active: str, href: str) -> bool:
    aliases = {
        "/projects": "/pt",
        "/tasks": "/pt",
        "/greenrock/final-reports": "/reports",
    }
    normalized = aliases.get(active, active)
    return normalized == href


def _first(query: dict[str, list[str]], key: str) -> str:
    return query.get(key, [""])[0]


def _with_status(path: str, message: str) -> str:
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    query["status"] = [message]
    return parsed.path + "?" + urlencode({key: values[0] for key, values in query.items()})


def _local_return_to(path: str) -> str:
    parsed = urlparse(path or "/greenrock")
    if parsed.scheme or parsed.netloc:
        return "/greenrock"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _safe(value: object) -> str:
    return html.escape(str(value))


def _static_response(filename: str) -> WebResponse:
    clean = filename.strip().lstrip("/")
    if "/" in clean or ".." in clean:
        return WebResponse(404, "Not found", content_type="text/plain; charset=utf-8")
    path = STATIC_DIR / clean
    if not path.exists():
        return WebResponse(404, "Not found", content_type="text/plain; charset=utf-8")
    content_type = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
    return WebResponse(200, path.read_bytes(), content_type=content_type)


def _greenrock_logo(class_name: str = "greenrock-logo") -> str:
    return (
        f"<img class='{_safe(class_name)}' src='{GREENROCK_LOGO_URL}' "
        "alt='GreenRock bull logo' loading='lazy' onerror=\"this.style.display='none'\">"
    )


def _atlas_logo(class_name: str = "atlas-logo") -> str:
    if ATLAS_LOGO_PATH.exists():
        return (
            f"<img class='{_safe(class_name)}' src='{ATLAS_LOGO_URL}' "
            "alt='Atlas OS logo' loading='lazy' onerror=\"this.outerHTML='<span class=&quot;atlas-mark&quot;>A</span>'\">"
        )
    return "<span class='atlas-mark' title='Atlas OS'>A</span>"


def _greenrock_brand_block() -> str:
    return f"<div class='greenrock-brand'>{_greenrock_logo()}<span>GreenRock Analysts</span></div>"


def _branded_title_hero(title: str, eyebrow: str, subtitle: str, context, metadata: dict[str, str] | None = None) -> str:
    provider = _real_data_provider_status()
    latest_scan_record = latest_scan(get_settings().output_dir)
    latest_refresh = latest_scan_record.scan_id if latest_scan_record else datetime.now().strftime("%Y-%m-%d")
    data_mode = metadata.get("data_mode", context["latest_run"].data_mode.upper() if context.get("latest_run") else "LOCAL") if metadata else (context["latest_run"].data_mode.upper() if context.get("latest_run") else "LOCAL")
    candidate_source = metadata.get("candidate_source", "GreenRock Analysts") if metadata else "GreenRock Analysts"
    return f"""
    <section class="hero branded-hero">
      <div class="brand-title-copy">
        <div class="brand-lockup">
          <div class="brand-logo-pair">{_atlas_logo()}{_greenrock_logo()}</div>
          <div>
            <p class="eyebrow">{_safe(eyebrow)}</p>
            <h1>{_safe(title)}</h1>
            <p>{_safe(subtitle)}</p>
          </div>
        </div>
        <div class="brand-title-line">
          <span>Atlas OS Command Center</span>
          <span>GreenRock Analysts</span>
          <span>Local development mode</span>
        </div>
      </div>
      <div class="brand-status-stack">
        <span class="badge data-mode">{_safe(data_mode)} DATA</span>
        <dl>
          <div><dt>Date / Last Refresh</dt><dd>{_safe(latest_refresh)}</dd></div>
          <div><dt>Mode</dt><dd>Local Only</dd></div>
          <div><dt>Real Data Provider</dt><dd>{_safe(provider["current_provider"] if provider["configured"] == "true" else provider["configured_label"])}</dd></div>
          <div><dt>Candidate Source</dt><dd>{_safe(candidate_source)}</dd></div>
        </dl>
        <p class="subtle">No publish/email/trading enabled.</p>
      </div>
    </section>
    """


def _parse_form(body: str) -> dict[str, str]:
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _route_int_part(route: str, index: int) -> int | None:
    parts = route.split("/")
    if len(parts) <= index:
        return None
    return _parse_int(parts[index])


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _error_response(status: int, title: str, message: str) -> WebResponse:
    body = f"""
    <section class="hero compact">
      <p class="eyebrow">Command Center Notice</p>
      <h1>{_safe(title)}</h1>
      <p>{_safe(message)}</p>
    </section>
    <section class="panel">
      <a class="button secondary" href="/greenrock">Return to GreenRock</a>
    </section>
    """
    return WebResponse(status, _page(title, body, active="/greenrock"))


def _page(title: str, content: str, active: str = "/") -> str:
    provider = _real_data_provider_status()
    nav_groups = (
        ("Executive", (("Command Center", "/"), ("Morning Brief", "/atlas/morning-brief"), ("Atlas Inbox", "/atlas/inbox"), ("Wall", "/atlas/wall"))),
        ("Operations", (("PT", "/pt"), ("Agents", "/agents"), ("Reports", "/reports"))),
        (
            "GreenRock",
            (
                ("Home", "/greenrock"),
                ("Picks", "/greenrock/picks"),
                ("Universe", "/greenrock/universe"),
                ("Market Pulse", "/greenrock/market-pulse"),
                ("Scanner", "/greenrock/scanner"),
                ("Watchlists", "/greenrock/watchlists"),
                ("Staging", "/greenrock/staging"),
                ("Score", "/greenrock/score"),
                ("Report Workbench", "/greenrock/report-workbench"),
            ),
        ),
    )
    nav_html = "".join(
        "<div class='nav-group'>"
        f"<span>{_safe(group)}</span>"
        + "".join(
            f"<a class='{'active' if _nav_active(active, href) else ''}' href='{href}'>{label}</a>"
            for label, href in links
        )
        + "</div>"
        for group, links in nav_groups
    )
    refresh = datetime.now().isoformat(timespec="seconds")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_safe(title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #090b12;
      --panel: rgba(18, 22, 34, 0.88);
      --panel-2: rgba(29, 34, 52, 0.92);
      --ink: #f3f7f2;
      --muted: #a6afbd;
      --line: rgba(255, 255, 255, 0.12);
      --green: #37d67a;
      --purple: #8c6cff;
      --gold: #f3c969;
      --red: #ff5f6d;
      --blue: #64b5ff;
      --neutral: #dbe2ea;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background:
        radial-gradient(circle at 18% 12%, rgba(55, 214, 122, 0.14), transparent 26%),
        radial-gradient(circle at 78% 6%, rgba(140, 108, 255, 0.18), transparent 30%),
        linear-gradient(135deg, #080a10 0%, #111421 52%, #0b1114 100%);
      color: var(--ink);
      min-height: 100vh;
    }}
    body:before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
      background-size: 44px 44px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,.8), transparent 78%);
    }}
    header, main, footer {{ position: relative; z-index: 1; }}
    header {{ padding: 22px 30px 12px; border-bottom: 1px solid var(--line); background: rgba(8, 10, 16, 0.72); backdrop-filter: blur(14px); }}
    header h1 {{ margin: 0 0 5px; font-size: 24px; letter-spacing: 0; }}
    header p {{ margin: 0; color: var(--muted); }}
    nav {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 16px; align-items: stretch; }}
    .nav-group {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; border: 1px solid var(--line); border-radius: 8px; padding: 6px; background: rgba(255,255,255,.035); }}
    .nav-group > span {{ color: var(--gold); font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 0 5px; }}
    nav a, .button, button {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.07);
      color: var(--ink);
      padding: 9px 12px;
      text-decoration: none;
      font: inherit;
      cursor: pointer;
    }}
    nav a.active, button, .button {{ background: linear-gradient(135deg, rgba(55,214,122,.88), rgba(57,157,111,.88)); border-color: rgba(55,214,122,.7); color: #06100b; font-weight: 700; }}
    .button.secondary, button.secondary {{ background: rgba(140,108,255,.16); color: #e8e2ff; border-color: rgba(140,108,255,.45); }}
    .button.disabled {{ opacity: .48; cursor: default; pointer-events: none; }}
    .muted-button {{ background: rgba(255,255,255,.08); color: var(--muted); }}
    main {{ padding: 24px 30px 40px; max-width: 1500px; margin: 0 auto; }}
    section, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 16px;
      box-shadow: 0 18px 50px rgba(0,0,0,.24);
    }}
    h1, h2, h3, p {{ letter-spacing: 0; }}
    h1 {{ margin: 0; font-size: 42px; }}
    h2 {{ margin: 0 0 12px; font-size: 16px; }}
    h3 {{ margin: 0 0 6px; font-size: 15px; }}
    p {{ line-height: 1.45; }}
    .eyebrow {{ margin: 0 0 7px; color: var(--gold); text-transform: uppercase; font-size: 12px; font-weight: 700; }}
    .hero {{ min-height: 210px; display: flex; justify-content: space-between; align-items: center; overflow: hidden; }}
    .hero.compact {{ min-height: 145px; display: block; }}
    .hero p {{ color: var(--muted); margin-bottom: 0; }}
    .hive {{ background: linear-gradient(135deg, rgba(18,22,34,.92), rgba(23,34,39,.88)); }}
    .branded-hero {{ min-height: 250px; gap: 24px; background: linear-gradient(135deg, rgba(7,18,24,.96), rgba(20,38,32,.9) 48%, rgba(28,25,45,.86)); border-color: rgba(55,214,122,.32); }}
    .brand-lockup {{ display: flex; align-items: center; gap: 18px; }}
    .brand-logo-pair {{ display: flex; align-items: center; gap: 10px; flex: 0 0 auto; }}
    .atlas-logo, .atlas-mark {{ width: 58px; height: 58px; object-fit: contain; border-radius: 8px; background: rgba(255,255,255,.07); border: 1px solid rgba(100,181,255,.34); padding: 5px; }}
    .atlas-mark {{ display: inline-grid; place-items: center; color: #d8efff; font-weight: 900; font-size: 28px; box-shadow: 0 0 22px rgba(100,181,255,.18); }}
    .branded-hero .greenrock-logo {{ width: 58px; height: 58px; }}
    .brand-title-copy {{ min-width: 0; }}
    .brand-title-line {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }}
    .brand-title-line span {{ border: 1px solid rgba(55,214,122,.24); border-radius: 999px; padding: 6px 10px; color: #d8ffe6; background: rgba(55,214,122,.08); font-size: 12px; font-weight: 800; }}
    .brand-status-stack {{ width: min(360px, 100%); border: 1px solid rgba(243,201,105,.34); border-radius: 8px; padding: 16px; background: rgba(0,0,0,.2); }}
    .brand-status-stack dl {{ margin: 14px 0 0; display: grid; gap: 9px; }}
    .brand-status-stack div {{ display: grid; grid-template-columns: 135px 1fr; gap: 10px; }}
    .brand-status-stack dt {{ color: var(--muted); font-size: 12px; }}
    .brand-status-stack dd {{ margin: 0; font-weight: 800; overflow-wrap: anywhere; }}
    .brand-status-stack code {{ color: #d8efff; }}
    .command-actions {{ border-color: rgba(55,214,122,.32); background: rgba(55,214,122,.055); }}
    .picks-hero {{ background: linear-gradient(135deg, rgba(7,42,25,.95), rgba(31,24,55,.88) 58%, rgba(50,39,18,.86)); }}
    .greenrock-brand {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; color: #d8ffe6; font-weight: 800; }}
    .greenrock-logo {{ width: 46px; height: 46px; object-fit: contain; border-radius: 8px; background: rgba(255,255,255,.06); border: 1px solid rgba(55,214,122,.22); padding: 4px; }}
    .picks-stamp {{ border: 1px solid rgba(243,201,105,.42); border-radius: 8px; padding: 18px; min-width: 190px; background: rgba(0,0,0,.2); text-align: right; }}
    .picks-stamp strong {{ display: block; font-size: 44px; color: var(--gold); margin-top: 12px; }}
    .orbital {{ width: 170px; aspect-ratio: 1; border: 1px solid rgba(55,214,122,.34); border-radius: 50%; position: relative; animation: spin 18s linear infinite; }}
    .orbital span {{ position: absolute; width: 12px; height: 12px; border-radius: 50%; background: var(--gold); box-shadow: 0 0 20px var(--gold); }}
    .orbital span:nth-child(1) {{ left: 28px; top: 18px; }}
    .orbital span:nth-child(2) {{ right: 20px; top: 72px; background: var(--purple); box-shadow: 0 0 20px var(--purple); }}
    .orbital span:nth-child(3) {{ left: 80px; bottom: 20px; background: var(--green); box-shadow: 0 0 20px var(--green); }}
    @keyframes spin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
    .attention-grid, .nav-grid, .project-grid, .candidate-grid, .detail-grid {{ display: grid; gap: 16px; }}
    .attention-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .board-meta {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }}
    .nav-grid, .project-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .candidate-grid, .detail-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .attention-card, .nav-card, .project-card, .inbox-card, .agent-card, .task-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      padding: 16px;
      color: var(--ink);
      text-decoration: none;
    }}
    .attention-card strong {{ display: block; font-size: 29px; margin-bottom: 8px; overflow-wrap: anywhere; }}
    .attention-card.red {{ border-color: rgba(255,95,109,.5); }}
    .attention-card.yellow {{ border-color: rgba(243,201,105,.55); }}
    .attention-card.green {{ border-color: rgba(55,214,122,.5); }}
    .attention-card.neutral {{ border-color: rgba(219,226,234,.28); }}
    .section-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }}
    .subtle, .empty {{ color: var(--muted); }}
    .inbox-list {{ display: grid; gap: 10px; }}
    .inbox-card {{ display: grid; grid-template-columns: 24px 1fr auto; gap: 12px; align-items: center; }}
    .inbox-card .check {{ width: 14px; height: 14px; border: 2px solid var(--green); border-radius: 4px; box-shadow: 0 0 16px rgba(55,214,122,.42); }}
    .inbox-card.attention .check {{ border-color: var(--red); box-shadow: 0 0 16px rgba(255,95,109,.42); }}
    .inbox-card.ready .check {{ border-color: var(--gold); box-shadow: 0 0 16px rgba(243,201,105,.42); }}
    .inbox-card.placeholder {{ opacity: .72; }}
    .nav-card:hover, .project-card:hover, .inbox-card:hover {{ border-color: rgba(55,214,122,.5); transform: translateY(-1px); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    table {{ table-layout: fixed; }}
    td, th {{ overflow-wrap: anywhere; word-break: normal; }}
    td.actions, .actions {{ min-width: 0; }}
    .staging-add-form, .inline-trim-form, .task-moves {{ max-width: 100%; flex-wrap: wrap; }}
    .staging-add-form select, .staging-add-form button, .inline-trim-form button {{ max-width: 100%; }}
    a {{ color: #b9fff0; }}
    .path {{ font-family: Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 4px 9px; background: rgba(219,226,234,.12); color: var(--neutral); font-size: 12px; white-space: nowrap; }}
    .badge.pending, .badge.attention {{ background: rgba(255,95,109,.16); color: #ffc8ce; }}
    .badge.approved, .badge.done, .badge.active, .badge.completed, .badge.awaiting_approval {{ background: rgba(55,214,122,.14); color: #b9ffd3; }}
    .badge.rejected, .badge.failed {{ background: rgba(243,201,105,.14); color: #ffe5a3; }}
    .badge.planned, .badge.inactive {{ background: rgba(140,108,255,.15); color: #ddd5ff; }}
    .badge.signal {{ background: rgba(243,201,105,.14); color: #ffe3a1; }}
    .badge.selection {{ background: rgba(140,108,255,.15); color: #ddd5ff; }}
    .badge.data-mode {{ background: rgba(55,214,122,.16); color: #c8ffda; border: 1px solid rgba(55,214,122,.3); }}
    .ticker-cloud {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .ticker-cloud span {{ border: 1px solid rgba(55,214,122,.28); background: rgba(55,214,122,.08); border-radius: 999px; padding: 5px 9px; color: #d6ffe4; font-size: 12px; }}
    .universe-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .universe-panel {{ border: 1px solid rgba(255,255,255,.1); border-radius: 8px; padding: 12px; background: rgba(255,255,255,.04); }}
    .warning-panel {{ border-color: rgba(243,201,105,.48); background: rgba(243,201,105,.08); }}
    .mega-pick {{ border-color: rgba(243,201,105,.38); background: linear-gradient(135deg, rgba(27,32,41,.92), rgba(30,44,34,.84)); }}
    .mega-card {{ display: grid; grid-template-columns: 260px 1fr 1.15fr; gap: 18px; align-items: start; }}
    .mega-card h2 {{ font-size: 40px; margin-top: 12px; color: var(--gold); }}
    .mega-card dl {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 0; }}
    .mega-card dt {{ font-size: 12px; }}
    .mega-card dd {{ font-weight: 700; }}
    .screened-in {{ border: 1px solid rgba(55,214,122,.22); border-radius: 8px; padding: 12px; background: rgba(55,214,122,.06); }}
    .compact-list {{ margin: 0; padding-left: 18px; color: #dfe9e3; }}
    .compact-list li {{ margin: 0 0 4px; }}
    .picks-panel {{ overflow-x: auto; }}
    .picks-table {{ min-width: 1780px; }}
    .picks-table th:nth-child(11), .picks-table td:nth-child(11) {{ min-width: 220px; }}
    .calculator-card {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; border-color: rgba(55,214,122,.38); }}
    .score-tool-hero {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, .5fr); gap: 22px; align-items: end; }}
    .score-tool-hero .score-form {{ margin: 0; }}
    .score-form {{ display: grid; grid-template-columns: minmax(150px, 1fr) auto; gap: 10px; }}
    .logo-score-button {{ display: inline-flex; align-items: center; justify-content: center; width: 54px; min-width: 54px; height: 44px; padding: 6px; }}
    .logo-score-button:hover, .logo-score-button:focus {{ transform: translateY(-1px); box-shadow: 0 0 0 3px rgba(55,214,122,.22); outline: none; }}
    .score-button-logo {{ width: 32px; height: 32px; object-fit: contain; }}
    .save-list-panel {{ border-color: rgba(55,214,122,.28); }}
    .save-list-form {{ display: grid; grid-template-columns: minmax(150px, .6fr) minmax(220px, 1fr) auto; gap: 10px; }}
    .inline-promote-form {{ display: grid; grid-template-columns: minmax(170px, 1fr) auto; gap: 8px; min-width: 300px; }}
    .inline-promote-form select, .inline-promote-form button {{ padding: 8px; }}
    .workflow-stepper {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; background: rgba(55,214,122,.08); border-color: rgba(55,214,122,.28); }}
    .workflow-stepper span {{ display: block; text-align: center; border: 1px solid rgba(55,214,122,.24); border-radius: 8px; padding: 12px; color: #d7ffe4; font-weight: 800; background: rgba(0,0,0,.16); }}
    .workflow-grid, .watchlist-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .discovery-flow-panel {{ border-color: rgba(55,214,122,.34); background: rgba(55,214,122,.055); }}
    .discovery-flow-panel .workflow-grid {{ margin-top: 14px; }}
    .workflow-card {{ min-height: 170px; margin: 0; background: rgba(255,255,255,.045); }}
    .workflow-card span {{ display: inline-grid; place-items: center; width: 32px; height: 32px; border-radius: 999px; color: #06100b; background: var(--green); font-weight: 900; margin-bottom: 12px; }}
    .discovery-hero h1 {{ max-width: 980px; }}
    .scanner-filter-form {{ display: grid; grid-template-columns: repeat(5, minmax(145px, 1fr)) auto auto; gap: 10px; align-items: end; }}
    .scanner-filter-form label {{ display: grid; gap: 6px; color: var(--muted); font-size: 12px; font-weight: 700; }}
    .scan-review-form {{ min-width: 1780px; }}
    .promotion-bar {{ position: sticky; left: 0; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; padding: 10px; margin-bottom: 12px; border: 1px solid rgba(55,214,122,.25); border-radius: 8px; background: rgba(55,214,122,.08); }}
    .promotion-bar select {{ min-width: 240px; }}
    .inline-trim-form {{ display: inline-flex; margin-left: 6px; vertical-align: middle; }}
    .inline-trim-form button {{ padding: 7px 10px; font-size: 12px; }}
    .watchlist-card {{ overflow-x: auto; }}
    .watchlist-card table {{ min-width: 560px; }}
    .staging-grid {{ display: grid; gap: 14px; }}
    .staging-bucket {{ overflow-x: auto; }}
    .staging-table {{ min-width: 0; width: 100%; }}
    .staging-add-form {{ display: grid; grid-template-columns: minmax(90px, .55fr) minmax(140px, .75fr) minmax(160px, 1fr) auto; gap: 8px; }}
    .staging-add-form.compact-add {{ grid-template-columns: minmax(80px, .55fr) minmax(120px, .75fr) minmax(130px, 1fr) auto; min-width: 0; }}
    .staging-actions, .staging-actions form {{ display: grid; gap: 8px; }}
    .staging-notes-form {{ display: grid; gap: 8px; min-width: 220px; }}
    .staging-notes-form textarea {{ min-height: 76px; }}
    .report-review-meta .detail-panel p {{ overflow-wrap: anywhere; }}
    .report-review-section {{ overflow-x: auto; }}
    .review-table-wrap {{ overflow-x: auto; margin: 10px 0 16px; }}
    .review-table {{ min-width: 760px; }}
    .review-table th {{ color: var(--gold); background: rgba(23,76,60,.82); }}
    .review-table td, .review-table th {{ vertical-align: top; }}
    .save-status {{ color: #c9ffdc; font-weight: 700; }}
    .setup-box {{ border: 1px solid rgba(243,201,105,.32); border-radius: 8px; padding: 12px; background: rgba(0,0,0,.18); margin: 12px 0; }}
    .setup-box pre {{ margin: 8px 0 0; white-space: pre-wrap; color: #ffe5a3; }}
    .score-result {{ border-color: rgba(55,214,122,.42); background: linear-gradient(135deg, rgba(27,32,41,.96), rgba(22,39,31,.9)); }}
    .score-intel-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 14px 0 18px; }}
    .score-hero-line {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 14px 0 18px; padding: 16px; border: 1px solid rgba(243,201,105,.28); border-radius: 8px; background: rgba(243,201,105,.07); }}
    .score-gauge, .priority-card {{ min-width: 180px; border: 1px solid rgba(243,201,105,.28); border-radius: 8px; padding: 16px; background: rgba(243,201,105,.07); }}
    .confidence-card {{ border-color: rgba(55,214,122,.34); background: rgba(55,214,122,.08); }}
    .priority-card {{ border-color: rgba(140,108,255,.38); background: rgba(140,108,255,.12); }}
    .score-gauge strong {{ display: block; font-size: 44px; color: var(--gold); line-height: 1; }}
    .confidence-card strong {{ color: #b9ffd3; }}
    .confidence-band {{ display: inline-block; margin-top: 8px; color: #c9ffdc; font-weight: 800; }}
    .confidence-explain-card {{ border-color: rgba(55,214,122,.26); }}
    .confidence-explain-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .confidence-explain-grid div {{ border: 1px solid rgba(255,255,255,.08); border-radius: 8px; padding: 12px; background: rgba(255,255,255,.035); }}
    .confidence-badge {{ background: rgba(55,214,122,.14); color: #b9ffd3; border: 1px solid rgba(55,214,122,.28); }}
    .evidence-engine-card {{ border-color: rgba(243,201,105,.26); background: rgba(243,201,105,.045); overflow-x: auto; }}
    .evidence-table {{ min-width: 920px; margin-top: 14px; }}
    .evidence-bullish {{ background: rgba(55,214,122,.14); color: #b9ffd3; border: 1px solid rgba(55,214,122,.28); }}
    .evidence-bearish {{ background: rgba(255,95,109,.14); color: #ffc8ce; border: 1px solid rgba(255,95,109,.28); }}
    .evidence-neutral {{ background: rgba(243,201,105,.14); color: #ffe3a1; border: 1px solid rgba(243,201,105,.28); }}
    .fundamental-guardrail-card {{ border-color: rgba(185,255,240,.24); background: rgba(185,255,240,.045); }}
    .guardrail-badge {{ background: rgba(185,255,240,.13); color: #d4fff7; border: 1px solid rgba(185,255,240,.28); }}
    .priority-card .priority {{ display: inline-block; margin-bottom: 12px; background: rgba(243,201,105,.16); color: #ffe3a1; border: 1px solid rgba(243,201,105,.32); font-size: 14px; }}
    .analyst-summary {{ border-color: rgba(55,214,122,.28); background: rgba(55,214,122,.06); margin-bottom: 12px; }}
    .analyst-summary p {{ margin-bottom: 0; }}
    .analyst-summary .summary-action {{ margin-top: 12px; }}
    .evidence-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .evidence-card {{ min-height: 180px; }}
    .bullish-card {{ border-color: rgba(55,214,122,.3); }}
    .bearish-card {{ border-color: rgba(255,95,109,.28); }}
    .watch-next-card {{ border-color: rgba(100,181,255,.28); }}
    .rank-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .rank-band {{ border: 1px solid rgba(255,255,255,.1); border-radius: 8px; padding: 12px; background: rgba(255,255,255,.04); min-height: 112px; }}
    .rank-band strong {{ color: #f4f8f0; }}
    .rank-band.active-rank {{ border-color: rgba(55,214,122,.58); background: rgba(55,214,122,.12); box-shadow: inset 0 0 0 1px rgba(55,214,122,.18); }}
    .score-explainer {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .score-explainer div {{ border: 1px solid rgba(255,255,255,.1); border-radius: 8px; padding: 12px; background: rgba(255,255,255,.04); }}
    .score-explainer span {{ display: inline-block; margin: 7px 0; color: var(--gold); font-weight: 700; }}
    .score-breakdown-card {{ border-color: rgba(243,201,105,.3); }}
    .score-breakdown-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .component-card {{ border: 1px solid rgba(255,255,255,.1); border-radius: 8px; padding: 13px; background: rgba(255,255,255,.04); }}
    .component-card h3 {{ margin: 0; font-size: 16px; color: #f4f8f0; }}
    .component-card p {{ margin-bottom: 0; color: var(--muted); }}
    .component-card dl {{ display: grid; gap: 8px; margin: 12px 0 0; }}
    .component-card dl div {{ display: grid; grid-template-columns: 122px minmax(0, 1fr); gap: 10px; }}
    .component-topline {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; }}
    .component-topline span {{ color: var(--gold); font-weight: 800; white-space: nowrap; }}
    .price-target-panel {{ overflow-x: auto; }}
    .target-assumptions {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 0 0 12px; }}
    .target-assumptions div {{ border: 1px solid rgba(255,255,255,.08); border-radius: 8px; padding: 10px; background: rgba(255,255,255,.035); }}
    .price-target-table tr.target-below-ath td:last-child {{ color: #ffc4d3; font-weight: 800; }}
    .price-target-table tr.target-above-ath td:last-child {{ color: #b9ffd3; font-weight: 800; }}
    .price-target-table tr.ath-row td:last-child {{ color: var(--gold); font-weight: 800; }}
    .target-warning {{ padding: 12px; border-radius: 8px; }}
    .inner-panel {{ margin-top: 12px; box-shadow: none; }}
    .actions, .action-row, .confirm-form {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .action-row form {{ margin: 0; }}
    .task-form {{ display: grid; grid-template-columns: minmax(220px, 1fr) 260px minmax(260px, 1fr) auto; gap: 10px; }}
    input, select, textarea {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; font: inherit; color: var(--ink); background: rgba(255,255,255,.07); }}
    textarea {{ min-height: 42px; resize: vertical; }}
    .kanban {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; align-items: start; }}
    .kanban-column {{ min-height: 250px; background: rgba(0,0,0,.18); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .kanban-column h2 {{ display: flex; justify-content: space-between; color: var(--gold); }}
    .task-card {{ margin-bottom: 10px; }}
    .task-card p {{ color: var(--muted); margin: 0 0 10px; }}
    .task-meta {{ display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; margin-bottom: 10px; }}
    .task-moves {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .task-moves button {{ padding: 6px 8px; font-size: 12px; }}
    button:disabled {{ opacity: .38; cursor: default; }}
    .agent-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
    .agent-card {{ position: relative; min-height: 180px; overflow: hidden; }}
    .agent-ring {{ width: 62px; height: 62px; border-radius: 50%; border: 2px solid rgba(55,214,122,.45); border-top-color: var(--gold); animation: spin 9s linear infinite; margin-bottom: 16px; }}
    .agent-card.inactive .agent-ring {{ border-color: rgba(219,226,234,.2); border-top-color: rgba(219,226,234,.55); animation-duration: 18s; }}
    .loadbar {{ height: 6px; background: rgba(255,255,255,.08); border-radius: 999px; overflow: hidden; margin-top: 16px; }}
    .loadbar span {{ display: block; width: 38%; height: 100%; background: linear-gradient(90deg, var(--purple), var(--green)); animation: load 2.8s ease-in-out infinite alternate; }}
    .agent-card.inactive .loadbar span {{ width: 14%; background: rgba(219,226,234,.45); animation: none; }}
    @keyframes load {{ from {{ transform: translateX(-20%); }} to {{ transform: translateX(180%); }} }}
    .detail-list {{ display: grid; gap: 10px; margin: 0; }}
    .detail-list div {{ display: grid; grid-template-columns: 160px 1fr; gap: 12px; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; }}
    .status-banner {{ border-color: rgba(55,214,122,.45); background: rgba(55,214,122,.12); color: #c9ffdc; }}
    footer {{ border-top: 1px solid var(--line); padding: 18px 30px 28px; color: var(--muted); background: rgba(8,10,16,.7); }}
    footer div {{ display: flex; gap: 12px; flex-wrap: wrap; max-width: 1500px; margin: 0 auto; }}
    @media (max-width: 1000px) {{
      .attention-grid, .board-meta, .nav-grid, .project-grid, .candidate-grid, .detail-grid, .kanban, .agent-grid, .task-form, .mega-card, .universe-grid, .score-form, .save-list-form, .score-explainer, .score-tool-hero, .rank-grid, .score-breakdown-grid, .target-assumptions, .score-intel-grid, .evidence-grid, .confidence-explain-grid, .workflow-stepper, .workflow-grid, .watchlist-grid, .scanner-filter-form, .staging-add-form, .staging-add-form.compact-add {{ grid-template-columns: 1fr; }}
      .calculator-card, .score-hero-line {{ align-items: flex-start; flex-direction: column; }}
      main, header, footer {{ padding-left: 16px; padding-right: 16px; }}
      .hero {{ min-height: auto; }}
      .orbital {{ display: none; }}
      h1 {{ font-size: 32px; }}
      table {{ font-size: 13px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Atlas Command Center</h1>
    <p>Development Mode: Local Only. Real Data Provider: {_safe(provider['configured_label'])}. Current Provider: {_safe(provider['current_provider'])}.</p>
    <nav>{nav_html}</nav>
  </header>
  <main>{content}</main>
  <footer>
    <div>
      <span>Atlas Command Center</span>
      <span>Development Mode</span>
      <span>Local Only</span>
      <span>Real Data Provider: {_safe(provider['configured_label'])}</span>
      <span>Current Provider: {_safe(provider['current_provider'])}</span>
      <span>Last Refresh: {refresh}</span>
    </div>
  </footer>
</body>
</html>"""


def _wall_page(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>{_safe(title)}</title>
  <style>
    :root {{
      --bg: #05070b;
      --panel: rgba(16,23,34,.92);
      --ink: #f4fbf7;
      --muted: #a9b7c9;
      --green: #37d67a;
      --yellow: #f3c969;
      --red: #ff5f6d;
      --gray: #7f8a99;
      --purple: #9d7cff;
      --line: rgba(255,255,255,.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at 20% 0%, rgba(55,214,122,.18), transparent 32%),
        linear-gradient(135deg, #05070b 0%, #0d1420 58%, #07110d 100%);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
    }}
    body {{ overflow: hidden; }}
    main {{
      height: 100vh;
      padding: 14px;
      display: grid;
      gap: 10px;
      grid-template-rows: 72px 40px minmax(250px, 34fr) minmax(0, 54fr);
    }}
    .wall-hero, .wall-panel, .wall-stat, .wall-agent, .wall-actions, .agent-room, .system-status-panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 20px 60px rgba(0,0,0,.32);
      overflow: hidden;
    }}
    .wall-hero {{ display: flex; justify-content: space-between; align-items: center; }}
    .wall-brand {{ display: flex; align-items: center; gap: 14px; min-width: 0; }}
    .wall-logo {{ width: 48px; height: 48px; object-fit: contain; border-radius: 8px; background: rgba(255,255,255,.06); padding: 5px; }}
    .atlas-mark {{ display: inline-grid; place-items: center; width: 48px; height: 48px; border-radius: 8px; background: rgba(255,255,255,.08); font-size: 28px; font-weight: 900; }}
    .wall-brand p {{ margin: 0; color: var(--yellow); text-transform: uppercase; font-weight: 800; letter-spacing: .08em; }}
    h1 {{ margin: 0; font-size: 36px; line-height: 1; }}
    h2 {{ margin: 0 0 7px; font-size: 19px; }}
    p {{ margin: 4px 0; font-size: 14px; line-height: 1.22; color: var(--muted); }}
    .wall-header-status {{ display: flex; gap: 12px; align-items: center; }}
    .wall-status-pill {{ max-width: 280px; border: 1px solid rgba(55,214,122,.52); background: rgba(55,214,122,.16); color: #c9ffdc; border-radius: 999px; padding: 8px 12px; font-size: 13px; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .provider-pill {{ border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; text-align: right; min-width: 170px; }}
    .provider-pill.green {{ border-color: rgba(55,214,122,.6); }}
    .provider-pill.yellow {{ border-color: rgba(243,201,105,.7); }}
    .provider-pill strong, .provider-pill span {{ display: block; }}
    .wall-clock {{ text-align: right; }}
    .wall-clock strong {{ display: block; font-size: 23px; }}
    .wall-clock span {{ color: var(--muted); font-size: 16px; }}
    .wall-intel-row {{ display: grid; grid-template-columns: 1.15fr .85fr .85fr 1fr; gap: 10px; min-height: 0; }}
    .wall-bottom-split {{ display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr); gap: 10px; min-height: 0; }}
    .system-status-panel {{ min-height: 0; }}
    .system-status-panel .section-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 7px; }}
    .wall-status-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-auto-rows: minmax(0, 1fr); gap: 8px; min-height: 0; height: calc(100% - 28px); }}
    .wall-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
    .wall-agent-grid {{ position: relative; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-auto-rows: minmax(0, 1fr); gap: 8px 10px; min-height: 0; height: calc(100% - 30px); }}
    .wall-stat span, .wall-count span, .wall-inbox-item span, .section-head span {{ color: var(--muted); font-size: 12px; }}
    .wall-stat {{ padding: 8px; }}
    .wall-stat strong {{ display: block; margin: 2px 0; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .wall-stat p {{ font-size: 11px; max-height: 28px; overflow: hidden; }}
    .wall-stat.green, .wall-agent.green, .wall-count.green {{ border-color: rgba(55,214,122,.6); }}
    .wall-stat.yellow, .wall-agent.yellow, .wall-count.yellow {{ border-color: rgba(243,201,105,.7); }}
    .wall-stat.red, .wall-agent.red, .wall-count.red {{ border-color: rgba(255,95,109,.7); }}
    .wall-stat.gray, .wall-agent.gray, .wall-count.gray {{ border-color: rgba(127,138,153,.6); }}
    .agent-room {{ position: relative; }}
    .agent-room .section-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
    .agent-room-line {{ position: absolute; left: 5%; right: 5%; top: 55%; height: 1px; background: linear-gradient(90deg, transparent, rgba(55,214,122,.45), transparent); pointer-events: none; }}
    .wall-agent {{ position: relative; min-height: 0; padding: 8px; }}
    .handoff-plane {{ position: absolute; right: -10px; top: 44%; width: 18px; height: 14px; z-index: 4; pointer-events: none; }}
    .handoff-plane::before {{
      content: "";
      position: absolute;
      inset: 1px 0 1px 2px;
      border-left: 14px solid var(--yellow);
      border-top: 6px solid transparent;
      border-bottom: 6px solid transparent;
      filter: drop-shadow(0 0 8px rgba(243,201,105,.38));
      opacity: .76;
      transform: skewX(-12deg);
    }}
    .wall-agent:nth-child(4) .handoff-plane, .wall-agent:last-child .handoff-plane {{ display: none; }}
    /* Compact wall variant of the richer Agent Monitor card language. */
    .wall-agent-head {{ display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; gap: 6px; align-items: center; min-width: 0; }}
    .wall-agent-title {{ min-width: 0; }}
    .wall-agent-head h2 {{ margin: 0; font-size: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .wall-agent-ring {{ width: 18px; height: 18px; border-radius: 999px; flex: 0 0 auto; border: 2px solid var(--gray); box-shadow: 0 0 18px rgba(127,138,153,.45); background: rgba(127,138,153,.15); }}
    .wall-agent-ring.green {{ border-color: var(--green); box-shadow: 0 0 18px rgba(55,214,122,.55); background: rgba(55,214,122,.18); }}
    .wall-agent-ring.yellow {{ border-color: var(--yellow); box-shadow: 0 0 18px rgba(243,201,105,.55); background: rgba(243,201,105,.18); }}
    .wall-agent-ring.red {{ border-color: var(--red); box-shadow: 0 0 18px rgba(255,95,109,.55); background: rgba(255,95,109,.18); }}
    .wall-agent .badge {{ display: inline-block; max-width: 100%; border-radius: 999px; padding: 2px 6px; background: rgba(255,255,255,.1); color: var(--muted); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .wall-agent-action {{ border: 1px solid var(--line); border-radius: 999px; padding: 4px 7px; color: var(--ink); background: rgba(255,255,255,.08); text-decoration: none; font-size: 10px; font-weight: 800; white-space: nowrap; }}
    .wall-agent-time {{ color: var(--yellow); font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .wall-agent-headline {{ color: var(--ink); font-weight: 800; }}
    .wall-agent-summary {{ color: var(--muted); }}
    .wall-loadbar {{ height: 5px; background: rgba(255,255,255,.08); border-radius: 999px; overflow: hidden; margin-top: 6px; }}
    .wall-loadbar span {{ display: block; width: 38%; height: 100%; background: linear-gradient(90deg, var(--purple), var(--green)); animation: wallLoad 2.8s ease-in-out infinite alternate; }}
    .wall-agent.idle .wall-loadbar span {{ width: 14%; background: rgba(127,138,153,.6); animation-duration: 5s; }}
    .wall-agent.failed .wall-loadbar span {{ background: linear-gradient(90deg, var(--red), var(--yellow)); }}
    .wall-agent.blocked .wall-loadbar span {{ background: linear-gradient(90deg, var(--yellow), rgba(255,255,255,.5)); }}
    .agent-status-line {{ display: flex; gap: 6px; align-items: center; margin: 4px 0; }}
    .agent-status-line strong {{ font-size: 14px; }}
    .agent-status-line span {{ border-radius: 999px; padding: 3px 7px; background: rgba(255,255,255,.1); font-size: 12px; }}
    .wall-agent p {{ font-size: 11px; max-height: 28px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
    @keyframes wallLoad {{ from {{ transform: translateX(-20%); }} to {{ transform: translateX(180%); }} }}
    @keyframes handoffPulse {{
      0%, 100% {{ opacity: .45; transform: translateX(0) skewX(-12deg); }}
      50% {{ opacity: 1; transform: translateX(3px) skewX(-12deg); }}
    }}
    .handoff-active .handoff-plane::before {{ animation: handoffPulse 2.8s ease-in-out infinite; }}
    @media (prefers-reduced-motion: reduce) {{
      .handoff-active .handoff-plane::before {{ animation: none; }}
      .wall-loadbar span {{ animation: none; }}
    }}
    .wall-counts {{ display: flex; gap: 6px; margin-bottom: 7px; }}
    .wall-count {{ flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: 6px; background: rgba(255,255,255,.05); }}
    .wall-count strong {{ display: block; font-size: 20px; }}
    .wall-list {{ display: grid; gap: 5px; }}
    .wall-inbox-item {{ border-top: 1px solid var(--line); padding-top: 5px; }}
    .wall-inbox-item strong {{ display: block; font-size: 13px; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .wall-more {{ color: var(--yellow); font-weight: 800; }}
    .wall-actions {{ display: flex; gap: 8px; flex-wrap: nowrap; align-items: center; }}
    .wall-actions-top {{ justify-content: center; }}
    button, .wall-actions a {{
      border: 1px solid rgba(55,214,122,.58);
      background: rgba(55,214,122,.9);
      color: #06100b;
      border-radius: 8px;
      padding: 9px 12px;
      font-size: 15px;
      font-weight: 800;
      text-decoration: none;
      cursor: pointer;
    }}
    .wall-actions a {{ background: rgba(255,255,255,.08); color: var(--ink); border-color: var(--line); }}
    .future-integrations p {{ font-size: 13px; }}
    @media (max-width: 1200px) {{
      body {{ overflow: auto; }}
      main {{ height: auto; grid-template-rows: none; }}
      .wall-intel-row, .wall-bottom-split, .wall-status-grid, .wall-agent-grid {{ grid-template-columns: 1fr; }}
      .wall-hero {{ align-items: flex-start; flex-direction: column; gap: 18px; }}
      h1 {{ font-size: 46px; }}
    }}
  </style>
</head>
<body class="wall-mode"><main>{content}</main></body>
</html>"""


def _real_data_provider_status() -> dict[str, str]:
    diagnostics = provider_diagnostics()
    return {
        "configured": "true" if diagnostics.score_calculator_ready else "false",
        "configured_label": diagnostics.status_label,
        "current_provider": diagnostics.active_provider_name,
    }


class _CommandCenterHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._send(dispatch_request("GET", self.path))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        self._send(dispatch_request("POST", self.path, body))

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, response: WebResponse) -> None:
        self.send_response(response.status)
        if response.location:
            self.send_header("Location", response.location)
        self.send_header("Content-Type", response.content_type)
        self.end_headers()
        if response.body:
            body = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
            self.wfile.write(body)
