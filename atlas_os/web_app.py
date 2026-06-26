"""Local Atlas Command Center web app."""

from __future__ import annotations

import csv
import html
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from atlas_os import __version__
from atlas_os.config import get_settings
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
    TASK_STATUSES,
    create_manual_task,
    list_manual_tasks,
    update_manual_task_status,
)
from atlas_os.core.reports import get_report, list_reports
from atlas_os.core.workflow_runs import get_workflow_run, list_workflow_runs
from atlas_os.db.database import connect, initialize_database
from atlas_os.greenrock.pdf_export import render_markdown_report_to_pdf
from atlas_os.greenrock.market_data import MarketDataConfigurationError
from atlas_os.greenrock.score import calculate_score_preview, score_signal
from atlas_os.greenrock.universe import GREENROCK_PLACEMENT_LABELS, add_ticker_to_greenrock_list, load_greenrock_universes
from atlas_os.greenrock.workflow import run_greenrock_screening_workflow
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
    body: str
    content_type: str = "text/html; charset=utf-8"
    location: str | None = None


def create_app():
    """Return a FastAPI app when FastAPI is installed."""
    try:
        from fastapi import FastAPI, Form
        from fastapi.responses import HTMLResponse, RedirectResponse
    except ImportError:
        return None

    app = FastAPI(title="Atlas Command Center")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return render_dashboard()

    @app.get("/projects", response_class=HTMLResponse)
    def projects() -> str:
        return render_projects()

    @app.get("/greenrock", response_class=HTMLResponse)
    def greenrock() -> str:
        return render_greenrock()

    @app.get("/greenrock/picks", response_class=HTMLResponse)
    def greenrock_picks() -> str:
        return render_greenrock_picks_board()

    @app.get("/greenrock/score", response_class=HTMLResponse)
    def greenrock_score() -> str:
        return render_greenrock_score()

    @app.post("/greenrock/score", response_class=HTMLResponse)
    def greenrock_score_post(ticker: str = Form(""), data_mode: str = Form("mock"), selection_mode: str = Form("")) -> str:
        return render_greenrock_score(ticker=ticker, data_mode=data_mode, selection_mode=selection_mode)

    @app.post("/greenrock/run-report")
    def run_greenrock_report(data_mode: str = Form("mock")):
        ok, message = run_greenrock_report_from_browser(data_mode)
        return RedirectResponse(_with_status("/greenrock", message), status_code=303)

    @app.get("/greenrock/final-reports", response_class=HTMLResponse)
    def final_reports() -> str:
        return render_greenrock_final_reports()

    @app.get("/approvals/{approval_id}", response_class=HTMLResponse)
    def approval_detail(approval_id: int) -> str:
        return render_approval_detail(approval_id)

    @app.get("/approvals/{approval_id}/confirm", response_class=HTMLResponse)
    def approval_confirm(approval_id: int, action: str = "approve") -> str:
        return render_approval_confirmation(approval_id, action)

    @app.post("/approvals/{approval_id}/decide")
    def approval_decide(approval_id: int, action: str = Form(...), return_to: str = Form("/greenrock")):
        decide_approval(approval_id, action)
        return RedirectResponse(_with_status(return_to, f"Approval {approval_id} {action}d."), status_code=303)

    @app.post("/greenrock/approvals/{approval_id}/export-pdf")
    def export_pdf(approval_id: int):
        export_greenrock_pdf(approval_id)
        return RedirectResponse(_with_status("/greenrock", f"PDF exported for approval {approval_id}."), status_code=303)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks() -> str:
        return render_tasks()

    @app.post("/tasks")
    def create_task(name: str = Form(...), division: str = Form("general"), notes: str = Form("")):
        save_manual_task(name, division, notes)
        return RedirectResponse(_with_status("/tasks", "Manual task created."), status_code=303)

    @app.post("/tasks/{task_id}/status")
    def update_task(task_id: int, status: str = Form(...)):
        save_task_status(task_id, status)
        return RedirectResponse(_with_status("/tasks", f"Task {task_id} updated."), status_code=303)

    @app.get("/agents", response_class=HTMLResponse)
    def agents() -> str:
        return render_agents()

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(run_id: str) -> str:
        return render_run_detail(run_id)

    @app.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
    def artifact_detail(artifact_id: int) -> str:
        return render_artifact_detail(artifact_id)

    @app.get("/reports", response_class=HTMLResponse)
    def reports() -> str:
        return render_reports()

    @app.get("/open-local")
    def open_local(path: str):
        open_local_path(path)
        return RedirectResponse("/", status_code=303)

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
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
    print(f"Atlas Command Center running at http://{host}:{port}")
    print("Local development mode. Mock data only. No publish or send controls.")
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

    if method == "GET" and route == "/":
        return WebResponse(200, render_dashboard(_first(query, "status")))
    if method == "GET" and route == "/projects":
        return WebResponse(200, render_projects(_first(query, "status")))
    if method == "GET" and route == "/greenrock":
        return WebResponse(200, render_greenrock(_first(query, "status")))
    if method == "GET" and route == "/greenrock/picks":
        return WebResponse(200, render_greenrock_picks_board(_first(query, "status")))
    if method == "GET" and route == "/greenrock/score":
        return WebResponse(200, render_greenrock_score())
    if method == "GET" and route == "/greenrock/final-reports":
        return WebResponse(200, render_greenrock_final_reports(_first(query, "status")))
    if method == "GET" and route == "/tasks":
        return WebResponse(200, render_tasks(_first(query, "status")))
    if method == "GET" and route == "/agents":
        return WebResponse(200, render_agents(_first(query, "status")))
    if method == "GET" and route == "/reports":
        return WebResponse(200, render_reports(_first(query, "status")))
    if method == "GET" and route == "/open-local":
        open_local_path(_first(query, "path"))
        return WebResponse(303, "", location="/")
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
            return WebResponse(200, render_approval_confirmation(approval_id, _first(query, "action") or "approve"))
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
        save_manual_task(form.get("name", ""), form.get("division", "general"), form.get("notes", ""))
        return WebResponse(303, "", location=_with_status("/tasks", "Manual task created."))
    if method == "POST" and route == "/greenrock/run-report":
        ok, message = run_greenrock_report_from_browser(form.get("data_mode", "mock"))
        return WebResponse(303, "", location=_with_status("/greenrock", message))
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
        return WebResponse(303, "", location=_with_status("/tasks", f"Task {task_id} updated."))
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
        return WebResponse(303, "", location=_with_status("/greenrock", f"PDF exported for approval {approval_id}."))

    return WebResponse(404, _page("Not Found", "<section class='panel'><h1>Not Found</h1></section>"))


