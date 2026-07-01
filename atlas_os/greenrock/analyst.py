"""Deterministic Atlas Analyst report intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas_os.greenrock.market_engine import MARKET_ARCHETYPES, classify_market_archetype
from atlas_os.greenrock.scanner import ScanResult, load_scan


@dataclass(frozen=True)
class PriorScanComparison:
    previous_scan_id: str
    previous_rank: str
    rank_change: int | None
    previous_score: str
    score_change: float | None
    previous_confidence: str
    confidence_change: float | None
    previous_evidence_agreement: str
    evidence_change: float | None


@dataclass(frozen=True)
class AnalystCandidate:
    ticker: str
    archetype: str
    rank: str
    scan_size: int
    greenrock_score: str
    confidence: str
    evidence_agreement: str
    guardrail: str
    research_priority: str
    source_scan_id: str
    top_bullish_signal: str
    top_caution_signal: str
    staged_bucket: str
    notes: str
    summary: str
    prior_summary: str
    bullish_evidence: str
    bearish_evidence: str
    watch_next: str
    prior: PriorScanComparison | None = None


def analyst_candidate_from_staged_row(output_dir: Path, row: dict[str, str]) -> AnalystCandidate:
    scan, scan_row = _source_scan_row(output_dir, row)
    ticker = row.get("ticker", "").upper()
    source_scan_id = row.get("source_scan_id", "")
    scan_size = len(scan.rows) if scan else 0
    rank = scan_row.get("rank", "") if scan_row else ""
    archetype = _archetype(row, scan_row)
    score = scan_row.get("greenrock_score", row.get("greenrock_score", "")) if scan_row else row.get("greenrock_score", "")
    confidence = scan_row.get("greenrock_confidence", row.get("confidence", "")) if scan_row else row.get("confidence", "")
    evidence = scan_row.get("evidence_agreement", row.get("evidence_agreement", "")) if scan_row else row.get("evidence_agreement", "")
    guardrail = scan_row.get("fundamental_guardrail", row.get("guardrail", "")) if scan_row else row.get("guardrail", "")
    priority = scan_row.get("research_priority", row.get("research_priority", "")) if scan_row else row.get("research_priority", "")
    bullish = scan_row.get("top_bullish_signal", row.get("top_bullish_signal", "")) if scan_row else row.get("top_bullish_signal", "")
    caution = scan_row.get("top_caution_signal", row.get("top_caution_signal", "")) if scan_row else row.get("top_caution_signal", "")
    prior = prior_scan_comparison(output_dir, source_scan_id, ticker) if source_scan_id else None
    prior_summary = _prior_summary(prior)
    summary = _analyst_summary(
        ticker=ticker,
        rank=rank,
        scan_size=scan_size,
        archetype=archetype,
        confidence=confidence,
        priority=priority,
        evidence=evidence,
        bullish=bullish,
        caution=caution,
        prior_summary=prior_summary,
    )
    return AnalystCandidate(
        ticker=ticker,
        archetype=archetype,
        rank=rank,
        scan_size=scan_size,
        greenrock_score=score,
        confidence=confidence,
        evidence_agreement=evidence,
        guardrail=guardrail,
        research_priority=priority,
        source_scan_id=source_scan_id,
        top_bullish_signal=bullish,
        top_caution_signal=caution,
        staged_bucket=row.get("staged_bucket", ""),
        notes=row.get("notes", ""),
        summary=summary,
        prior_summary=prior_summary,
        bullish_evidence=bullish or "No top bullish signal recorded.",
        bearish_evidence=caution or "No top caution signal recorded.",
        watch_next=_watch_next(evidence, guardrail, caution),
        prior=prior,
    )


def analyst_candidates(output_dir: Path, rows: tuple[dict[str, str], ...]) -> tuple[AnalystCandidate, ...]:
    return tuple(analyst_candidate_from_staged_row(output_dir, row) for row in rows)


def archetype_leaders(candidates: tuple[AnalystCandidate, ...]) -> tuple[AnalystCandidate, ...]:
    leaders: list[AnalystCandidate] = []
    seen: set[str] = set()
    for archetype in MARKET_ARCHETYPES:
        rows = tuple(candidate for candidate in candidates if candidate.archetype == archetype and candidate.ticker not in seen)
        if rows:
            leader = sorted(rows, key=_candidate_rank_key)[0]
            leaders.append(leader)
            seen.add(leader.ticker)
    return tuple(leaders)


def remaining_candidates(
    candidates: tuple[AnalystCandidate, ...],
    leaders: tuple[AnalystCandidate, ...],
) -> tuple[AnalystCandidate, ...]:
    featured = {candidate.ticker for candidate in leaders}
    return tuple(candidate for candidate in sorted(candidates, key=_candidate_rank_key) if candidate.ticker not in featured)


def prior_scan_comparison(output_dir: Path, current_scan_id: str, ticker: str) -> PriorScanComparison | None:
    scan_ids = _scan_ids_newest_first(output_dir)
    if current_scan_id not in scan_ids:
        return None
    normalized = ticker.upper()
    for scan_id in scan_ids[scan_ids.index(current_scan_id) + 1 :]:
        try:
            scan = load_scan(output_dir, scan_id)
        except ValueError:
            continue
        row = next((item for item in scan.rows if item.get("symbol", "").upper() == normalized), None)
        if row is None:
            continue
        current_row = next((item for item in load_scan(output_dir, current_scan_id).rows if item.get("symbol", "").upper() == normalized), None)
        return PriorScanComparison(
            previous_scan_id=scan.scan_id,
            previous_rank=row.get("rank", ""),
            rank_change=_int(current_row.get("rank", "") if current_row else "") - _int(row.get("rank", "")) if current_row else None,
            previous_score=row.get("greenrock_score", ""),
            score_change=_delta(current_row.get("greenrock_score", "") if current_row else "", row.get("greenrock_score", "")),
            previous_confidence=row.get("greenrock_confidence", ""),
            confidence_change=_delta(current_row.get("greenrock_confidence", "") if current_row else "", row.get("greenrock_confidence", "")),
            previous_evidence_agreement=row.get("evidence_agreement", ""),
            evidence_change=_delta(current_row.get("evidence_agreement", "") if current_row else "", row.get("evidence_agreement", "")),
        )
    return None


def _source_scan_row(output_dir: Path, row: dict[str, str]) -> tuple[ScanResult | None, dict[str, str] | None]:
    scan_id = row.get("source_scan_id", "")
    ticker = row.get("ticker", "").upper()
    if not scan_id or not ticker:
        return None, None
    try:
        scan = load_scan(output_dir, scan_id)
    except ValueError:
        return None, None
    scan_row = next((item for item in scan.rows if item.get("symbol", "").upper() == ticker), None)
    return scan, scan_row


def _archetype(row: dict[str, str], scan_row: dict[str, str] | None) -> str:
    if scan_row and scan_row.get("market_archetype", ""):
        return scan_row["market_archetype"]
    return classify_market_archetype(row.get("ticker", ""))


def _analyst_summary(
    ticker: str,
    rank: str,
    scan_size: int,
    archetype: str,
    confidence: str,
    priority: str,
    evidence: str,
    bullish: str,
    caution: str,
    prior_summary: str,
) -> str:
    rank_text = f"ranks {_ordinal(rank)} out of {scan_size} securities scanned" if rank and scan_size else "is staged from the latest available research scan"
    confidence_text = _confidence_label(confidence)
    why = bullish or "the available GreenRock evidence set"
    caution_text = caution or "no primary caution was recorded"
    return (
        f"{ticker} {rank_text}. Atlas flags it as a {archetype} opportunity with {confidence_text} "
        f"and a Research Priority of {priority or 'Not Recorded'}. The setup is supported by {why}, "
        f"with Evidence Agreement at {evidence or 'not recorded'}. The primary remaining caution is {caution_text}. "
        f"{prior_summary}"
    )


def _prior_summary(prior: PriorScanComparison | None) -> str:
    if prior is None:
        return "No prior scan comparison available."
    changes = [
        _rank_change_text(prior.rank_change),
        _change_text("GreenRock Score", prior.score_change),
        _change_text("Confidence", prior.confidence_change),
        _change_text("Evidence Agreement", prior.evidence_change),
    ]
    return (
        f"Prior scan comparison: previous rank {prior.previous_rank or 'not recorded'} in {prior.previous_scan_id}; "
        + "; ".join(change for change in changes if change)
        + "."
    )


def _watch_next(evidence: str, guardrail: str, caution: str) -> str:
    if caution:
        return f"Watch whether the caution improves: {caution}"
    if guardrail and guardrail not in {"Supportive", "Strong Balance Sheet", "Acceptable"}:
        return f"Watch whether the Fundamental Guardrail moves from {guardrail} toward a more supportive reading."
    if _float(evidence) < 70:
        return "Watch for stronger Evidence Agreement before elevating research priority."
    return "Watch for sustained confirmation across price trend, volume participation, and evidence agreement."


def _scan_ids_newest_first(output_dir: Path) -> list[str]:
    scans_dir = Path(output_dir) / "greenrock" / "scans"
    if not scans_dir.exists():
        return []
    return [path.name for path in sorted((path for path in scans_dir.iterdir() if path.is_dir()), reverse=True)]


def _candidate_rank_key(candidate: AnalystCandidate) -> tuple[int, str]:
    return (_int(candidate.rank) or 999999, candidate.ticker)


def _ordinal(value: str) -> str:
    number = _int(value)
    if number is None:
        return value
    suffix = "th"
    if number % 100 not in {11, 12, 13}:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _confidence_label(value: str) -> str:
    confidence = _float(value)
    if confidence >= 90:
        return "Very High Confidence"
    if confidence >= 75:
        return "High Confidence"
    if confidence >= 60:
        return "Moderate Confidence"
    if confidence >= 40:
        return "Low Confidence"
    return "Very Low Confidence" if value else "Confidence not recorded"


def _rank_change_text(change: int | None) -> str:
    if change is None:
        return ""
    if change < 0:
        return f"rank improved by {abs(change)} places"
    if change > 0:
        return f"rank weakened by {change} places"
    return "rank was unchanged"


def _change_text(label: str, change: float | None) -> str:
    if change is None:
        return ""
    if change > 0:
        return f"{label} increased by {change:.2f}"
    if change < 0:
        return f"{label} decreased by {abs(change):.2f}"
    return f"{label} was unchanged"


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
