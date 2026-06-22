"""Local Atlas Command Center web app."""

from __future__ import annotations

import csv
import html
import subprocess
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from atlas_os import __version__
from atlas_os.config import get_settings
from atlas_os.core.approvals import (
    ApprovalStatus,
    approve_approval,
    list_approvals,
    reject_approval,
)
from atlas_os.core.artifacts import list_artifacts, list_artifacts_for_run
from atlas_os.core.audit_log import list_audit_logs
from atlas_os.core.manual_tasks import TASK_STATUSES, create_manual_task, list_manual_tasks, update_manual_task_status
from atlas_os.core.reports import list_reports
from atlas_os.core.workflow_runs import list_workflow_runs
from atlas_os.db.database import connect, initialize_database


PLANNED_AGENTS = (
    ("Atlas Core", "core"),
    ("GreenRock Analyst Agent", "greenrock"),
    ("Publisher Agent", "publishing"),
    ("Compliance Review Agent", "compliance"),
    ("Bat Signal Agent", "variance-capital"),
    ("Insurance Follow-Up Agent", "greenrock-insurance"),
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

    @app.get("/greenrock", response_class=HTMLResponse)
    def greenrock() -> str:
        return render_greenrock()

    @app.post("/greenrock/approvals/{approval_id}/approve")
    def approve(approval_id: int):
        decide_approval(approval_id, "approve")
        return RedirectResponse("/greenrock", status_code=303)

    @app.post("/greenrock/approvals/{approval_id}/reject")
    def reject(approval_id: int):
        decide_approval(approval_id, "reject")
        return RedirectResponse("/greenrock", status_code=303)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks() -> str:
        return render_tasks()

    @app.post("/tasks")
    def create_task(name: str = Form(...), division: str = Form("general")):
        save_manual_task(name, division)
        return RedirectResponse("/tasks", status_code=303)

    @app.post("/tasks/{task_id}/status")
    def update_task(task_id: int, status: str = Form(...)):
        save_task_status(task_id, status)
        return RedirectResponse("/tasks", status_code=303)

    @app.get("/agents", response_class=HTMLResponse)
    def agents() -> str:
        return render_agents()

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
    form = _parse_form(body)

    if method == "GET" and route == "/":
        return WebResponse(200, render_dashboard())
    if method == "GET" and route == "/greenrock":
        return WebResponse(200, render_greenrock())
    if method == "GET" and route == "/tasks":
        return WebResponse(200, render_tasks())
    if method == "GET" and route == "/agents":
        return WebResponse(200, render_agents())
    if method == "GET" and route == "/open-local":
        query = parse_qs(parsed.query)
        open_local_path(query.get("path", [""])[0])
        return WebResponse(303, "", location="/")
    if method == "POST" and route == "/tasks":
        save_manual_task(form.get("name", ""), form.get("division", "general"))
        return WebResponse(303, "", location="/tasks")
    if method == "POST" and route.startswith("/tasks/") and route.endswith("/status"):
        task_id = int(route.split("/")[2])
        save_task_status(task_id, form.get("status", "pending"))
        return WebResponse(303, "", location="/tasks")
    if method == "POST" and route.startswith("/greenrock/approvals/"):
        parts = route.strip("/").split("/")
        if len(parts) == 4 and parts[3] in {"approve", "reject"}:
            decide_approval(int(parts[2]), parts[3])
            return WebResponse(303, "", location="/greenrock")

    return WebResponse(404, _page("Not Found", "<section><h1>Not Found</h1></section>"))


def render_dashboard() -> str:
    context = _load_context()
    latest_report = context["latest_report"]
    latest_pdf = context["latest_pdf"]
    pending_approvals = [approval for approval in context["approvals"] if approval.status == ApprovalStatus.PENDING]
    recent_runs = context["runs"][:6]
    artifacts = context["artifacts"][:8]
    audit_logs = context["audit_logs"][:8]

    content = f"""
    <section class="notice">
      <strong>Local development mode</strong>
      <span>Mock data only</span>
      <span>External services disabled</span>
      <span>Human approval required</span>
    </section>
    <section class="grid overview">
      {_metric("Atlas OS overview", f"v{__version__}", "Local workflow command center")}
      {_metric("Recent runs", str(len(context["runs"])), "SQLite-backed workflow history")}
      {_metric("Pending approvals", str(len(pending_approvals)), "Reports remain blocked until approved")}
      {_metric("Artifacts", str(len(context["artifacts"])), "Local files only")}
    </section>
    <section>
      <h2>Project / Division Switcher</h2>
      <div class="switcher">
        <a href="/greenrock">GreenRock</a>
        <span>Variance Capital / The Bat Signal</span>
        <span>GreenRock Insurance</span>
      </div>
    </section>
    <section class="grid two">
      <div>
        <h2>Latest GreenRock Report</h2>
        {_path_block(latest_report.content_path if latest_report else None, "Markdown")}
        <p class="muted">All GreenRock reports are mock-data drafts unless explicitly approved by a human.</p>
      </div>
      <div>
        <h2>Latest GreenRock PDF</h2>
        {_path_block(latest_pdf.path if latest_pdf else None, "PDF")}
        <p class="muted">Approvals unlock local final packet/PDF only. No publish or send controls exist here.</p>
      </div>
    </section>
    <section class="grid two">
      <div>
        <h2>Recent Workflow Runs</h2>
        {_runs_table(recent_runs)}
      </div>
      <div>
        <h2>Pending Approvals</h2>
        {_approvals_table(pending_approvals, actions=False)}
      </div>
    </section>
    <section class="grid two">
      <div>
        <h2>Artifact List</h2>
        {_artifacts_table(artifacts)}
      </div>
      <div>
        <h2>Audit Log Summary</h2>
        {_audit_table(audit_logs)}
      </div>
    </section>
    <section class="grid two">
      <div>
        <h2>Task Queue Placeholder</h2>
        <p class="muted">Manual tasks can be created and moved through local states. Autonomous execution is not enabled.</p>
        <a class="button" href="/tasks">Open Task Board</a>
      </div>
      <div>
        <h2>Agent Activity Placeholder</h2>
        <p class="muted">Planned agents are visible as inactive placeholders until explicitly implemented.</p>
        <a class="button" href="/agents">Open Agent Monitor</a>
      </div>
    </section>
    """
    return _page("Atlas Command Center", content, active="/")


def render_greenrock() -> str:
    context = _load_context()
    latest_run = context["latest_run"]
    latest_report = context["latest_report"]
    latest_pdf = context["latest_pdf"]
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
    artifacts = list_artifacts_for_run(context["connection"], latest_run.run_id) if latest_run else ()
    large_candidates = _candidate_rows(latest_run.output_paths.get("large_cap") if latest_run else None)
    small_candidates = _candidate_rows(latest_run.output_paths.get("small_cap") if latest_run else None)

    content = f"""
    <section class="notice">
      <strong>GreenRock local review</strong>
      <span>Mock-data reports</span>
      <span>Approval gate mandatory</span>
      <span>No publish/send actions</span>
    </section>
    <section class="grid overview">
      {_metric("Latest run status", _safe(latest_run.status if latest_run else "none"), _safe(latest_run.run_id if latest_run else "No run yet"))}
      {_metric("Approval status", _safe(latest_run_approvals[0].status.value if latest_run_approvals else "none"), "Report drafts remain blocked until approved")}
      {_metric("Candidate files", str(len([p for p in (latest_run.output_paths if latest_run else {}) if p in {"large_cap", "small_cap"}])), "Run-specific mock outputs")}
      {_metric("Final PDF", "exported" if latest_pdf else "not exported", _safe(latest_pdf.path if latest_pdf else "Approve and export to create PDF"))}
    </section>
    <section class="grid two">
      <div>
        <h2>Latest Report Path</h2>
        {_path_block(latest_report.content_path if latest_report else None, "Open Markdown")}
      </div>
      <div>
        <h2>Latest PDF Path</h2>
        {_path_block(latest_pdf.path if latest_pdf else None, "Open PDF")}
      </div>
    </section>
    <section class="grid two">
      <div>
        <h2>Top Large-Cap Candidates</h2>
        {_candidate_table(large_candidates)}
      </div>
      <div>
        <h2>Top Small/Mid-Cap Candidates</h2>
        {_candidate_table(small_candidates)}
      </div>
    </section>
    <section>
      <h2>Approvals</h2>
      {_approvals_table(approvals[:12], actions=True)}
    </section>
    <section>
      <h2>Artifacts</h2>
      {_artifacts_table(artifacts)}
    </section>
    """
    return _page("GreenRock Command Center", content, active="/greenrock")


def render_tasks() -> str:
    context = _load_context()
    tasks = context["tasks"]
    content = f"""
    <section class="notice">
      <strong>Manual task board</strong>
      <span>No autonomous execution</span>
      <span>Local SQLite only</span>
    </section>
    <section>
      <h2>Create Manual Task</h2>
      <form method="post" action="/tasks" class="task-form">
        <input name="name" required placeholder="Task name">
        <select name="division">
          <option value="greenrock">GreenRock</option>
          <option value="variance-capital">Variance Capital / The Bat Signal</option>
          <option value="greenrock-insurance">GreenRock Insurance</option>
          <option value="atlas-core">Atlas Core</option>
        </select>
        <button type="submit">Create</button>
      </form>
    </section>
    <section>
      <h2>Task Queue</h2>
      {_tasks_table(tasks)}
    </section>
    """
    return _page("Atlas Task Board", content, active="/tasks")


def render_agents() -> str:
    rows = "".join(
        f"<tr><td>{_safe(name)}</td><td>{_safe(division)}</td><td><span class='pill muted-pill'>inactive placeholder</span></td></tr>"
        for name, division in PLANNED_AGENTS
    )
    content = f"""
    <section class="notice">
      <strong>Agent monitor placeholder</strong>
      <span>Planned agents only</span>
      <span>No autonomous execution</span>
    </section>
    <section>
      <h2>Planned Agents</h2>
      <table><thead><tr><th>Agent</th><th>Division</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>
    </section>
    """
    return _page("Atlas Agent Monitor", content, active="/agents")


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


def save_manual_task(name: str, division: str) -> None:
    if not name.strip():
        return
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    with connect(db_path) as connection:
        create_manual_task(connection, name, division)


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
    }


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


