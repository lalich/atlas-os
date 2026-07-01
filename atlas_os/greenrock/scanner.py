"""GreenRock population scanner."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from atlas_os.greenrock.criteria import evaluate_stock
from atlas_os.greenrock.market_engine import classify_market_archetype
from atlas_os.greenrock.market_data import MarketDataConfigurationError, MarketDataProvider, YFinanceMarketDataProvider
from atlas_os.greenrock.population import ALL_POPULATION, GREENROCK_POPULATION_LABELS, population_tickers
from atlas_os.greenrock.ranking import rank_candidate_rows
from atlas_os.greenrock.score import (
    build_evidence_items,
    candidate_evidence_agreement,
    confidence_band,
    score_signal,
    top_evidence_signal,
)
from atlas_os.greenrock.universe_manager import default_universe_manager
from atlas_os.greenrock.universe import (
    LARGE_CAP_UNIVERSE,
    MEGA_ROCK_UNIVERSE,
    RANKED_CANDIDATES_PLACEMENT,
    PERSONAL_WATCHLIST_PLACEMENT,
    SMALL_MID_CAP_UNIVERSE,
    STRICT_REVIEW_PLACEMENT,
    WATCHLIST_PLACEMENT,
    TickerPlacementResult,
    add_ticker_to_greenrock_list,
)


@dataclass(frozen=True)
class ScanResult:
    scan_id: str
    population: str
    data_source: str
    results_path: Path
    summary_path: Path
    rows: tuple[dict[str, str], ...]
    warnings: tuple[str, ...] = ()
    configured_ticker_count: int = 0
    fetched_ticker_count: int = 0
    skipped_ticker_count: int = 0
    provider_failure_count: int = 0
    duplicates_removed: int = 0


FAILURE_HEADERS = [
    "ticker",
    "failure_reason",
    "provider_membership",
    "suggested_action",
]


PROMOTION_METADATA_HEADERS = [
    "ticker",
    "destination_list",
    "scan_id",
    "score",
    "confidence",
    "evidence_agreement",
    "research_priority",
    "guardrail",
    "promoted_at",
]


SCAN_HEADERS = [
    "rank",
    "symbol",
    "company_name",
    "market_cap_bucket",
    "market_archetype",
    "market_cap",
    "price",
    "greenrock_score",
    "greenrock_confidence",
    "confidence_band",
    "evidence_agreement",
    "fundamental_guardrail",
    "research_priority",
    "percentile",
    "universe_membership",
    "top_bullish_signal",
    "top_caution_signal",
    "data_quality_warnings",
    "finviz",
]


def run_population_scan(
    output_dir: Path,
    population: str,
    provider: MarketDataProvider | None = None,
) -> ScanResult:
    normalized_population = population.strip().lower()
    universe_manager = default_universe_manager(output_dir)
    master = universe_manager.master_universe()
    membership_by_ticker = {row.ticker: row.provider_membership for row in master.rows}
    if normalized_population == ALL_POPULATION:
        tickers = tuple(row.ticker for row in master.rows)
        duplicates_removed = master.duplicates_removed
    else:
        tickers = population_tickers(output_dir, normalized_population)
        duplicates_removed = 0
    if not tickers:
        raise MarketDataConfigurationError(f"Population {population} has no tickers.")
    market_data_provider = provider or _provider_for_scan(tickers, normalized_population)
    stocks = market_data_provider.fetch_stocks()
    rows = [_candidate_row(evaluate_stock(stock)) for stock in stocks]
    rows.sort(key=lambda row: _row_sort_key(row), reverse=True)
    ranked_rows = rank_candidate_rows(tuple(rows), membership_by_ticker)
    fetched_count = len(stocks)
    provider_failures = tuple(getattr(market_data_provider, "provider_failures", ()))
    warnings = _scan_warnings(tickers, stocks) + provider_failures
    failure_rows = _failure_rows(tickers, stocks, membership_by_ticker, provider_failures)

    scan_id = f"scan-{normalized_population}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    scan_dir = Path(output_dir) / "greenrock" / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    results_path = scan_dir / "scan_results.csv"
    summary_path = scan_dir / "scan_summary.md"
    _write_results_csv(results_path, ranked_rows)
    _write_failures_csv(scan_dir / "scan_failures.csv", failure_rows)
    _write_summary(
        summary_path,
        scan_id,
        normalized_population,
        market_data_provider.source_name,
        ranked_rows,
        configured_ticker_count=len(tickers),
        fetched_ticker_count=fetched_count,
        skipped_ticker_count=max(0, len(tickers) - fetched_count),
        provider_failure_count=len(provider_failures),
        duplicates_removed=duplicates_removed,
    )
    return ScanResult(
        scan_id=scan_id,
        population=normalized_population,
        data_source=market_data_provider.source_name,
        results_path=results_path,
        summary_path=summary_path,
        rows=ranked_rows,
        warnings=warnings,
        configured_ticker_count=len(tickers),
        fetched_ticker_count=fetched_count,
        skipped_ticker_count=max(0, len(tickers) - fetched_count),
        provider_failure_count=len(provider_failures),
        duplicates_removed=duplicates_removed,
    )


def latest_scan(output_dir: Path) -> ScanResult | None:
    scans_dir = Path(output_dir) / "greenrock" / "scans"
    if not scans_dir.exists():
        return None
    scan_dirs = sorted((path for path in scans_dir.iterdir() if path.is_dir()), key=_scan_dir_sort_key, reverse=True)
    for scan_dir in scan_dirs:
        results_path = scan_dir / "scan_results.csv"
        summary_path = scan_dir / "scan_summary.md"
        if results_path.exists():
            rows = _read_results_csv(results_path)
            if not rows:
                continue
            population = scan_dir.name.removeprefix("scan-").rsplit("-", maxsplit=1)[0]
            return ScanResult(
                scan_id=scan_dir.name,
                population=population,
                data_source=_summary_value(summary_path, "Data Source") if summary_path.exists() else "unknown",
                results_path=results_path,
                summary_path=summary_path,
                rows=rows,
                configured_ticker_count=_summary_int(summary_path, "Total Configured Tickers"),
                fetched_ticker_count=_summary_int(summary_path, "Tickers Successfully Fetched/Scored"),
                skipped_ticker_count=_summary_int(summary_path, "Skipped Tickers"),
                provider_failure_count=_summary_int(summary_path, "Provider Failures"),
                duplicates_removed=_summary_int(summary_path, "Duplicates Removed"),
            )
    return None


def load_scan(output_dir: Path, scan_id: str) -> ScanResult:
    clean_scan_id = _clean_scan_id(scan_id)
    scan_dir = Path(output_dir) / "greenrock" / "scans" / clean_scan_id
    results_path = scan_dir / "scan_results.csv"
    summary_path = scan_dir / "scan_summary.md"
    if not results_path.exists():
        raise ValueError(f"Scan {scan_id} was not found.")
    rows = _read_results_csv(results_path)
    population = clean_scan_id.removeprefix("scan-").rsplit("-", maxsplit=1)[0]
    return ScanResult(
        scan_id=clean_scan_id,
        population=population,
        data_source=_summary_value(summary_path, "Data Source") if summary_path.exists() else "unknown",
        results_path=results_path,
        summary_path=summary_path,
        rows=rows,
        configured_ticker_count=_summary_int(summary_path, "Total Configured Tickers"),
        fetched_ticker_count=_summary_int(summary_path, "Tickers Successfully Fetched/Scored"),
        skipped_ticker_count=_summary_int(summary_path, "Skipped Tickers"),
        provider_failure_count=_summary_int(summary_path, "Provider Failures"),
        duplicates_removed=_summary_int(summary_path, "Duplicates Removed"),
    )


def promote_scan_ticker(
    output_dir: Path,
    scan_id: str,
    ticker: str,
    list_key: str,
) -> TickerPlacementResult:
    scan = load_scan(output_dir, scan_id)
    normalized_ticker = ticker.strip().upper()
    row = next((item for item in scan.rows if item.get("symbol", "").upper() == normalized_ticker), None)
    if row is None:
        raise ValueError(f"{normalized_ticker} was not found in scan {scan.scan_id}.")
    placement = add_ticker_to_greenrock_list(
        output_dir,
        normalized_ticker,
        _promotion_list_key(list_key),
        market_cap_bucket=row.get("market_cap_bucket", ""),
    )
    if placement.added:
        _append_promotion_metadata(output_dir, scan, row, placement.list_key)
    return placement


def promotion_metadata_path(output_dir: Path) -> Path:
    return Path(output_dir) / "greenrock" / "watchlists" / "promotion_metadata.csv"


def load_promotion_metadata(output_dir: Path) -> tuple[dict[str, str], ...]:
    path = promotion_metadata_path(output_dir)
    if not path.exists():
        return ()
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        return tuple(dict(row) for row in reader)


def _provider_for_scan(tickers: tuple[str, ...], population: str) -> MarketDataProvider:
    import os

    provider_name = os.getenv("ATLAS_MARKET_DATA_PROVIDER", "").strip().lower()
    if provider_name != "yfinance":
        raise MarketDataConfigurationError(
            "GreenRock population scan requires ATLAS_MARKET_DATA_PROVIDER=yfinance. "
            "Configure real data locally with: export ATLAS_MARKET_DATA_PROVIDER=yfinance; "
            "python3 -m pip install -e \".[market-data]\""
        )
    return YFinanceMarketDataProvider(tickers, universe_name=f"population_{population}")


def _promotion_list_key(list_key: str) -> str:
    aliases = {
        "watchlist": WATCHLIST_PLACEMENT,
        "personal": PERSONAL_WATCHLIST_PLACEMENT,
        "personal_watchlist": PERSONAL_WATCHLIST_PLACEMENT,
        "ranked": RANKED_CANDIDATES_PLACEMENT,
        "ranked_candidates": RANKED_CANDIDATES_PLACEMENT,
        "strict": STRICT_REVIEW_PLACEMENT,
        "strict_review": STRICT_REVIEW_PLACEMENT,
        "mega_rock": MEGA_ROCK_UNIVERSE,
        "large_cap": LARGE_CAP_UNIVERSE,
        "small_mid": SMALL_MID_CAP_UNIVERSE,
        "small_mid_cap": SMALL_MID_CAP_UNIVERSE,
    }
    normalized = list_key.strip().lower()
    if normalized not in aliases:
        raise ValueError("Choose a valid promotion list.")
    return aliases[normalized]


def _clean_scan_id(scan_id: str) -> str:
    clean_scan_id = scan_id.strip()
    if not clean_scan_id or "/" in clean_scan_id or ".." in clean_scan_id:
        raise ValueError("Choose a valid scan ID.")
    return clean_scan_id


def _append_promotion_metadata(output_dir: Path, scan: ScanResult, row: dict[str, str], list_key: str) -> None:
    path = promotion_metadata_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    metadata_row = {
        "ticker": row.get("symbol", "").upper(),
        "destination_list": list_key,
        "scan_id": scan.scan_id,
        "score": row.get("greenrock_score", ""),
        "confidence": row.get("greenrock_confidence", ""),
        "evidence_agreement": row.get("evidence_agreement", ""),
        "research_priority": row.get("research_priority", ""),
        "guardrail": row.get("fundamental_guardrail", ""),
        "promoted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=PROMOTION_METADATA_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(metadata_row)


def _candidate_row(candidate) -> dict[str, str]:
    evidence_items = build_evidence_items(candidate)
    evidence_agreement = candidate_evidence_agreement(candidate)
    data_warnings = []
    if not candidate.has_price_history:
        data_warnings.append("missing price history")
    if not candidate.has_market_cap:
        data_warnings.append("missing market cap")
    if not candidate.has_volume_data:
        data_warnings.append("missing volume data")
    if candidate.skipped_reason:
        data_warnings.append(candidate.skipped_reason.replace("_", " "))
    confidence = _scan_confidence(candidate, evidence_agreement, data_warnings, evidence_items)
    return {
        "rank": "",
        "symbol": candidate.symbol,
        "company_name": candidate.company_name,
        "market_cap_bucket": candidate.market_cap_bucket,
        "market_archetype": classify_market_archetype(candidate.symbol, candidate.market_cap),
        "market_cap": f"{candidate.market_cap:.2f}",
        "price": f"{candidate.indicators.latest_close:.2f}",
        "greenrock_score": f"{candidate.score:.2f}",
        "greenrock_confidence": f"{confidence:.2f}",
        "confidence_band": confidence_band(confidence),
        "evidence_agreement": f"{evidence_agreement:.2f}",
        "fundamental_guardrail": _guardrail_label(candidate),
        "research_priority": _research_priority(candidate.score, confidence),
        "top_bullish_signal": top_evidence_signal(candidate, "bullish"),
        "top_caution_signal": top_evidence_signal(candidate, "bearish"),
        "data_quality_warnings": "; ".join(data_warnings) if data_warnings else "none",
        "finviz": f"https://finviz.com/quote.ashx?t={candidate.symbol}",
    }


def _scan_confidence(candidate, evidence_agreement: float, data_warnings: list[str], evidence_items) -> float:
    score = 45 + evidence_agreement * 0.35
    if candidate.has_price_history:
        score += 8
    if candidate.has_volume_data:
        score += 5
    if candidate.has_market_cap:
        score += 5
    if any(item.category == "fundamental" and item.direction == "bullish" for item in evidence_items):
        score += 5
    if any(item.category == "fundamental" and item.direction == "bearish" for item in evidence_items):
        score -= 8
    score -= min(12, len(data_warnings) * 4)
    return round(max(0.0, min(100.0, score)), 2)


def _research_priority(score: float, confidence: float) -> str:
    if confidence < 35:
        return "Ignore"
    if score >= 85 and confidence >= 75:
        return "Immediate Review"
    if score >= 70 and confidence >= 65:
        return "This Week"
    if score >= 55 and confidence >= 50:
        return "Interesting"
    if score >= 45 and confidence >= 45:
        return "Monitor"
    return "Ignore"


def _guardrail_label(candidate) -> str:
    fundamentals = candidate.fundamentals
    if fundamentals is None or fundamentals.quick_ratio is None or fundamentals.net_cash is None or fundamentals.shares_outstanding_change_percent is None:
        return "Insufficient Data"
    if fundamentals.quick_ratio < 0.75 or fundamentals.shares_outstanding_change_percent >= 0.20:
        return "Red Flag"
    if (
        fundamentals.quick_ratio < 1.0
        or fundamentals.shares_outstanding_change_percent >= 0.10
        or (candidate.market_cap > 0 and fundamentals.net_cash < -candidate.market_cap * 0.25)
    ):
        return "Caution"
    if fundamentals.net_cash > 0 and fundamentals.quick_ratio >= 1.5 and fundamentals.shares_outstanding_change_percent <= 0.02:
        return "Strong Balance Sheet"
    return "Acceptable"


def _row_sort_key(row: dict[str, str]) -> tuple[float, float, float]:
    return (
        float(row["greenrock_score"]),
        float(row["greenrock_confidence"]),
        float(row["evidence_agreement"]),
    )


def _write_results_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=SCAN_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def _read_results_csv(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return tuple(_normalize_scan_row(row) for row in csv.DictReader(csv_file))


def scan_failures_path(output_dir: Path, scan_id: str) -> Path:
    return Path(output_dir) / "greenrock" / "scans" / _clean_scan_id(scan_id) / "scan_failures.csv"


def load_scan_failures(output_dir: Path, scan_id: str | None = None) -> tuple[dict[str, str], ...]:
    scan = load_scan(output_dir, scan_id) if scan_id else latest_scan(output_dir)
    if scan is None:
        return ()
    path = scan_failures_path(output_dir, scan.scan_id)
    if path.exists():
        with path.open(newline="", encoding="utf-8") as csv_file:
            return tuple(dict(row) for row in csv.DictReader(csv_file))
    return _inferred_failure_rows(output_dir, scan)


def universe_health_rows(output_dir: Path) -> tuple[dict[str, str], ...]:
    return load_scan_failures(output_dir)


def cleanup_failed_tickers(output_dir: Path, confirm: bool = False) -> tuple[dict[str, str], ...]:
    failures = tuple(row for row in universe_health_rows(output_dir) if row.get("suggested_action") == "remove from seed")
    if not confirm:
        return failures
    from atlas_os.greenrock.population import load_population, save_population

    affected: list[dict[str, str]] = []
    removals_by_provider: dict[str, set[str]] = {}
    for row in failures:
        ticker = row.get("ticker", "").strip().upper()
        for provider in row.get("provider_membership", "").split("|"):
            if provider in GREENROCK_POPULATION_LABELS and ticker:
                removals_by_provider.setdefault(provider, set()).add(ticker)
    for provider, tickers in removals_by_provider.items():
        population = load_population(output_dir, provider)
        kept = tuple(ticker for ticker in population.tickers if ticker not in tickers)
        if len(kept) != len(population.tickers):
            save_population(output_dir, provider, kept)
            for ticker in sorted(tickers):
                affected.append({"ticker": ticker, "provider": provider, "action": "removed"})
    return tuple(affected)


def _write_summary(
    path: Path,
    scan_id: str,
    population: str,
    data_source: str,
    rows: tuple[dict[str, str], ...],
    configured_ticker_count: int,
    fetched_ticker_count: int,
    skipped_ticker_count: int,
    provider_failure_count: int,
    duplicates_removed: int,
) -> None:
    label = "Master Universe" if population == ALL_POPULATION else GREENROCK_POPULATION_LABELS.get(population, population)
    lines = [
        "# GreenRock Population Scan Summary",
        "",
        f"**Scan ID:** {scan_id}",
        f"**Population:** {label}",
        f"**Data Source:** {data_source}",
        f"**Total Configured Tickers:** {configured_ticker_count}",
        f"**Tickers Successfully Fetched/Scored:** {fetched_ticker_count}",
        f"**Skipped Tickers:** {skipped_ticker_count}",
        f"**Provider Failures:** {provider_failure_count}",
        f"**Duplicates Removed:** {duplicates_removed}",
        f"**Ranked Count:** {len(rows)}",
        "",
        "> Local research scan only. Not a report, not an approval, not investment advice, and not published.",
        "",
        "| Rank | Percentile | Symbol | Score | Confidence | Evidence Agreement | Guardrail | Priority | Universe Membership |",
        "|---:|---:|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows[:25]:
        lines.append(
            f"| {row['rank']} | {row.get('percentile', '')} | {row['symbol']} | {row['greenrock_score']} | "
            f"{row['greenrock_confidence']} | {row['evidence_agreement']} | "
            f"{row['fundamental_guardrail']} | {row['research_priority']} | {row.get('universe_membership', '')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_failures_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FAILURE_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def _summary_value(path: Path, label: str) -> str:
    prefix = f"**{label}:**"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return "unknown"


def _summary_int(path: Path, label: str) -> int:
    if not path.exists():
        return 0
    try:
        return int(_summary_value(path, label))
    except ValueError:
        return 0


def _normalize_scan_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    if not normalized.get("market_archetype", "").strip():
        normalized["market_archetype"] = classify_market_archetype(
            normalized.get("symbol", ""),
            _float_or_none(normalized.get("market_cap", "")),
            tuple(item for item in normalized.get("universe_membership", "").split("|") if item),
        )
    return normalized


def _scan_warnings(tickers, stocks) -> tuple[str, ...]:
    returned = {stock.symbol for stock in stocks}
    missing = tuple(ticker for ticker in tickers if ticker not in returned)
    if missing:
        return (f"{len(missing)} ticker(s) did not return usable provider data.",)
    return ()


def _failure_rows(
    tickers: tuple[str, ...],
    stocks,
    membership_by_ticker: dict[str, tuple[str, ...]],
    provider_failures: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    returned = {stock.symbol for stock in stocks}
    reasons = _provider_failure_reasons(provider_failures)
    rows: list[dict[str, str]] = []
    for ticker in tickers:
        if ticker in returned:
            continue
        reason = reasons.get(ticker, "No usable provider data returned.")
        rows.append(
            {
                "ticker": ticker,
                "failure_reason": reason,
                "provider_membership": "|".join(membership_by_ticker.get(ticker, ())),
                "suggested_action": _suggested_failure_action(reason),
            }
        )
    return tuple(rows)


def _inferred_failure_rows(output_dir: Path, scan: ScanResult) -> tuple[dict[str, str], ...]:
    manager = default_universe_manager(output_dir)
    membership_by_ticker = manager.membership_by_ticker()
    if scan.population == ALL_POPULATION:
        configured = tuple(membership_by_ticker)
    else:
        configured = population_tickers(output_dir, scan.population)
    ranked = {row.get("symbol", "").upper() for row in scan.rows}
    return tuple(
        {
            "ticker": ticker,
            "failure_reason": "No usable provider data returned.",
            "provider_membership": "|".join(membership_by_ticker.get(ticker, ())),
            "suggested_action": "review",
        }
        for ticker in configured
        if ticker not in ranked
    )


def _provider_failure_reasons(provider_failures: tuple[str, ...]) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for item in provider_failures:
        ticker, separator, reason = item.partition(":")
        if separator and ticker.strip():
            reasons[ticker.strip().upper()] = reason.strip()
    return reasons


def _suggested_failure_action(reason: str) -> str:
    lowered = reason.lower()
    if "delist" in lowered or "no price" in lowered or "possibly delisted" in lowered:
        return "remove from seed"
    if "fewer than 252" in lowered:
        return "review"
    if "symbol may be delisted" in lowered or "acquired" in lowered:
        return "replace ticker if known"
    return "review"


def _scan_dir_sort_key(path: Path) -> tuple[str, float]:
    stamp = path.name.rsplit("-", maxsplit=1)[-1]
    if len(stamp) == 14 and stamp.isdigit():
        return (stamp, path.stat().st_mtime)
    return ("", path.stat().st_mtime)


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