def render_dashboard(status_message: str | None = None) -> str:
    context = _load_context()
    pending_approvals = [approval for approval in context["approvals"] if approval.status == ApprovalStatus.PENDING]
    reports_ready = _approved_reports_missing_pdf(context)
    completed_runs = [run for run in context["runs"] if run.status in {"completed", "approved", "awaiting_approval"}]
    failed_runs = [run for run in context["runs"] if run.status == "failed"]
    inbox_items = _build_inbox_items(context, pending_approvals, reports_ready, failed_runs)
    latest_source = _latest_report_data_source(context["latest_report"])

    content = f"""
    {_status_banner(status_message)}
    <section class="hero hive">
      <div>
        <p class="eyebrow">Atlas Mission Control</p>
        <h1>Atlas Inbox</h1>
        <p>What needs your attention</p>
      </div>
      <div class="orbital"><span></span><span></span><span></span></div>
    </section>
    <section class="attention-grid">
      {_attention_card("red", str(len(pending_approvals)), "Pending Approvals", "Human review required")}
      {_attention_card("yellow", str(len(reports_ready)), "Reports Ready For PDF Export", "Approved locally, PDF not exported")}
      {_attention_card("green", str(len(completed_runs)), "Completed Workflows", "Finished or approval-gated runs")}
      {_attention_card("neutral", _safe(latest_source or "none"), "Latest Data Source", "Shown on the newest GreenRock draft")}
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Atlas Inbox</h2>
        <span class="subtle">Checklist-style operator queue</span>
      </div>
      <div class="inbox-list">{''.join(_inbox_card(item) for item in inbox_items)}</div>
    </section>
    <section class="nav-grid">
      {_nav_card("Project Directory", "/projects", "GreenRock, Bat Signal, Insurance, Atlas Core")}
      {_nav_card("GreenRock Analysts", "/greenrock", "Run latest draft, approvals, candidates")}
      {_nav_card("GreenRock Picks Board", "/greenrock/picks", "Mega Rock, large-cap, and small/mid-cap picks")}
      {_nav_card("Score Any Ticker", "/greenrock/score", "Preview GreenRock Score without report artifacts")}
      {_nav_card("Task Board", "/tasks", "Manual work queue by division")}
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


def render_projects(status_message: str | None = None) -> str:
    context = _load_context()
    cards = []
    for name, division, description, href, status in PROJECTS:
        latest_run = next((run for run in context["runs"] if run.division == division), None)
        task_count = len([task for task in context["tasks"] if task.division == division])
        cards.append(
            f"""
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
        )
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Project Directory</p>
      <h1>Divisions and Workstreams</h1>
      <p>Local project state, placeholders, and links into active pages.</p>
    </section>
    <section class="project-grid">{''.join(cards)}</section>
    """
    return _page("Project Directory", content, active="/projects")


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
      <p class="eyebrow">GreenRock Analysts</p>
      <h1>Report Review Console</h1>
      <p>Mock-data screening output, approval queue, and local final packet controls.</p>
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
          <button type="submit">Run Mock Report</button>
        </form>
        <form method="post" action="/greenrock/run-report" onsubmit="return confirm('Run a new local REAL GreenRock report draft using the configured provider?');">
          <input type="hidden" name="data_mode" value="real">
          <button class="secondary" type="submit">Run Real Report</button>
        </form>
        <a class="button secondary" href="/greenrock/picks">GreenRock Picks Board</a>
        <a class="button secondary" href="/greenrock/score">Score Any Ticker</a>
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
      <p class="subtle">Real mode uses environment tickers first. When blank, Atlas ranks these configured local watchlists. Full-market scanner planned.</p>
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


def render_greenrock_score(
    ticker: str = "",
    status_message: str | None = None,
    save_status: str | None = None,
) -> str:
    settings = get_settings()
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
            result_html = _score_preview_panel(preview)
        except (MarketDataConfigurationError, ValueError) as error:
            result_html = f"""
            <section class="panel warning-panel">
              <h2>Score Preview Blocked</h2>
              <p>{_safe(error)}</p>
              {_score_setup_instructions()}
              <p class="subtle">No report, approval, artifact, email, publication, or external action was created.</p>
            </section>
            """

    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact greenrock-hero score-tool-hero">
      <div>
        <p class="eyebrow">GreenRock Analysts</p>
        <h1>GreenRock Score Calculator</h1>
        <p>Score any ticker against the GreenRock technical dislocation framework.</p>
      </div>
      <form method="post" action="/greenrock/score" class="score-form">
        <input name="ticker" value="{_safe(cleaned_ticker)}" placeholder="Ticker" required>
        <button type="submit">Calculate Score</button>
      </form>
    </section>
    {result_html}
    {_save_ticker_panel(cleaned_ticker, save_status)}
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
        <div><strong>Bonus / penalty factors</strong><span>10 pts</span><p>Adds a bonus below the lower 2.5σ Bollinger Band.</p></div>
      </div>
      <p><a href="/open-local?path={quote(str(Path('docs/GREENROCK_SCORE_METHODOLOGY.md').resolve()))}">Open methodology notes</a></p>
    </section>
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
        verb = "saved to" if placement.added else "already exists in"
        warnings = " ".join(placement.warnings)
        save_status = f"{placement.ticker} {verb} {placement.list_label}. {warnings}".strip()
    except (MarketDataConfigurationError, ValueError) as error:
        save_status = f"Save blocked: {error}"
    return render_greenrock_score(ticker=cleaned_ticker, save_status=save_status)


def render_tasks(status_message: str | None = None) -> str:
    context = _load_context()
    tasks = context["tasks"]
    columns = (
        ("pending", "Backlog"),
        ("in_progress", "In Progress"),
        ("awaiting_review", "Awaiting Review"),
        ("done", "Completed"),
    )
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Task Board</p>
      <h1>Manual Operator Queue</h1>
      <p>Local tasks only. No autonomous execution.</p>
    </section>
    <section class="panel">
      <h2>Create Manual Task</h2>
      <form method="post" action="/tasks" class="task-form">
        <input name="name" required placeholder="Task title">
        <select name="division">
          <option value="greenrock">GreenRock</option>
          <option value="variance-capital">Variance Capital / The Bat Signal</option>
          <option value="greenrock-insurance">GreenRock Insurance</option>
          <option value="atlas-core">Atlas Core</option>
        </select>
        <textarea name="notes" placeholder="Notes"></textarea>
        <button type="submit">Create</button>
      </form>
    </section>
    <section class="kanban">{''.join(_task_column(tasks, status, title) for status, title in columns)}</section>
    """
    return _page("Atlas Task Board", content, active="/tasks")


