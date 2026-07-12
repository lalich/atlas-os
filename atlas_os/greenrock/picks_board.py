"""Approved-report source of truth for the GreenRock Picks Board."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from atlas_os.core.approvals import ApprovalStatus, get_approval
from atlas_os.core.artifacts import Artifact, list_artifacts_for_run
from atlas_os.core.audit_log import create_audit_log
from atlas_os.core.reports import ReportRecord, list_reports


BOARD_VERSION = 1
BOARD_DIR = "picks_board"
BOARD_FILE = "approved_board.json"
UNAVAILABLE = "Unavailable in approved report"

PICK_FIELDS = (
    "symbol",
    "company_name",
    "market_cap_bucket",
    "market_cap",
    "score",
    "latest_close",
    "rsi_14",
    "low_proximity",
    "volume_avg_10",
    "previous_volume_avg_10",
    "ema_8",
    "sma_10",
    "sma_50",
    "sma_150",
    "ma_roc_50",
    "ma_roc_150",
    "bollinger_lower",
    "bollinger_upper",
    "passed_rules",
    "failed_rules",
    "has_price_history",
    "has_market_cap",
    "has_volume_data",
    "has_52_week_low",
    "skipped_reason",
    "selection_label",
    "guardrail",
    "quick_ratio",
    "net_cash_debt",
    "share_change_percent",
    "evidence_agreement",
    "top_bullish_signal",
    "top_caution_signal",
    "note",
    "confidence",
    "research_priority",
    "source_list",
    "source_scan_id",
    "staged_bucket",
)

NUMERIC_FIELDS = {
    "market_cap",
    "score",
    "latest_close",
    "rsi_14",
    "low_proximity",
    "volume_avg_10",
    "previous_volume_avg_10",
    "ema_8",
    "sma_10",
    "sma_50",
    "sma_150",
    "ma_roc_50",
    "ma_roc_150",
    "bollinger_lower",
    "bollinger_upper",
    "quick_ratio",
    "evidence_agreement",
    "confidence",
}

ALIASES = {
    "symbol": ("symbol", "ticker"),
    "company_name": ("company_name", "company", "name"),
    "market_cap_bucket": ("market_cap_bucket", "section", "bucket", "staged_bucket"),
    "market_cap": ("market_cap", "market_capitalization"),
    "score": ("score", "greenrock_score"),
    "latest_close": ("latest_close", "price", "approved_report_price"),
    "guardrail": ("guardrail", "fundamental_guardrail", "risk_state"),
    "confidence": ("confidence", "greenrock_confidence"),
    "top_bullish_signal": ("top_bullish_signal", "bullish_signal"),
    "top_caution_signal": ("top_caution_signal", "caution_signal", "bearish_signal"),
}


@dataclass(frozen=True)
class PicksBoardArtifactDiagnostic:
    artifact_type: str
    path: str
    exists: bool
    headers: tuple[str, ...]
    representative_row: dict[str, str]
    row_count: int
    issue: str = ""


@dataclass(frozen=True)
class PicksBoardFieldDiagnostic:
    field: str
    source_columns: tuple[str, ...]
    populated_count: int
    unavailable_count: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class PicksBoardHydrationDiagnostics:
    report_id: int | None
    approval_id: int | None
    run_id: str | None
    report_path: str
    artifact_diagnostics: tuple[PicksBoardArtifactDiagnostic, ...]
    matched_ticker_count: int
    unmatched_ticker_count: int
    populated_field_count: int
    unavailable_field_count: int
    field_diagnostics: tuple[PicksBoardFieldDiagnostic, ...]
    warning: str = ""


@dataclass(frozen=True)
class ApprovedPicksBoard:
    status: str
    source_report: ReportRecord | None
    source_approval_id: int | None
    source_run_id: str | None
    data_mode: str
    report_path: str
    mega_pick: dict[str, str] | None
    large_candidates: tuple[dict[str, str], ...]
    small_candidates: tuple[dict[str, str], ...]
    warnings: tuple[str, ...] = ()
    from_snapshot: bool = False

    @property
    def slot_count(self) -> int:
        return (1 if self.mega_pick else 0) + len(self.large_candidates) + len(self.small_candidates)

    @property
    def has_picks(self) -> bool:
        return bool(self.mega_pick or self.large_candidates or self.small_candidates)


def approved_picks_board_path(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / BOARD_DIR / BOARD_FILE


def approved_picks_board(connection: Connection, output_dir: Path) -> ApprovedPicksBoard:
    approved_reports = _approved_greenrock_reports(connection)
    if not approved_reports:
        snapshot = _load_snapshot(output_dir)
        if snapshot:
            return snapshot
        return ApprovedPicksBoard(
            status="empty",
            source_report=None,
            source_approval_id=None,
            source_run_id=None,
            data_mode="NONE",
            report_path="",
            mega_pick=None,
            large_candidates=(),
            small_candidates=(),
            warnings=("No approved GreenRock report exists yet. The Picks Board updates only after Managing Director approval.",),
        )

    newest = approved_reports[0]
    try:
        board = _board_from_report(connection, newest)
    except (OSError, csv.Error, ValueError) as error:
        snapshot = _load_snapshot(output_dir)
        if snapshot:
            return _with_warning(
                snapshot,
                f"Latest approved report {newest.run_id or newest.id} could not hydrate the Picks Board: {error}. Preserving last valid approved board.",
            )
        for fallback in approved_reports[1:]:
            try:
                board = _board_from_report(connection, fallback)
            except (OSError, csv.Error, ValueError):
                continue
            board = _with_warning(
                board,
                f"Latest approved report {newest.run_id or newest.id} could not hydrate the Picks Board: {error}. Showing prior valid approved board.",
            )
            _save_snapshot(output_dir, board)
            return board
        return ApprovedPicksBoard(
            status="blocked",
            source_report=newest,
            source_approval_id=newest.approval_id,
            source_run_id=newest.run_id,
            data_mode="UNKNOWN",
            report_path=newest.content_path or "",
            mega_pick=None,
            large_candidates=(),
            small_candidates=(),
            warnings=(f"Latest approved report could not hydrate the Picks Board: {error}.",),
        )

    _save_snapshot(output_dir, board)
    return board


def approved_picks_board_diagnostics(connection: Connection) -> PicksBoardHydrationDiagnostics:
    approved_reports = _approved_greenrock_reports(connection)
    if not approved_reports:
        return PicksBoardHydrationDiagnostics(
            report_id=None,
            approval_id=None,
            run_id=None,
            report_path="",
            artifact_diagnostics=(),
            matched_ticker_count=0,
            unmatched_ticker_count=0,
            populated_field_count=0,
            unavailable_field_count=0,
            field_diagnostics=(),
            warning="No approved GreenRock report exists.",
        )
    report = approved_reports[0]
    if not report.run_id:
        return PicksBoardHydrationDiagnostics(
            report_id=report.id,
            approval_id=report.approval_id,
            run_id=None,
            report_path=report.content_path or "",
            artifact_diagnostics=(),
            matched_ticker_count=0,
            unmatched_ticker_count=0,
            populated_field_count=0,
            unavailable_field_count=0,
            field_diagnostics=(),
            warning="Approved report has no workflow run.",
        )
    artifacts = list_artifacts_for_run(connection, report.run_id)
    paths = {artifact.artifact_type: artifact.path for artifact in artifacts}
    artifact_diagnostics = tuple(_artifact_diagnostic(artifact) for artifact in artifacts)
    candidate_rows = _read_rows(paths.get("candidates_csv"), limit=None)
    all_by_symbol = {_symbol(row): row for row in candidate_rows if _symbol(row)}
    section_rows = (
        _read_rows(paths.get("mega_rock_csv"), limit=1)
        + _read_rows(paths.get("large_cap_csv"), limit=11)
        + _read_rows(paths.get("small_cap_csv"), limit=11)
    )
    matched = sum(1 for row in section_rows if _symbol(row) in all_by_symbol)
    unmatched = sum(1 for row in section_rows if _symbol(row) and _symbol(row) not in all_by_symbol)
    hydrated = [_merge_row(row, all_by_symbol.get(_symbol(row), {})) for row in section_rows]
    normalized = [_normalize_pick_row(row) for row in hydrated]
    field_diagnostics = tuple(_field_diagnostic(field, hydrated, normalized, bool(candidate_rows)) for field in PICK_FIELDS)
    populated = sum(item.populated_count for item in field_diagnostics)
    unavailable = sum(item.unavailable_count for item in field_diagnostics)
    return PicksBoardHydrationDiagnostics(
        report_id=report.id,
        approval_id=report.approval_id,
        run_id=report.run_id,
        report_path=report.content_path or "",
        artifact_diagnostics=artifact_diagnostics,
        matched_ticker_count=matched,
        unmatched_ticker_count=unmatched,
        populated_field_count=populated,
        unavailable_field_count=unavailable,
        field_diagnostics=field_diagnostics,
    )


def record_picks_board_update(connection: Connection, board: ApprovedPicksBoard) -> None:
    if not board.source_run_id or not board.source_approval_id or not board.has_picks:
        return
    create_audit_log(
        connection,
        actor="greenrock_picks_board",
        action="picks_board_hydrated",
        detail=f"run_id={board.source_run_id}; slots={board.slot_count}/23",
        run_id=board.source_run_id,
        approval_id=board.source_approval_id,
    )


def _approved_greenrock_reports(connection: Connection) -> tuple[ReportRecord, ...]:
    reports = []
    for report in list_reports(connection):
        if report.report_type != "greenrock_monthly_draft" or report.status != ApprovalStatus.APPROVED.value:
            continue
        if report.approval_id is None:
            continue
        try:
            approval = get_approval(connection, report.approval_id)
        except KeyError:
            continue
        if approval.status == ApprovalStatus.APPROVED:
            reports.append(report)
    return tuple(sorted(reports, key=_approved_report_sort_key, reverse=True))


def _approved_report_sort_key(report: ReportRecord) -> tuple[str, str, int]:
    return (report.approved_at or "", report.created_at or "", report.id)


def _board_from_report(connection: Connection, report: ReportRecord) -> ApprovedPicksBoard:
    if not report.run_id:
        raise ValueError("approved report has no workflow run")
    artifacts = list_artifacts_for_run(connection, report.run_id)
    paths = {artifact.artifact_type: artifact.path for artifact in artifacts}
    all_rows = _read_rows(paths.get("candidates_csv"), limit=None)
    all_by_symbol = {_symbol(row): row for row in all_rows if _symbol(row)}
    mega_rows = _hydrate_rows(_read_rows(paths.get("mega_rock_csv"), limit=1), all_by_symbol, limit=1)
    large_rows = _hydrate_rows(_read_rows(paths.get("large_cap_csv"), limit=11), all_by_symbol, limit=11)
    small_rows = _hydrate_rows(_read_rows(paths.get("small_cap_csv"), limit=11), all_by_symbol, limit=11)
    mega_pick = mega_rows[0] if mega_rows else (_normalize_pick_row(all_rows[0]) if all_rows else None)
    if not mega_pick and not large_rows and not small_rows:
        raise ValueError("approved report has no parseable candidate rows")
    warnings = _section_warnings(mega_pick, large_rows, small_rows)
    return ApprovedPicksBoard(
        status="ready",
        source_report=report,
        source_approval_id=report.approval_id,
        source_run_id=report.run_id,
        data_mode=_run_data_mode(connection, report.run_id),
        report_path=report.content_path or "",
        mega_pick=mega_pick,
        large_candidates=tuple(large_rows),
        small_candidates=tuple(small_rows),
        warnings=tuple(warnings),
    )


def _run_data_mode(connection: Connection, run_id: str) -> str:
    row = connection.execute("SELECT data_mode FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
    return str(row["data_mode"]).upper() if row else "UNKNOWN"


def _read_rows(path: str | None, limit: int | None) -> list[dict[str, str]]:
    if not path:
        return []
    candidate_path = Path(path)
    if not candidate_path.exists():
        return []
    with candidate_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    if rows and not _symbol(rows[0]):
        raise ValueError(f"{candidate_path.name} is missing symbol column")
    rows.sort(key=_row_score, reverse=True)
    return rows[:limit] if limit is not None else rows


def _artifact_diagnostic(artifact: Artifact) -> PicksBoardArtifactDiagnostic:
    path = Path(artifact.path)
    if not path.exists():
        return PicksBoardArtifactDiagnostic(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            exists=False,
            headers=(),
            representative_row={},
            row_count=0,
            issue="artifact_not_found",
        )
    if path.suffix.lower() != ".csv":
        return PicksBoardArtifactDiagnostic(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            exists=True,
            headers=(),
            representative_row={},
            row_count=0,
        )
    try:
        with path.open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)
    except (OSError, csv.Error):
        return PicksBoardArtifactDiagnostic(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            exists=True,
            headers=(),
            representative_row={},
            row_count=0,
            issue="unsupported_schema",
        )
    headers = tuple(reader.fieldnames or ())
    issue = "" if any(key in headers for key in ALIASES["symbol"]) else "unsupported_schema"
    return PicksBoardArtifactDiagnostic(
        artifact_type=artifact.artifact_type,
        path=artifact.path,
        exists=True,
        headers=headers,
        representative_row=dict(rows[0]) if rows else {},
        row_count=len(rows),
        issue=issue,
    )


def _field_diagnostic(
    field: str,
    merged_rows: list[dict[str, str]],
    normalized_rows: list[dict[str, str]],
    candidate_artifact_found: bool,
) -> PicksBoardFieldDiagnostic:
    aliases = tuple(ALIASES.get(field, (field,)))
    reasons = {
        "field_genuinely_absent": 0,
        "artifact_not_found": 0,
        "malformed_value": 0,
        "unsupported_schema": 0,
    }
    populated = 0
    unavailable = 0
    for raw, normalized in zip(merged_rows, normalized_rows):
        normalized_value = normalized.get(field, "")
        if _present(normalized_value):
            populated += 1
            continue
        unavailable += 1
        raw_value = _value(raw, field)
        if not candidate_artifact_found:
            reasons["artifact_not_found"] += 1
        elif field in NUMERIC_FIELDS and _present(raw_value):
            reasons["malformed_value"] += 1
        elif not any(key in raw for key in aliases):
            reasons["unsupported_schema"] += 1
        else:
            reasons["field_genuinely_absent"] += 1
    return PicksBoardFieldDiagnostic(
        field=field,
        source_columns=aliases,
        populated_count=populated,
        unavailable_count=unavailable,
        reasons={key: count for key, count in reasons.items() if count},
    )


def _hydrate_rows(
    rows: list[dict[str, str]],
    all_by_symbol: dict[str, dict[str, str]],
    limit: int,
) -> list[dict[str, str]]:
    hydrated = []
    for row in rows:
        fallback = all_by_symbol.get(_symbol(row), {})
        hydrated.append(_normalize_pick_row(_merge_row(row, fallback)))
    hydrated.sort(key=_row_score, reverse=True)
    return hydrated[:limit]


def _merge_row(primary: dict[str, str], fallback: dict[str, str]) -> dict[str, str]:
    merged = dict(fallback)
    for key, value in primary.items():
        if _present(value):
            merged[key] = value
    return merged


def _row_score(row: dict[str, str]) -> float:
    try:
        return float(_value(row, "score") or "0")
    except ValueError:
        return 0.0


def _normalize_pick_row(row: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field in PICK_FIELDS:
        value = _value(row, field)
        if field == "symbol":
            normalized[field] = value.upper()
        elif field in NUMERIC_FIELDS:
            normalized[field] = _numeric_or_unavailable(value)
        elif field == "company_name" and not _present(value):
            normalized[field] = normalized.get("symbol", "")
        elif field in {"selection_label", "guardrail", "top_bullish_signal", "top_caution_signal"}:
            normalized[field] = value if _present(value) else UNAVAILABLE
        else:
            normalized[field] = value if _present(value) else UNAVAILABLE
    normalized["data_origin"] = "approved_report_run"
    return normalized


def _value(row: dict[str, str], field: str) -> str:
    for key in ALIASES.get(field, (field,)):
        value = str(row.get(key, "")).strip()
        if _present(value):
            return value
    return ""


def _symbol(row: dict[str, str]) -> str:
    return _value(row, "symbol").upper()


def _present(value: object) -> bool:
    return str(value or "").strip() not in {"", "-", "none", "None", "null", "NULL", UNAVAILABLE}


def _numeric_or_unavailable(value: str) -> str:
    if not _present(value):
        return UNAVAILABLE
    try:
        float(value)
    except ValueError:
        return UNAVAILABLE
    return value


def _section_warnings(
    mega_pick: dict[str, str] | None,
    large_rows: list[dict[str, str]],
    small_rows: list[dict[str, str]],
) -> list[str]:
    warnings = []
    if mega_pick is None:
        warnings.append("Mega Rock section has 0/1 picks.")
    if len(large_rows) < 11:
        warnings.append(f"Large-cap section has {len(large_rows)}/11 picks.")
    if len(small_rows) < 11:
        warnings.append(f"Small/mid-cap section has {len(small_rows)}/11 picks.")
    return warnings


def _save_snapshot(output_dir: Path, board: ApprovedPicksBoard) -> None:
    path = approved_picks_board_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": BOARD_VERSION,
        "status": board.status,
        "source_approval_id": board.source_approval_id,
        "source_run_id": board.source_run_id,
        "data_mode": board.data_mode,
        "report_path": board.report_path,
        "mega_pick": board.mega_pick,
        "large_candidates": list(board.large_candidates),
        "small_candidates": list(board.small_candidates),
        "warnings": list(board.warnings),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_snapshot(output_dir: Path) -> ApprovedPicksBoard | None:
    path = approved_picks_board_path(output_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != BOARD_VERSION or not payload.get("source_run_id"):
        return None
    return ApprovedPicksBoard(
        status=payload.get("status", "ready"),
        source_report=None,
        source_approval_id=payload.get("source_approval_id"),
        source_run_id=payload.get("source_run_id"),
        data_mode=payload.get("data_mode", "UNKNOWN"),
        report_path=payload.get("report_path", ""),
        mega_pick=payload.get("mega_pick"),
        large_candidates=tuple(payload.get("large_candidates", ())),
        small_candidates=tuple(payload.get("small_candidates", ())),
        warnings=tuple(payload.get("warnings", ())),
        from_snapshot=True,
    )


def _with_warning(board: ApprovedPicksBoard, warning: str) -> ApprovedPicksBoard:
    return ApprovedPicksBoard(
        status=board.status,
        source_report=board.source_report,
        source_approval_id=board.source_approval_id,
        source_run_id=board.source_run_id,
        data_mode=board.data_mode,
        report_path=board.report_path,
        mega_pick=board.mega_pick,
        large_candidates=board.large_candidates,
        small_candidates=board.small_candidates,
        warnings=(warning,) + tuple(item for item in board.warnings if item != warning),
        from_snapshot=board.from_snapshot,
    )
