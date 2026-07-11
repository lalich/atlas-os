"""Read-only GreenRock report dry-run assembly."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, time, timedelta
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas_os.greenrock.derivatives import latest_derivative_analysis
from atlas_os.greenrock.scanner import latest_scan
from atlas_os.greenrock.staging import STAGING_BUCKET_LABELS, load_staged_candidates

DEFAULT_SCHEDULE_TIMEZONE = "America/New_York"
DEFAULT_MONTH_END_TIME = time(19, 0)
DEFAULT_SUNDAY_REFRESH_TIME = time(11, 0)


@dataclass(frozen=True)
class ReportDryRunScheduleConfig:
    timezone: str = DEFAULT_SCHEDULE_TIMEZONE
    month_end_hour: int = DEFAULT_MONTH_END_TIME.hour
    month_end_minute: int = DEFAULT_MONTH_END_TIME.minute
    sunday_refresh_enabled: bool = True
    sunday_refresh_hour: int = DEFAULT_SUNDAY_REFRESH_TIME.hour
    sunday_refresh_minute: int = DEFAULT_SUNDAY_REFRESH_TIME.minute
    market_holidays: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportDryRunOccurrence:
    occurrence_id: str
    scheduled_for: str
    schedule_reason: str
    review_required: bool = True


def report_dry_run_dir(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / "report_dry_runs"


def report_schedule_config_path(output_dir: Path) -> Path:
    return report_dry_run_dir(output_dir) / "schedule_config.json"


def report_schedule_ledger_path(output_dir: Path) -> Path:
    return report_dry_run_dir(output_dir) / "schedule_runs.json"


def create_report_dry_run(
    output_dir: Path,
    scheduled_for: str = "manual",
    schedule_reason: str = "manual_dry_run",
    occurrence_id: str | None = None,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    dry_run_id = f"report-dry-run-{occurrence_id}-{timestamp}" if occurrence_id else f"report-dry-run-{timestamp}"
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    markdown = build_report_dry_run_markdown(
        output_dir,
        dry_run_id=dry_run_id,
        scheduled_for=scheduled_for,
        generated_at=generated_at,
        schedule_reason=schedule_reason,
    )
    directory = report_dry_run_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dry_run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    if occurrence_id:
        _record_schedule_run(output_dir, occurrence_id, scheduled_for, generated_at, schedule_reason, path)
    return path


def build_report_dry_run_markdown(
    output_dir: Path,
    dry_run_id: str = "local-dry-run",
    scheduled_for: str = "manual",
    generated_at: str | None = None,
    schedule_reason: str = "manual_dry_run",
) -> str:
    scan = latest_scan(output_dir)
    staged = load_staged_candidates(output_dir)
    derivatives = latest_derivative_analysis(output_dir)
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# GreenRock Report Agent Dry Run",
        "",
        f"**Dry Run ID:** {dry_run_id}",
        f"**scheduled_for:** {scheduled_for}",
        f"**generated_at:** {generated_at}",
        f"**schedule_reason:** {schedule_reason}",
        "**review_required:** yes",
        "**Status:** DRAFT / REVIEW ONLY",
        "",
        (
            "> Dry run only. No email, publishing, brokerage execution, order construction, "
            "client contact, PDF export, approval bypass, or external LLM/API action was performed."
        ),
        "",
        "## Market Scan",
        "",
        _market_scan_section(scan),
        "",
        "## Wall Candidates",
        "",
        _wall_candidates_section(staged),
        "",
        "## Derivative Workbench Top Research",
        "",
        _derivative_top_research_section(derivatives),
        "",
        "## Exclusions / No-Recommendation Explanations",
        "",
        _exclusions_section(derivatives),
        "",
        "## Strategy Intent",
        "",
        _strategy_intent_section(derivatives),
        "",
        "## Risk Notes",
        "",
        (
            "- GreenRock Score, derivative research score, cross-window classification, and strategy intent "
            "are research triage aids only."
        ),
        "- Options research may be stale, illiquid, mispriced, or unavailable when provider data is incomplete.",
        "- Staged equity candidates can remain underfilled, lack analytics, or require additional fundamental review.",
        "- This dry run does not establish suitability, portfolio fit, or any transaction recommendation.",
        "",
        "## Human Review Required",
        "",
        (
            "A human reviewer must decide whether any separate approval-gated draft should be generated. "
            "This dry run is not client-facing and cannot be sent, published, traded, or exported as a final report."
        ),
    ]
    return "\n".join(lines) + "\n"


def load_report_schedule_config(output_dir: Path) -> ReportDryRunScheduleConfig:
    path = report_schedule_config_path(output_dir)
    if not path.exists():
        return ReportDryRunScheduleConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ReportDryRunScheduleConfig()
    holidays = tuple(str(item) for item in payload.get("market_holidays", ()) if str(item).strip())
    return ReportDryRunScheduleConfig(
        timezone=str(payload.get("timezone", DEFAULT_SCHEDULE_TIMEZONE)),
        month_end_hour=int(payload.get("month_end_hour", DEFAULT_MONTH_END_TIME.hour)),
        month_end_minute=int(payload.get("month_end_minute", DEFAULT_MONTH_END_TIME.minute)),
        sunday_refresh_enabled=bool(payload.get("sunday_refresh_enabled", True)),
        sunday_refresh_hour=int(payload.get("sunday_refresh_hour", DEFAULT_SUNDAY_REFRESH_TIME.hour)),
        sunday_refresh_minute=int(payload.get("sunday_refresh_minute", DEFAULT_SUNDAY_REFRESH_TIME.minute)),
        market_holidays=holidays,
    )


def default_report_schedule_config(output_dir: Path) -> Path:
    path = report_schedule_config_path(output_dir)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(ReportDryRunScheduleConfig()), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def preview_report_schedule(
    output_dir: Path,
    now: datetime | None = None,
    months: int = 3,
    config: ReportDryRunScheduleConfig | None = None,
) -> tuple[ReportDryRunOccurrence, ...]:
    config = config or load_report_schedule_config(output_dir)
    local_now = _local_now(now, config)
    occurrences: list[ReportDryRunOccurrence] = []
    year = local_now.year
    month = local_now.month
    for offset in range(max(1, months + 2)):
        target_month = _add_months(date(year, month, 1), offset)
        occurrences.extend(_month_occurrences(target_month.year, target_month.month, config))
        future = tuple(item for item in occurrences if _parse_dt(item.scheduled_for) >= local_now)
        if len(future) >= months:
            return future[:months]
    return tuple(item for item in occurrences if _parse_dt(item.scheduled_for) >= local_now)[:months]


def due_report_schedule(
    output_dir: Path,
    now: datetime | None = None,
    config: ReportDryRunScheduleConfig | None = None,
) -> tuple[ReportDryRunOccurrence, ...]:
    config = config or load_report_schedule_config(output_dir)
    local_now = _local_now(now, config)
    generated = _generated_occurrence_ids(output_dir)
    current = date(local_now.year, local_now.month, 1)
    due: list[ReportDryRunOccurrence] = []
    for occurrence in _month_occurrences(current.year, current.month, config):
        if _parse_dt(occurrence.scheduled_for) <= local_now and occurrence.occurrence_id not in generated:
            due.append(occurrence)
    return tuple(sorted(due, key=lambda item: item.scheduled_for))


def run_due_report_dry_runs(
    output_dir: Path,
    now: datetime | None = None,
    config: ReportDryRunScheduleConfig | None = None,
) -> tuple[tuple[ReportDryRunOccurrence, Path], ...]:
    created: list[tuple[ReportDryRunOccurrence, Path]] = []
    for occurrence in due_report_schedule(output_dir, now=now, config=config):
        path = create_report_dry_run(
            output_dir,
            scheduled_for=occurrence.scheduled_for,
            schedule_reason=occurrence.schedule_reason,
            occurrence_id=occurrence.occurrence_id,
        )
        created.append((occurrence, path))
    return tuple(created)


def _month_occurrences(year: int, month: int, config: ReportDryRunScheduleConfig) -> tuple[ReportDryRunOccurrence, ...]:
    last_day = _last_trading_day(year, month, config)
    trigger_day = _previous_trading_day(last_day, config)
    zone = ZoneInfo(config.timezone)
    scheduled = datetime.combine(trigger_day, time(config.month_end_hour, config.month_end_minute), tzinfo=zone)
    reason = "friday_evening_before_monday_last_trading_day" if last_day.weekday() == 0 else "month_end_before_last_trading_day"
    occurrences = [
        ReportDryRunOccurrence(
            f"greenrock-report-{year:04d}-{month:02d}-month-end",
            scheduled.isoformat(timespec="minutes"),
            reason,
        )
    ]
    if last_day.weekday() == 0 and config.sunday_refresh_enabled:
        sunday = last_day - timedelta(days=1)
        refresh = datetime.combine(sunday, time(config.sunday_refresh_hour, config.sunday_refresh_minute), tzinfo=zone)
        occurrences.append(
            ReportDryRunOccurrence(
                f"greenrock-report-{year:04d}-{month:02d}-sunday-refresh",
                refresh.isoformat(timespec="minutes"),
                "sunday_morning_refresh_before_monday_last_trading_day",
            )
        )
    return tuple(sorted(occurrences, key=lambda item: item.scheduled_for))


def _last_trading_day(year: int, month: int, config: ReportDryRunScheduleConfig) -> date:
    cursor = _add_months(date(year, month, 1), 1) - timedelta(days=1)
    while not _is_trading_day(cursor, config):
        cursor -= timedelta(days=1)
    return cursor


def _previous_trading_day(day: date, config: ReportDryRunScheduleConfig) -> date:
    cursor = day - timedelta(days=1)
    while not _is_trading_day(cursor, config):
        cursor -= timedelta(days=1)
    return cursor


def _is_trading_day(day: date, config: ReportDryRunScheduleConfig) -> bool:
    return day.weekday() < 5 and day.isoformat() not in set(config.market_holidays)


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _local_now(now: datetime | None, config: ReportDryRunScheduleConfig) -> datetime:
    zone = ZoneInfo(config.timezone)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    return current.astimezone(zone)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _generated_occurrence_ids(output_dir: Path) -> set[str]:
    path = report_schedule_ledger_path(output_dir)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(item.get("occurrence_id", "")) for item in payload.get("generated", ()) if item.get("occurrence_id")}


def _record_schedule_run(output_dir: Path, occurrence_id: str, scheduled_for: str, generated_at: str, schedule_reason: str, path: Path) -> None:
    ledger_path = report_schedule_ledger_path(output_dir)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"generated": []}
    except (OSError, json.JSONDecodeError):
        payload = {"generated": []}
    generated = [item for item in payload.get("generated", []) if item.get("occurrence_id") != occurrence_id]
    generated.append(
        {
            "occurrence_id": occurrence_id,
            "scheduled_for": scheduled_for,
            "generated_at": generated_at,
            "schedule_reason": schedule_reason,
            "review_required": True,
            "path": str(path),
        }
    )
    payload["generated"] = sorted(generated, key=lambda item: item.get("scheduled_for", ""))
    ledger_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _market_scan_section(scan) -> str:
    if scan is None:
        return "No latest market scan was found. Run a local scan before relying on report dry-run context."
    leaders = scan.rows[:8]
    lines = [
        f"- Latest scan: {scan.scan_id}",
        f"- Population: {scan.population}",
        f"- Data source: {scan.data_source}",
        f"- Scored rows: {len(scan.rows)}",
        f"- Provider failures: {scan.provider_failure_count}",
        "",
        "| Rank | Ticker | Score | Confidence | Priority | Guardrail |",
        "|---:|---|---:|---:|---|---|",
    ]
    for row in leaders:
        lines.append(
            "| "
            f"{_cell(row.get('rank', ''))} | "
            f"{_cell(row.get('symbol', ''))} | "
            f"{_cell(row.get('greenrock_score', ''))} | "
            f"{_cell(row.get('greenrock_confidence', ''))} | "
            f"{_cell(row.get('research_priority', ''))} | "
            f"{_cell(row.get('fundamental_guardrail', ''))} |"
        )
    if not leaders:
        lines.append("| - | No ranked scan rows available | - | - | - | - |")
    return "\n".join(lines)


def _wall_candidates_section(staged: tuple[dict[str, str], ...]) -> str:
    if not staged:
        return "No staged Wall/report candidates are available."
    lines = [
        "| Ticker | Bucket | Score | Evidence | Priority | Notes |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in staged[:12]:
        bucket = STAGING_BUCKET_LABELS.get(row.get("staged_bucket", ""), row.get("staged_bucket", ""))
        lines.append(
            "| "
            f"{_cell(row.get('ticker', ''))} | "
            f"{_cell(bucket)} | "
            f"{_cell(row.get('greenrock_score', ''))} | "
            f"{_cell(row.get('evidence_agreement', ''))} | "
            f"{_cell(row.get('research_priority', ''))} | "
            f"{_cell(row.get('notes', ''))} |"
        )
    return "\n".join(lines)


def _derivative_top_research_section(analysis: dict | None) -> str:
    if not analysis:
        return "No Derivative Workbench snapshot is available. Equity report dry run continues without options context."
    rows = _top_derivative_rows(analysis)
    if not rows:
        return "Derivative Workbench snapshot found, but no OTM Top Research contracts are available."
    lines = [
        f"- Snapshot: {analysis.get('snapshot_id', '')}",
        f"- Underlying: {analysis.get('ticker', '')} at {analysis.get('underlying_price', '')}",
        "- Top Research remains OTM-only; raw chain CSVs retain full contract data.",
        "",
        "| Window | Type | Expiration | Strike | Score | Rationale |",
        "|---|---|---|---:|---:|---|",
    ]
    for window, item in rows[:10]:
        contract = item.get("contract", {})
        lines.append(
            "| "
            f"{_cell(str(window) + 'D')} | "
            f"{_cell(contract.get('option_type', ''))} | "
            f"{_cell(contract.get('expiration', ''))} | "
            f"{_cell(str(contract.get('strike', '')))} | "
            f"{_cell(str(item.get('score', '')))} | "
            f"{_cell(item.get('ranking_rationale', ''))} |"
        )
    return "\n".join(lines)


def _exclusions_section(analysis: dict | None) -> str:
    explanations = [
        "This report dry run is not a recommendation and does not ask the operator to buy, sell, hold, hedge, or trade.",
        "Derivative exclusions explain why contracts did not enter OTM Top Research; they are not trading instructions.",
    ]
    if not analysis:
        return "\n".join(f"- {item}" for item in explanations)
    excluded = []
    for group_name in ("excluded_calls", "excluded_puts"):
        for window, rows in analysis.get(group_name, {}).items():
            for item in rows[:4]:
                contract = item.get("contract", {})
                excluded.append(
                    f"{window}D {contract.get('option_type', '')} {contract.get('strike', '')}: "
                    f"{'; '.join(item.get('reasons', ())) or 'excluded'}"
                )
    if excluded:
        explanations.extend(excluded[:12])
    else:
        explanations.append("No contracts were reported as excluded by the latest Derivative Workbench snapshot.")
    return "\n".join(f"- {item}" for item in explanations)


def _strategy_intent_section(analysis: dict | None) -> str:
    if not analysis:
        return "No strategy intent labels are available without a Derivative Workbench snapshot."
    rows = _top_derivative_rows(analysis)
    if not rows:
        return "No Top Research contracts are available for strategy intent mapping."
    lines = [
        "| Window | Contract | Intent | Manifesto | Position Context | Rationale |",
        "|---|---|---|---|---|---|",
    ]
    for window, item in rows[:10]:
        contract = item.get("contract", {})
        contract_label = f"{contract.get('option_type', '')} {contract.get('strike', '')}"
        lines.append(
            "| "
            f"{_cell(str(window) + 'D')} | "
            f"{_cell(contract_label)} | "
            f"{_cell(item.get('strategy_intent', 'research_only'))} | "
            f"{_cell(item.get('manifesto_alignment', ''))} | "
            f"{_cell(item.get('position_context_alignment', ''))} | "
            f"{_cell(item.get('intent_rationale', ''))} |"
        )
    return "\n".join(lines)


def _top_derivative_rows(analysis: dict) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for group_name in ("top_calls", "top_puts"):
        for window, items in analysis.get(group_name, {}).items():
            for item in items:
                rows.append((str(window), item))
    rows.sort(
        key=lambda row: (
            _window_sort_key(row[0]),
            row[1].get("contract", {}).get("option_type", ""),
            -_float(row[1].get("score", 0)),
        )
    )
    return rows


def _window_sort_key(window: str) -> int:
    try:
        return int(window)
    except ValueError:
        return 9999


def _cell(value) -> str:
    return str(value or "-").replace("|", "/").replace("\n", " ").strip()


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
