"""Local Atlas Inbox storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


INBOX_ROOT = Path("atlas") / "inbox"
INBOX_FILE = "items.json"
INBOX_STATUSES = {"open", "dismissed", "completed"}
INBOX_SEVERITIES = {"info", "warning", "critical", "action"}


@dataclass(frozen=True)
class InboxItem:
    item_id: str
    created_at: str
    source_agent: str
    severity: str
    title: str
    detail: str
    target_url: str
    status: str = "open"
    related_agent_run_id: str | None = None
    related_scan_id: str | None = None
    related_report_run_id: str | None = None
    related_approval_id: int | None = None
    created_reason: str = ""


def inbox_path(output_dir: Path) -> Path:
    return Path(output_dir) / INBOX_ROOT / INBOX_FILE


def list_inbox_items(output_dir: Path, include_closed: bool = False) -> tuple[InboxItem, ...]:
    path = inbox_path(output_dir)
    if not path.exists():
        return ()
    try:
        raw_items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    items = tuple(_item_from_dict(item) for item in raw_items if isinstance(item, dict))
    if include_closed:
        return items
    return tuple(item for item in items if item.status == "open")


def create_inbox_item(
    output_dir: Path,
    source_agent: str,
    severity: str,
    title: str,
    detail: str,
    target_url: str,
    related_agent_run_id: str | None = None,
    related_scan_id: str | None = None,
    related_report_run_id: str | None = None,
    related_approval_id: int | None = None,
    created_reason: str = "",
) -> InboxItem:
    normalized_severity = severity if severity in INBOX_SEVERITIES else "info"
    now = _now()
    item = InboxItem(
        item_id="inbox-" + now.replace("+00:00", "Z").replace(":", "").replace("-", "").replace(".", "") + "-" + uuid4().hex[:8],
        created_at=now,
        source_agent=source_agent,
        severity=normalized_severity,
        title=title,
        detail=detail,
        target_url=target_url,
        related_agent_run_id=related_agent_run_id,
        related_scan_id=related_scan_id,
        related_report_run_id=related_report_run_id,
        related_approval_id=related_approval_id,
        created_reason=created_reason,
    )
    existing = list(list_inbox_items(output_dir, include_closed=True))
    if _duplicate_open_item(existing, item):
        for index, current in enumerate(existing):
            if current.status == "open" and current.source_agent == item.source_agent and current.title == item.title:
                refreshed = InboxItem(**{**item.__dict__, "item_id": current.item_id, "created_at": current.created_at})
                existing[index] = refreshed
                _write_items(output_dir, tuple(existing))
                return refreshed
    existing.insert(0, item)
    _write_items(output_dir, tuple(existing))
    return item


def dismiss_inbox_item(output_dir: Path, item_id: str) -> InboxItem:
    return _update_inbox_status(output_dir, item_id, "dismissed")


def complete_inbox_item(output_dir: Path, item_id: str) -> InboxItem:
    return _update_inbox_status(output_dir, item_id, "completed")


def get_inbox_item(output_dir: Path, item_id: str) -> InboxItem:
    for item in list_inbox_items(output_dir, include_closed=True):
        if item.item_id == item_id:
            return item
    raise KeyError(f"Unknown inbox item: {item_id}")


def _update_inbox_status(output_dir: Path, item_id: str, status: str) -> InboxItem:
    items = list(list_inbox_items(output_dir, include_closed=True))
    for index, item in enumerate(items):
        if item.item_id == item_id:
            updated = InboxItem(**{**item.__dict__, "status": status})
            items[index] = updated
            _write_items(output_dir, tuple(items))
            return updated
    raise KeyError(f"Unknown inbox item: {item_id}")


def _write_items(output_dir: Path, items: tuple[InboxItem, ...]) -> None:
    path = inbox_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([item.__dict__ for item in items], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _item_from_dict(item: dict) -> InboxItem:
    return InboxItem(
        item_id=str(item.get("item_id", "")),
        created_at=str(item.get("created_at", "")),
        source_agent=str(item.get("source_agent", "")),
        severity=str(item.get("severity", "info")),
        title=str(item.get("title", "")),
        detail=str(item.get("detail", "")),
        target_url=str(item.get("target_url", "")),
        status=str(item.get("status", "open")) if str(item.get("status", "open")) in INBOX_STATUSES else "open",
        related_agent_run_id=item.get("related_agent_run_id"),
        related_scan_id=item.get("related_scan_id"),
        related_report_run_id=item.get("related_report_run_id"),
        related_approval_id=item.get("related_approval_id"),
        created_reason=str(item.get("created_reason", "")),
    )


def _duplicate_open_item(items: list[InboxItem], item: InboxItem) -> bool:
    return any(
        current.status == "open" and current.source_agent == item.source_agent and current.title == item.title
        for current in items
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