def render_agents(status_message: str | None = None) -> str:
    cards = "".join(
        f"""
        <article class="agent-card {status}">
          <div class="agent-ring"></div>
          <h2>{_safe(name)}</h2>
          <p>{_safe(division)}</p>
          <span class="badge {status}">{_safe(status)}</span>
          <div class="loadbar"><span></span></div>
        </article>
        """
        for name, division, status in PLANNED_AGENTS
    )
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact agent-hero">
      <p class="eyebrow">Agent Monitor</p>
      <h1>Planned Agent HUD</h1>
      <p>Inactive and planned agents only. Autonomous execution is not enabled.</p>
    </section>
    <section class="agent-grid">{cards}</section>
    """
    return _page("Atlas Agent Monitor", content, active="/agents")


def render_reports(status_message: str | None = None) -> str:
    context = _load_context()
    visible_reports = _visible_report_records(context)
    content = f"""
    {_status_banner(status_message)}
    <section class="hero compact">
      <p class="eyebrow">Artifacts / Reports</p>
      <h1>Local Output Index</h1>
      <p>Run-specific files, report records, and local artifact paths.</p>
    </section>
    <section class="panel">
      <h2>Reports</h2>
      {_reports_table(visible_reports)}
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


def render_approval_confirmation(approval_id: int, action: str) -> str:
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
        <input type="hidden" name="return_to" value="/greenrock">
        <button type="submit">{verb} locally</button>
        <a class="button secondary" href="/greenrock">Cancel</a>
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