def _candidate_rows(path: str | None, limit: int = 6) -> list[dict[str, str]]:
    if not path:
        return []
    candidate_path = Path(path)
    if not candidate_path.exists():
        return []
    with candidate_path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))[:limit]


def _candidate_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<p class='empty'>No candidates available yet.</p>"
    body = "".join(
        "<tr>"
        f"<td>{_safe(row.get('symbol', ''))}</td>"
        f"<td>{_safe(row.get('company_name', ''))}</td>"
        f"<td>{_safe(row.get('score', ''))}</td>"
        f"<td>{_safe(row.get('signal_label', ''))}</td>"
        "</tr>"
        for row in rows
    )
    return f"<table><thead><tr><th>Symbol</th><th>Name</th><th>Score</th><th>Signal</th></tr></thead><tbody>{body}</tbody></table>"


def _runs_table(runs) -> str:
    if not runs:
        return "<p class='empty'>No workflow runs found.</p>"
    body = "".join(
        "<tr>"
        f"<td>{_safe(run.run_id)}</td><td>{_safe(run.division)}</td>"
        f"<td>{_safe(run.workflow_name)}</td><td>{_safe(run.status)}</td>"
        "</tr>"
        for run in runs
    )
    return f"<table><thead><tr><th>Run</th><th>Division</th><th>Workflow</th><th>Status</th></tr></thead><tbody>{body}</tbody></table>"


