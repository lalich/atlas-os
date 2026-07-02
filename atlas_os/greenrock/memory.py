"""Local Atlas Memory for GreenRock scan history."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


MEMORY_HEADERS = [
    "scan_id",
    "scan_timestamp",
    "ticker",
    "company",
    "rank",
    "percentile",
    "greenrock_score",
    "confidence",
    "evidence_agreement",
    "research_priority",
    "fundamental_guardrail",
    "market_archetype",
    "top_bullish_signal",
    "top_caution_signal",
    "provider_membership",
    "source_population",
    "data_source",
]


@dataclass(frozen=True)
class MemoryComparison:
    ticker: str
    current: dict[str, str]
    previous: dict[str, str]
    rank_change: int | None
    score_change: float | None
    confidence_change: float | None
    evidence_change: float | None
    research_priority_changed: bool
    guardrail_changed: bool
    archetype_changed: bool

    @property
    def rank_direction(self) -> str:
        if self.rank_change is None or self.rank_change == 0:
            return "unchanged"
        return "improved" if self.rank_change < 0 else "deteriorated"


def memory_path(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / "memory" / "scan_memory.csv"


def load_memory_rows(output_dir: Path) -> tuple[dict[str, str], ...]:
    path = memory_path(output_dir)
    if not path.exists():
        return ()
    with path.open(newline="", encoding="utf-8") as csv_file:
        return tuple(_normalize_memory_row(row) for row in csv.DictReader(csv_file))


def save_memory_rows(output_dir: Path, rows: tuple[dict[str, str], ...]) -> None:
    path = memory_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=MEMORY_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_normalize_memory_row(row))


def ingest_scan_result(output_dir: Path, scan) -> int:
    existing = list(load_memory_rows(output_dir))
    seen = {(row["scan_id"], row["ticker"]) for row in existing}
    scan_timestamp = _timestamp_from_scan_id(scan.scan_id)
    added = 0
    for row in scan.rows:
        ticker = row.get("symbol", "").strip().upper()
        key = (scan.scan_id, ticker)
        if not ticker or key in seen:
            continue
        existing.append(
            _normalize_memory_row(
                {
                    "scan_id": scan.scan_id,
                    "scan_timestamp": scan_timestamp,
                    "ticker": ticker,
                    "company": row.get("company_name", ""),
                    "rank": row.get("rank", ""),
                    "percentile": row.get("percentile", ""),
                    "greenrock_score": row.get("greenrock_score", ""),
                    "confidence": row.get("greenrock_confidence", ""),
                    "evidence_agreement": row.get("evidence_agreement", ""),
                    "research_priority": row.get("research_priority", ""),
                    "fundamental_guardrail": row.get("fundamental_guardrail", ""),
                    "market_archetype": row.get("market_archetype", ""),
                    "top_bullish_signal": row.get("top_bullish_signal", ""),
                    "top_caution_signal": row.get("top_caution_signal", ""),
                    "provider_membership": row.get("universe_membership", ""),
                    "source_population": scan.population,
                    "data_source": scan.data_source,
                }
            )
        )
        seen.add(key)
        added += 1
    if added:
        save_memory_rows(output_dir, tuple(existing))
    elif not memory_path(output_dir).exists():
        save_memory_rows(output_dir, tuple(existing))
    return added


def memory_summary(output_dir: Path) -> dict[str, str | int]:
    rows = load_memory_rows(output_dir)
    scan_ids = sorted({row["scan_id"] for row in rows})
    latest = latest_scan_id(output_dir) or ""
    return {
        "total_scans": len(scan_ids),
        "total_observations": len(rows),
        "latest_scan_id": latest,
        "unique_tickers": len({row["ticker"] for row in rows}),
    }


def latest_scan_id(output_dir: Path) -> str | None:
    rows = load_memory_rows(output_dir)
    if not rows:
        return None
    return max(rows, key=lambda row: (row.get("scan_timestamp", ""), row.get("scan_id", ""))).get("scan_id", "")


def ticker_history(output_dir: Path, ticker: str) -> tuple[dict[str, str], ...]:
    symbol = ticker.strip().upper()
    rows = tuple(row for row in load_memory_rows(output_dir) if row["ticker"] == symbol)
    return tuple(sorted(rows, key=lambda row: (row.get("scan_timestamp", ""), row.get("scan_id", "")), reverse=True))


def compare_ticker(output_dir: Path, ticker: str, scan_id: str | None = None) -> MemoryComparison | None:
    history = ticker_history(output_dir, ticker)
    if not history:
        return None
    if scan_id:
        current_index = next((index for index, row in enumerate(history) if row["scan_id"] == scan_id), None)
        if current_index is None:
            return None
    else:
        current_index = 0
    if current_index + 1 >= len(history):
        return None
    return _comparison(history[current_index], history[current_index + 1])


def latest_comparisons(output_dir: Path) -> tuple[MemoryComparison, ...]:
    latest = latest_scan_id(output_dir)
    if not latest:
        return ()
    current_rows = tuple(row for row in load_memory_rows(output_dir) if row["scan_id"] == latest)
    comparisons = []
    for row in current_rows:
        comparison = compare_ticker(output_dir, row["ticker"], latest)
        if comparison is not None:
            comparisons.append(comparison)
    return tuple(comparisons)


def memory_movers(output_dir: Path, limit: int = 5) -> dict[str, tuple[MemoryComparison, ...]]:
    comparisons = latest_comparisons(output_dir)
    rank_improvers = tuple(sorted((item for item in comparisons if item.rank_change is not None and item.rank_change < 0), key=lambda item: item.rank_change)[:limit])
    score_improvers = tuple(sorted((item for item in comparisons if (item.score_change or 0) > 0), key=lambda item: item.score_change or 0, reverse=True)[:limit])
    confidence_improvers = tuple(sorted((item for item in comparisons if (item.confidence_change or 0) > 0), key=lambda item: item.confidence_change or 0, reverse=True)[:limit])
    evidence_improvers = tuple(sorted((item for item in comparisons if (item.evidence_change or 0) > 0), key=lambda item: item.evidence_change or 0, reverse=True)[:limit])
    deteriorations = tuple(
        sorted(
            (
                item
                for item in comparisons
                if (item.rank_change is not None and item.rank_change > 0)
                or (item.score_change or 0) < 0
                or (item.confidence_change or 0) < 0
                or (item.evidence_change or 0) < 0
            ),
            key=_deterioration_key,
            reverse=True,
        )[:limit]
    )
    return {
        "rank_improvers": rank_improvers,
        "score_improvers": score_improvers,
        "confidence_improvers": confidence_improvers,
        "evidence_improvers": evidence_improvers,
        "deteriorations": deteriorations,
    }


def movement_explanation(comparison: MemoryComparison | None) -> str:
    if comparison is None:
        return "No prior scan comparison available."
    parts = [
        _rank_text(comparison.rank_change),
        _delta_text("GreenRock Score", comparison.previous.get("greenrock_score", ""), comparison.current.get("greenrock_score", ""), comparison.score_change),
        _delta_text("Confidence", comparison.previous.get("confidence", ""), comparison.current.get("confidence", ""), comparison.confidence_change),
        _delta_text(
            "Evidence Agreement",
            comparison.previous.get("evidence_agreement", ""),
            comparison.current.get("evidence_agreement", ""),
            comparison.evidence_change,
        ),
    ]
    if comparison.research_priority_changed:
        parts.append(
            f"research priority changed from {comparison.previous.get('research_priority', 'not recorded')} to {comparison.current.get('research_priority', 'not recorded')}"
        )
    if comparison.guardrail_changed:
        parts.append(
            f"guardrail changed from {comparison.previous.get('fundamental_guardrail', 'not recorded')} to {comparison.current.get('fundamental_guardrail', 'not recorded')}"
        )
    if comparison.archetype_changed:
        parts.append(
            f"archetype changed from {comparison.previous.get('market_archetype', 'not recorded')} to {comparison.current.get('market_archetype', 'not recorded')}"
        )
    clean = [part for part in parts if part]
    return "; ".join(clean) + "." if clean else "No material movement versus the prior scan."


def movement_symbol(comparison: MemoryComparison | None) -> str:
    if comparison is None or comparison.rank_change is None or comparison.rank_change == 0:
        return "->"
    return "^" if comparison.rank_change < 0 else "v"


def _comparison(current: dict[str, str], previous: dict[str, str]) -> MemoryComparison:
    return MemoryComparison(
        ticker=current["ticker"],
        current=current,
        previous=previous,
        rank_change=_int(current.get("rank", "")) - _int(previous.get("rank", "")) if _int(current.get("rank", "")) is not None and _int(previous.get("rank", "")) is not None else None,
        score_change=_delta(current.get("greenrock_score", ""), previous.get("greenrock_score", "")),
        confidence_change=_delta(current.get("confidence", ""), previous.get("confidence", "")),
        evidence_change=_delta(current.get("evidence_agreement", ""), previous.get("evidence_agreement", "")),
        research_priority_changed=current.get("research_priority", "") != previous.get("research_priority", ""),
        guardrail_changed=current.get("fundamental_guardrail", "") != previous.get("fundamental_guardrail", ""),
        archetype_changed=current.get("market_archetype", "") != previous.get("market_archetype", ""),
    )


def _normalize_memory_row(row: dict[str, str]) -> dict[str, str]:
    normalized = {header: row.get(header, "") for header in MEMORY_HEADERS}
    normalized["ticker"] = normalized["ticker"].strip().upper()
    return normalized


def _timestamp_from_scan_id(scan_id: str) -> str:
    suffix = scan_id.rsplit("-", maxsplit=1)[-1]
    if len(suffix) == 14 and suffix.isdigit():
        return f"{suffix[0:4]}-{suffix[4:6]}-{suffix[6:8]}T{suffix[8:10]}:{suffix[10:12]}:{suffix[12:14]}Z"
    return suffix


def _rank_text(change: int | None) -> str:
    if change is None:
        return ""
    if change < 0:
        return f"rank improved by {abs(change)} positions"
    if change > 0:
        return f"rank deteriorated by {change} positions"
    return "rank was unchanged"


def _delta_text(label: str, previous: str, current: str, change: float | None) -> str:
    if change is None:
        return ""
    if change > 0:
        direction = "improved"
    elif change < 0:
        direction = "weakened"
    else:
        direction = "was unchanged"
        return f"{label} {direction} at {current or 'not recorded'}"
    return f"{label} {direction} from {previous or 'not recorded'} to {current or 'not recorded'}"


def _deterioration_key(item: MemoryComparison) -> float:
    return max(
        float(item.rank_change or 0),
        abs(min(0.0, item.score_change or 0.0)),
        abs(min(0.0, item.confidence_change or 0.0)),
        abs(min(0.0, item.evidence_change or 0.0)),
    )


def _delta(current: str, previous: str) -> float | None:
    if not current or not previous:
        return None
    return _float(current) - _float(previous)


def _int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