def save_manual_task(name: str, division: str, notes: str | None = None) -> None:
    if not name.strip():
        return
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        create_manual_task(connection, name, division, notes.strip() if notes else None)


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
                "href": f"/approvals/{approval.id}/confirm?action=approve",
                "status": "attention",
                "label": "pending approval",
            }
        )
    for report in reports_ready[:3]:
        items.append(
            {
                "title": "Approve PDF Export",
                "detail": f"Run {report.run_id} is approved and missing final PDF.",
                "href": f"/greenrock",
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
        "<th>Price</th><th>GreenRock Score</th><th>Signal</th><th>Selection</th><th>RSI</th><th>52-week Low Distance</th>"
        "<th>Bollinger Band Status</th><th>Volume Acceleration</th><th>Why It Screened In</th></tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def _score_preview_panel(preview) -> str:
    candidate = preview.candidate
    indicators = candidate.indicators
    warnings = preview.data_quality_warnings or ("none",)
    component_cards = "".join(_score_component_card(component) for component in preview.component_explanations)
    bonus_items = "".join(f"<li>{_safe(item)}</li>" for item in preview.bonus_penalty_explanations)
    warning_items = "".join(f"<li>{_safe(warning)}</li>" for warning in warnings)
    return f"""
    <section class="panel score-result">
      <div class="section-head">
        <h2>{_safe(candidate.symbol)} Score Preview</h2>
        <span class="badge data-mode">{_safe(preview.data_mode.upper())} DATA</span>
      </div>
      <div class="score-hero-line">
        <div class="score-gauge">
          <strong>{candidate.score:.2f}</strong>
          <p>GreenRock Score</p>
        </div>
        <div>
          <span class="badge signal">{_safe(score_signal(candidate))}</span>
          <span class="badge selection">{_safe(candidate.selection_label)}</span>
        </div>
      </div>
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
      <section class="panel inner-panel score-breakdown-card">
        <h2>Score Breakdown</h2>
        <p class="subtle">Each card shows the raw metric, component score, weight, and plain-English rationale before the final 100-point cap.</p>
        <div class="score-breakdown-grid">{component_cards}</div>
      </section>
      <section class="panel inner-panel">
        <h2>Bonus / Penalty Factors</h2>
        <ul class="compact-list">{bonus_items}</ul>
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


def _score_setup_instructions() -> str:
    return """
    <div class="setup-box">
      <p>Configure the real score calculator locally:</p>
      <pre>export ATLAS_MARKET_DATA_PROVIDER=yfinance
python3 -m pip install -e ".[market-data]"</pre>
    </div>
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
        rows.append(
            "<tr>"
            f"<td><a href='/runs/{quote(run.run_id)}'>{_safe(run.run_id)}</a></td>"
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
        f"<td>{_safe(approval.artifact_type)}</td><td>{_safe(approval.run_id or '-')}</td>"
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
        return "<td class='subtle'>decision recorded</td>"
    return f"""
    <td class="actions">
      <a class="button" href="/approvals/{approval.id}/confirm?action=approve">Approve</a>
      <a class="button secondary" href="/approvals/{approval.id}/confirm?action=reject">Reject</a>
    </td>
    """


def _reports_table(reports) -> str:
    if not reports:
        return "<p class='empty'>No reports found.</p>"
    rows = "".join(
        "<tr>"
        f"<td>{report.id}</td><td>{_safe(report.title)}</td><td>{_safe(report.status)}</td>"
        f"<td>{_safe(report.run_id or '-')}</td><td class='path'>{_safe(report.content_path or '-')}</td>"
        "</tr>"
        for report in reports[:30]
    )
    return "<table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Run</th><th>Path</th></tr></thead><tbody>" + rows + "</tbody></table>"


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


def _task_card(task) -> str:
    buttons = "".join(
        f"<button {'disabled' if task.status == status else ''} name='status' value='{status}'>{_task_status_label(status)}</button>"
        for status in TASK_STATUSES
    )
    return f"""
    <article class="task-card">
      <h3>{_safe(task.name)}</h3>
      <p>{_safe(task.notes or "No notes")}</p>
      <div class="task-meta"><span>{_safe(task.division)}</span><span>{_safe(task.updated_at)}</span></div>
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


def _attention_card(color: str, value: str, label: str, note: str) -> str:
    return f"<article class='attention-card {color}'><strong>{value}</strong><h2>{_safe(label)}</h2><p>{_safe(note)}</p></article>"


def _inbox_card(item: dict[str, str]) -> str:
    return f"""
    <a class="inbox-card {item['status']}" href="{item['href']}">
      <span class="check"></span>
      <div>
        <h3>{_safe(item['title'])}</h3>
        <p>{_safe(item['detail'])}</p>
      </div>
      <span class="badge">{_safe(item['label'])}</span>
    </a>
    """


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


def _first(query: dict[str, list[str]], key: str) -> str:
    return query.get(key, [""])[0]


def _with_status(path: str, message: str) -> str:
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    query["status"] = [message]
    return parsed.path + "?" + urlencode({key: values[0] for key, values in query.items()})


def _safe(value: object) -> str:
    return html.escape(str(value))


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
    nav = (
        ("Inbox", "/"),
        ("Projects", "/projects"),
        ("GreenRock", "/greenrock"),
        ("Picks", "/greenrock/picks"),
        ("Score", "/greenrock/score"),
        ("Tasks", "/tasks"),
        ("Agents", "/agents"),
        ("Reports", "/reports"),
    )
    nav_html = "".join(
        f"<a class='{'active' if href == active else ''}' href='{href}'>{label}</a>"
        for label, href in nav
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
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }}
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
    .picks-hero {{ background: linear-gradient(135deg, rgba(7,42,25,.95), rgba(31,24,55,.88) 58%, rgba(50,39,18,.86)); }}
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
    .picks-table {{ min-width: 1180px; }}
    .picks-table th:nth-child(11), .picks-table td:nth-child(11) {{ min-width: 220px; }}
    .calculator-card {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; border-color: rgba(55,214,122,.38); }}
    .score-tool-hero {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, .5fr); gap: 22px; align-items: end; }}
    .score-tool-hero .score-form {{ margin: 0; }}
    .score-form {{ display: grid; grid-template-columns: minmax(150px, 1fr) auto; gap: 10px; }}
    .save-list-panel {{ border-color: rgba(55,214,122,.28); }}
    .save-list-form {{ display: grid; grid-template-columns: minmax(150px, .6fr) minmax(220px, 1fr) auto; gap: 10px; }}
    .save-status {{ color: #c9ffdc; font-weight: 700; }}
    .setup-box {{ border: 1px solid rgba(243,201,105,.32); border-radius: 8px; padding: 12px; background: rgba(0,0,0,.18); margin: 12px 0; }}
    .setup-box pre {{ margin: 8px 0 0; white-space: pre-wrap; color: #ffe5a3; }}
    .score-result {{ border-color: rgba(55,214,122,.42); background: linear-gradient(135deg, rgba(27,32,41,.96), rgba(22,39,31,.9)); }}
    .score-hero-line {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 14px 0 18px; padding: 16px; border: 1px solid rgba(243,201,105,.28); border-radius: 8px; background: rgba(243,201,105,.07); }}
    .score-gauge {{ min-width: 180px; }}
    .score-hero-line strong {{ display: block; font-size: 44px; color: var(--gold); line-height: 1; }}
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
      .attention-grid, .board-meta, .nav-grid, .project-grid, .candidate-grid, .detail-grid, .kanban, .agent-grid, .task-form, .mega-card, .universe-grid, .score-form, .save-list-form, .score-explainer, .score-tool-hero, .rank-grid, .score-breakdown-grid, .target-assumptions {{ grid-template-columns: 1fr; }}
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
    <p>Development Mode. Local Only. Mock Data Unless Otherwise Noted.</p>
    <nav>{nav_html}</nav>
  </header>
  <main>{content}</main>
  <footer>
    <div>
      <span>Atlas Command Center</span>
      <span>Development Mode</span>
      <span>Local Only</span>
      <span>Mock Data Unless Otherwise Noted</span>
      <span>Last Refresh: {refresh}</span>
    </div>
  </footer>
</body>
</html>"""


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
            self.wfile.write(response.body.encode("utf-8"))