def _approvals_table(approvals, actions: bool) -> str:
    if not approvals:
        return "<p class='empty'>No approvals found.</p>"
    action_header = "<th>Action</th>" if actions else ""
    body = "".join(
        "<tr>"
        f"<td>{approval.id}</td><td><span class='pill'>{_safe(approval.status.value)}</span></td>"
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
        return "<td class='muted'>decision recorded</td>"
    return f"""
    <td class="actions">
      <form method="post" action="/greenrock/approvals/{approval.id}/approve" onsubmit="return confirm('Approve this report draft for local final packet/PDF?');">
        <button type="submit">Approve</button>
      </form>
      <form method="post" action="/greenrock/approvals/{approval.id}/reject" onsubmit="return confirm('Reject this report draft?');">
        <button class="secondary" type="submit">Reject</button>
      </form>
    </td>
    """


def _artifacts_table(artifacts) -> str:
    if not artifacts:
        return "<p class='empty'>No artifacts found.</p>"
    body = "".join(
        "<tr>"
        f"<td>{artifact.id}</td><td>{_safe(artifact.artifact_type)}</td>"
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


def _tasks_table(tasks) -> str:
    if not tasks:
        return "<p class='empty'>No manual tasks found.</p>"
    rows = []
    for task in tasks:
        buttons = "".join(
            f"<button {'disabled' if task.status == status else ''} name='status' value='{status}'>{status.replace('_', ' ')}</button>"
            for status in TASK_STATUSES
        )
        rows.append(
            "<tr>"
            f"<td>{task.id}</td><td>{_safe(task.name)}</td><td>{_safe(task.division)}</td><td><span class='pill'>{_safe(task.status)}</span></td>"
            f"<td><form method='post' action='/tasks/{task.id}/status' class='inline-form'>{buttons}</form></td>"
            "</tr>"
        )
    return "<table><thead><tr><th>ID</th><th>Task</th><th>Division</th><th>Status</th><th>Move</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def _path_block(path: str | None, label: str) -> str:
    if not path:
        return "<p class='empty'>Not available yet.</p>"
    return f"<p class='path'>{_safe(path)}</p>{_open_link(path, label)}"


def _open_link(path: str, label: str) -> str:
    if not path:
        return ""
    return f"<a class='button secondary' href='/open-local?path={quote(path)}'>{_safe(label)}</a>"


def _metric(title: str, value: str, note: str) -> str:
    return f"<div class='metric'><h2>{_safe(title)}</h2><strong>{_safe(value)}</strong><p>{_safe(note)}</p></div>"


def _page(title: str, content: str, active: str = "/") -> str:
    nav = (
        ("Command Center", "/"),
        ("GreenRock", "/greenrock"),
        ("Tasks", "/tasks"),
        ("Agents", "/agents"),
    )
    nav_html = "".join(
        f"<a class='{'active' if href == active else ''}' href='{href}'>{label}</a>"
        for label, href in nav
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_safe(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f4;
      --panel: #ffffff;
      --ink: #17211d;
      --muted: #68736f;
      --line: #d9ded8;
      --accent: #1f6b4a;
      --accent-2: #2f5f88;
      --warn: #8a5a18;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; background: var(--bg); color: var(--ink); }}
    header {{ padding: 22px 28px 14px; background: #edf1ea; border-bottom: 1px solid var(--line); }}
    header h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: 0; }}
    header p {{ margin: 0; color: var(--muted); }}
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 18px; }}
    nav a, .button, button {{ border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--ink); padding: 8px 11px; text-decoration: none; font: inherit; cursor: pointer; }}
    nav a.active, button, .button {{ background: var(--accent); border-color: var(--accent); color: white; }}
    button.secondary, .button.secondary {{ background: white; color: var(--accent-2); border-color: #b8c7d3; }}
    main {{ padding: 22px 28px 36px; max-width: 1440px; margin: 0 auto; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 16px; }}
    section h2 {{ margin: 0 0 12px; font-size: 16px; }}
    .notice {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; background: #fffaf0; border-color: #ead7b6; color: var(--warn); }}
    .grid {{ display: grid; gap: 16px; }}
    .overview {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .metric strong {{ display: block; font-size: 26px; margin-bottom: 6px; overflow-wrap: anywhere; }}
    .metric p, .muted, .empty {{ color: var(--muted); }}
    .switcher {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .switcher a, .switcher span {{ border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; text-decoration: none; color: var(--ink); background: #fbfcfb; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    .path {{ font-family: Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }}
    .pill {{ display: inline-block; border-radius: 999px; padding: 3px 8px; background: #e6f1eb; color: #155f3f; font-size: 12px; }}
    .muted-pill {{ background: #eef0f0; color: var(--muted); }}
    .actions, .inline-form {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .actions form {{ margin: 0; }}
    .task-form {{ display: grid; grid-template-columns: minmax(220px, 1fr) 260px auto; gap: 10px; }}
    input, select {{ border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; font: inherit; }}
    button:disabled {{ opacity: 0.45; cursor: default; }}
    @media (max-width: 900px) {{
      .overview, .two, .task-form {{ grid-template-columns: 1fr; }}
      main, header {{ padding-left: 16px; padding-right: 16px; }}
      table {{ font-size: 13px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{_safe(title)}</h1>
    <p>Atlas OS local command center. External services disabled; mock data and human approvals remain mandatory.</p>
    <nav>{nav_html}</nav>
  </header>
  <main>{content}</main>
</body>
</html>"""


def _safe(value: object) -> str:
    return html.escape(str(value))


def _parse_form(body: str) -> dict[str, str]:
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


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
