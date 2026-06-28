"""Local GreenRock report candidate staging."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.greenrock.scanner import latest_scan, load_promotion_metadata
from atlas_os.greenrock.universe import LARGE_CAP_UNIVERSE, MEGA_ROCK_UNIVERSE, SMALL_MID_CAP_UNIVERSE


MEGA_BUCKET = "mega"
LARGE_BUCKET = "large"
SMALL_MID_BUCKET = "small_mid"
RESEARCH_BUCKET = "research"
EXCLUDED_BUCKET = "excluded"

STAGING_BUCKET_LABELS = {
    MEGA_BUCKET: "Mega Rock Candidate",
    LARGE_BUCKET: "Large Cap Candidate",
    SMALL_MID_BUCKET: "Small/Mid Candidate",
    RESEARCH_BUCKET: "Research Only",
    EXCLUDED_BUCKET: "Excluded",
}

STAGING_BUCKET_TARGETS = {
    MEGA_BUCKET: 1,
    LARGE_BUCKET: 11,
    SMALL_MID_BUCKET: 11,
}

STAGING_HEADERS = [
    "ticker",
    "staged_bucket",
    "source_list",
    "source_scan_id",
    "greenrock_score",
    "confidence",
    "evidence_agreement",
    "guardrail",
    "research_priority",
    "top_bullish_signal",
    "top_caution_signal",
    "staged_at",
    "notes",
]


@dataclass(frozen=True)
class StagingReadiness:
    bucket: str
    label: str
    count: int
    target: int | None
    status: str


def staging_path(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / "staging" / "report_candidates.csv"


def load_staged_candidates(output_dir: Path) -> tuple[dict[str, str], ...]:
    path = staging_path(output_dir)
    if not path.exists():
        return ()
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        return tuple(_normalize_row(dict(row)) for row in reader)


def save_staged_candidates(output_dir: Path, rows: tuple[dict[str, str], ...]) -> None:
    path = staging_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_rows = tuple(_normalize_row(row) for row in rows if row.get("ticker", "").strip())
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=STAGING_HEADERS)
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow(row)


def add_staged_candidate(
    output_dir: Path,
    ticker: str,
    bucket: str,
    source_list: str = "manual",
    notes: str = "",
) -> dict[str, str]:
    normalized_ticker = _normalize_ticker(ticker)
    normalized_bucket = _normalize_bucket(bucket)
    if not normalized_ticker:
        raise ValueError("Ticker is required.")
    existing = list(load_staged_candidates(output_dir))
    metadata = _candidate_metadata(output_dir, normalized_ticker, source_list)
    _validate_staging_bucket(normalized_ticker, normalized_bucket, metadata)
    row = _normalize_row(
        {
            "ticker": normalized_ticker,
            "staged_bucket": normalized_bucket,
            "source_list": source_list or metadata.get("source_list", "manual"),
            "source_scan_id": metadata.get("source_scan_id", ""),
            "greenrock_score": metadata.get("greenrock_score", ""),
            "confidence": metadata.get("confidence", ""),
            "evidence_agreement": metadata.get("evidence_agreement", ""),
            "guardrail": metadata.get("guardrail", ""),
            "research_priority": metadata.get("research_priority", ""),
            "top_bullish_signal": metadata.get("top_bullish_signal", ""),
            "top_caution_signal": metadata.get("top_caution_signal", ""),
            "staged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "notes": notes,
        }
    )
    replaced = False
    for index, item in enumerate(existing):
        if item["ticker"] == normalized_ticker:
            row["notes"] = notes if notes else item.get("notes", "")
            existing[index] = row
            replaced = True
            break
    if not replaced:
        existing.append(row)
    save_staged_candidates(output_dir, tuple(existing))
    return row


def move_staged_candidate(output_dir: Path, ticker: str, bucket: str, notes: str | None = None) -> dict[str, str]:
    normalized_ticker = _normalize_ticker(ticker)
    normalized_bucket = _normalize_bucket(bucket)
    rows = list(load_staged_candidates(output_dir))
    for index, row in enumerate(rows):
        if row["ticker"] == normalized_ticker:
            updated = dict(row)
            updated["staged_bucket"] = normalized_bucket
            updated["staged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if notes is not None:
                updated["notes"] = notes
            rows[index] = _normalize_row(updated)
            save_staged_candidates(output_dir, tuple(rows))
            return rows[index]
    return add_staged_candidate(output_dir, normalized_ticker, normalized_bucket, notes=notes or "")


def remove_staged_candidate(output_dir: Path, ticker: str) -> bool:
    normalized_ticker = _normalize_ticker(ticker)
    rows = load_staged_candidates(output_dir)
    kept = tuple(row for row in rows if row["ticker"] != normalized_ticker)
    save_staged_candidates(output_dir, kept)
    return len(kept) != len(rows)


def update_staged_notes(output_dir: Path, ticker: str, notes: str) -> dict[str, str]:
    normalized_ticker = _normalize_ticker(ticker)
    rows = list(load_staged_candidates(output_dir))
    for index, row in enumerate(rows):
        if row["ticker"] == normalized_ticker:
            updated = dict(row)
            updated["notes"] = notes
            rows[index] = _normalize_row(updated)
            save_staged_candidates(output_dir, tuple(rows))
            return rows[index]
    raise ValueError(f"{normalized_ticker} is not staged.")


def staging_readiness(output_dir: Path) -> tuple[StagingReadiness, ...]:
    rows = load_staged_candidates(output_dir)
    statuses: list[StagingReadiness] = []
    for bucket, label in STAGING_BUCKET_LABELS.items():
        count = sum(1 for row in rows if row.get("staged_bucket") == bucket)
        target = STAGING_BUCKET_TARGETS.get(bucket)
        if target is None:
            status = "Needs Review" if count else "Ready"
        elif count == target:
            status = "Ready"
        elif count < target:
            status = "Underfilled"
        else:
            status = "Overfilled"
        statuses.append(StagingReadiness(bucket=bucket, label=label, count=count, target=target, status=status))
    return tuple(statuses)


def _candidate_metadata(output_dir: Path, ticker: str, source_list: str) -> dict[str, str]:
    metadata = _metadata_from_latest_scan(output_dir, ticker)
    if metadata:
        return metadata | {"source_list": source_list or "latest_scan"}
    for row in reversed(load_promotion_metadata(output_dir)):
        if row.get("ticker", "").upper() == ticker:
            return {
                "source_list": source_list or row.get("destination_list", ""),
                "source_scan_id": row.get("scan_id", ""),
                "greenrock_score": row.get("score", ""),
                "confidence": row.get("confidence", ""),
                "evidence_agreement": row.get("evidence_agreement", ""),
                "guardrail": row.get("guardrail", ""),
                "research_priority": row.get("research_priority", ""),
                "top_bullish_signal": "",
                "top_caution_signal": "",
            }
    return {"source_list": source_list or "manual"}


def _metadata_from_latest_scan(output_dir: Path, ticker: str) -> dict[str, str]:
    scan = latest_scan(output_dir)
    if not scan:
        return {}
    row = next((item for item in scan.rows if item.get("symbol", "").upper() == ticker), None)
    if row is None:
        return {}
    return {
        "source_scan_id": scan.scan_id,
        "greenrock_score": row.get("greenrock_score", ""),
        "confidence": row.get("greenrock_confidence", ""),
        "evidence_agreement": row.get("evidence_agreement", ""),
        "guardrail": row.get("fundamental_guardrail", ""),
        "market_cap_bucket": row.get("market_cap_bucket", ""),
        "research_priority": row.get("research_priority", ""),
        "top_bullish_signal": row.get("top_bullish_signal", ""),
        "top_caution_signal": row.get("top_caution_signal", ""),
    }


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    normalized = {header: row.get(header, "") for header in STAGING_HEADERS}
    normalized["ticker"] = _normalize_ticker(normalized["ticker"])
    normalized["staged_bucket"] = _normalize_bucket(normalized["staged_bucket"] or RESEARCH_BUCKET)
    return normalized


def _validate_staging_bucket(ticker: str, bucket: str, metadata: dict[str, str]) -> None:
    if bucket in {RESEARCH_BUCKET, EXCLUDED_BUCKET}:
        return
    suggested = _suggested_staging_bucket(metadata.get("market_cap_bucket", ""))
    if not suggested or suggested == bucket:
        return
    raise ValueError(
        f"This ticker does not currently meet the requirements for {STAGING_BUCKET_LABELS[bucket]}. "
        f"Consider adding it to {STAGING_BUCKET_LABELS[suggested]} or Research Only instead."
    )


def _suggested_staging_bucket(market_cap_bucket: str) -> str:
    return {
        MEGA_ROCK_UNIVERSE: MEGA_BUCKET,
        LARGE_CAP_UNIVERSE: LARGE_BUCKET,
        "small_cap": SMALL_MID_BUCKET,
        SMALL_MID_CAP_UNIVERSE: SMALL_MID_BUCKET,
    }.get(market_cap_bucket.strip().lower(), "")


def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _normalize_bucket(bucket: str) -> str:
    aliases = {
        "mega": MEGA_BUCKET,
        "mega_rock": MEGA_BUCKET,
        "large": LARGE_BUCKET,
        "large_cap": LARGE_BUCKET,
        "small": SMALL_MID_BUCKET,
        "small_mid": SMALL_MID_BUCKET,
        "small_mid_cap": SMALL_MID_BUCKET,
        "research": RESEARCH_BUCKET,
        "research_only": RESEARCH_BUCKET,
        "excluded": EXCLUDED_BUCKET,
        "exclude": EXCLUDED_BUCKET,
    }
    normalized = bucket.strip().lower()
    if normalized not in aliases:
        raise ValueError("Choose a valid staging bucket.")
    return aliases[normalized]
