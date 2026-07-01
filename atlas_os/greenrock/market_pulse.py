"""Market Pulse staging helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas_os.greenrock.market_engine import (
    ARCHETYPE_LARGE,
    ARCHETYPE_MEGA,
    ARCHETYPE_MICRO,
    ARCHETYPE_MID,
    ARCHETYPE_SMALL,
    MARKET_ARCHETYPES,
    classify_market_archetype,
)
from atlas_os.greenrock.scanner import ScanResult, latest_scan
from atlas_os.greenrock.staging import (
    LARGE_BUCKET,
    MEGA_BUCKET,
    SMALL_MID_BUCKET,
    add_staged_scan_candidate,
    load_staged_candidates,
    save_staged_candidates,
)


@dataclass(frozen=True)
class MarketPulseStageResult:
    scan_id: str
    staged_rows: tuple[dict[str, str], ...]
    replaced_existing: bool
    warnings: tuple[str, ...] = ()


TARGETS = (
    (ARCHETYPE_MEGA, MEGA_BUCKET, 1),
    (ARCHETYPE_LARGE, LARGE_BUCKET, 11),
)
SMALL_MID_ARCHETYPES = (ARCHETYPE_MID, ARCHETYPE_SMALL, ARCHETYPE_MICRO)
SMALL_MID_TARGET = 11
ANALYST_SLATE_TARGET = 23


def select_market_pulse_candidates(scan: ScanResult) -> tuple[tuple[dict[str, str], str], ...]:
    """Return ranked scan rows paired with their staging bucket."""

    rows = tuple(_pulse_row(row) for row in scan.rows)
    selected: list[tuple[dict[str, str], str]] = []
    seen: set[str] = set()
    for archetype, bucket, target in TARGETS:
        selected.extend(_take_rows(rows, (archetype,), bucket, target, seen))
    selected.extend(_take_rows(rows, SMALL_MID_ARCHETYPES, SMALL_MID_BUCKET, SMALL_MID_TARGET, seen))
    return tuple(selected)


def select_analyst_slate_candidates(scan: ScanResult, target: int = ANALYST_SLATE_TARGET) -> tuple[tuple[dict[str, str], str], ...]:
    rows = tuple(_pulse_row(row) for row in scan.rows)
    selected: list[tuple[dict[str, str], str]] = []
    seen: set[str] = set()
    for archetype in MARKET_ARCHETYPES:
        for row in rows:
            symbol = row.get("symbol", "").upper()
            if symbol and symbol not in seen and row.get("market_archetype", "") == archetype:
                selected.append((row, _bucket_for_row(row)))
                seen.add(symbol)
                break
    for row in rows:
        if len(selected) >= target:
            break
        symbol = row.get("symbol", "").upper()
        if not symbol or symbol in seen:
            continue
        selected.append((row, _bucket_for_row(row)))
        seen.add(symbol)
    return tuple(selected)


def stage_top_market_pulse_candidates(output_dir: Path, overwrite: bool = False) -> MarketPulseStageResult:
    return _stage_candidates(output_dir, select_market_pulse_candidates, overwrite, "Market Pulse staged candidate")


def stage_analyst_slate_candidates(output_dir: Path, overwrite: bool = False) -> MarketPulseStageResult:
    return _stage_candidates(output_dir, select_analyst_slate_candidates, overwrite, "Atlas Analyst slate candidate")


def _stage_candidates(output_dir: Path, selector, overwrite: bool, notes: str) -> MarketPulseStageResult:
    scan = latest_scan(output_dir)
    if scan is None:
        raise ValueError("No successful scan found. Run atlas greenrock scan --population all first.")
    existing = load_staged_candidates(output_dir)
    if existing and not overwrite:
        raise ValueError("Staging already has candidates. Confirm overwrite before staging candidates.")
    selected = selector(scan)
    if not selected:
        raise ValueError(f"Scan {scan.scan_id} has no candidates to stage.")
    if existing and overwrite:
        save_staged_candidates(output_dir, ())
    staged: list[dict[str, str]] = []
    warnings: list[str] = []
    for row, bucket in selected:
        ticker = row.get("symbol", "")
        try:
            staged.append(
                add_staged_scan_candidate(
                    output_dir,
                    scan.scan_id,
                    ticker,
                    bucket,
                    notes=notes,
                )
            )
        except ValueError as error:
            warnings.append(str(error))
    return MarketPulseStageResult(
        scan_id=scan.scan_id,
        staged_rows=tuple(staged),
        replaced_existing=bool(existing and overwrite),
        warnings=tuple(warnings),
    )


def _take_rows(
    rows: tuple[dict[str, str], ...],
    archetypes: tuple[str, ...],
    bucket: str,
    target: int,
    seen: set[str],
) -> list[tuple[dict[str, str], str]]:
    chosen: list[tuple[dict[str, str], str]] = []
    for row in rows:
        symbol = row.get("symbol", "").upper()
        if not symbol or symbol in seen:
            continue
        if row.get("market_archetype", "") not in archetypes:
            continue
        chosen.append((row, bucket))
        seen.add(symbol)
        if len(chosen) >= target:
            break
    return chosen


def _pulse_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    if not normalized.get("market_archetype", "").strip():
        normalized["market_archetype"] = classify_market_archetype(
            normalized.get("symbol", ""),
            _float_or_none(normalized.get("market_cap", "")),
            tuple(item for item in normalized.get("universe_membership", "").split("|") if item),
        )
    return normalized


def _bucket_for_row(row: dict[str, str]) -> str:
    return {
        "mega_rock": MEGA_BUCKET,
        "large_cap": LARGE_BUCKET,
        "small_cap": SMALL_MID_BUCKET,
        "small_mid": SMALL_MID_BUCKET,
    }.get(row.get("market_cap_bucket", "").strip().lower(), SMALL_MID_BUCKET)


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
