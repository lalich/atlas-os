"""GreenRock screening engine."""

from __future__ import annotations

import csv
from pathlib import Path

from atlas_os.greenrock.criteria import evaluate_stock, passes_core_criteria
from atlas_os.greenrock.market_data import MarketDataProvider, MockMarketDataProvider
from atlas_os.greenrock.models import StockCandidate, ScreeningResult
from atlas_os.greenrock.sample_data import SAMPLE_CANDIDATES, load_mock_stocks
from atlas_os.greenrock.score import candidate_evidence_agreement, top_evidence_signal


CSV_HEADERS = [
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
]

MEGA_ROCK_TARGET = 1
SECTION_TARGET = 11
MEGA_ROCK_MARKET_CAP_MIN = 1_000_000_000_000
LARGE_CAP_MARKET_CAP_MIN = 10_000_000_000
LARGE_CAP_MARKET_CAP_MAX = MEGA_ROCK_MARKET_CAP_MIN
SMALL_MID_MARKET_CAP_MAX = LARGE_CAP_MARKET_CAP_MIN


def run_sample_screen() -> ScreeningResult:
    if SAMPLE_CANDIDATES:
        selected = tuple(sorted(SAMPLE_CANDIDATES, key=lambda item: item.mock_score, reverse=True))
        return ScreeningResult(selected=selected, all_candidates=selected, mega_rock=selected[:1], selection_mode="strict")
    return run_screen()


def run_screen(provider: MarketDataProvider | None = None, selection_mode: str | None = None) -> ScreeningResult:
    market_data_provider = provider or MockMarketDataProvider()
    resolved_selection_mode = _resolve_selection_mode(market_data_provider.data_mode, selection_mode)
    grouped_stocks = market_data_provider.fetch_grouped_stocks()
    if grouped_stocks:
        return _run_grouped_screen(market_data_provider, grouped_stocks, resolved_selection_mode)

    all_candidates = tuple(evaluate_stock(stock) for stock in market_data_provider.fetch_stocks())
    eligible = tuple(candidate for candidate in all_candidates if passes_core_criteria(candidate))
    mega_rock = _top_candidates(eligible, MEGA_ROCK_TARGET)
    large_cap = _top_by_bucket(eligible, "large_cap", limit=SECTION_TARGET)
    small_cap = _top_by_bucket(eligible, "small_cap", limit=SECTION_TARGET)
    selected = mega_rock + large_cap + small_cap
    return ScreeningResult(
        selected=selected,
        all_candidates=all_candidates,
        mega_rock=mega_rock,
        large_cap=large_cap,
        small_cap=small_cap,
        data_mode=market_data_provider.data_mode,
        data_source=market_data_provider.source_name,
        selection_mode=resolved_selection_mode,
        data_quality_warnings=_section_warnings(mega_rock, large_cap, small_cap),
    )


def write_screen_outputs(result: ScreeningResult, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "all": output_dir / "greenrock_candidates.csv",
        "mega_rock": output_dir / "greenrock_mega_rock.csv",
        "large_cap": output_dir / "greenrock_large_cap.csv",
        "small_cap": output_dir / "greenrock_small_cap.csv",
    }
    write_candidates_csv(result.all_candidates, paths["all"])
    write_candidates_csv(result.mega_rock, paths["mega_rock"])
    write_candidates_csv(result.large_cap, paths["large_cap"])
    write_candidates_csv(result.small_cap, paths["small_cap"])
    return paths


def write_candidates_csv(candidates: tuple[StockCandidate, ...], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(_candidate_to_row(candidate))


def _top_by_bucket(
    candidates: tuple[StockCandidate, ...],
    bucket: str,
    limit: int,
) -> tuple[StockCandidate, ...]:
    bucket_candidates = [candidate for candidate in candidates if candidate.market_cap_bucket == bucket]
    return _top_candidates(tuple(bucket_candidates), limit)


def _top_candidates(candidates: tuple[StockCandidate, ...], limit: int) -> tuple[StockCandidate, ...]:
    return tuple(sorted(candidates, key=lambda item: item.score, reverse=True)[:limit])


def _run_grouped_screen(
    provider: MarketDataProvider,
    grouped_stocks: dict[str, tuple],
    selection_mode: str,
) -> ScreeningResult:
    grouped_candidates = {
        name: tuple(evaluate_stock(stock) for stock in stocks)
        for name, stocks in grouped_stocks.items()
    }
    mega_rock = _select_section_candidates(
        grouped_candidates.get("mega_rock", ()),
        MEGA_ROCK_TARGET,
        selection_mode,
        minimum_market_cap=MEGA_ROCK_MARKET_CAP_MIN,
    )
    large_cap = _select_section_candidates(
        grouped_candidates.get("large_cap", ()),
        SECTION_TARGET,
        selection_mode,
        minimum_market_cap=LARGE_CAP_MARKET_CAP_MIN,
        maximum_market_cap=LARGE_CAP_MARKET_CAP_MAX,
        excluded_symbols={candidate.symbol for candidate in mega_rock},
    )
    small_cap = _select_section_candidates(
        grouped_candidates.get("small_mid_cap", ()),
        SECTION_TARGET,
        selection_mode,
        maximum_market_cap=SMALL_MID_MARKET_CAP_MAX,
        excluded_symbols={candidate.symbol for candidate in mega_rock + large_cap},
    )
    all_candidates = _dedupe_candidates(
        tuple(candidate for candidates in grouped_candidates.values() for candidate in candidates)
    )
    selected = mega_rock + large_cap + small_cap
    return ScreeningResult(
        selected=selected,
        all_candidates=all_candidates,
        mega_rock=mega_rock,
        large_cap=large_cap,
        small_cap=small_cap,
        data_mode=provider.data_mode,
        data_source=provider.source_name,
        selection_mode=selection_mode,
        data_quality_warnings=_section_warnings(mega_rock, large_cap, small_cap),
    )


def _select_section_candidates(
    candidates: tuple[StockCandidate, ...],
    limit: int,
    selection_mode: str,
    minimum_market_cap: float = 0,
    maximum_market_cap: float | None = None,
    excluded_symbols: set[str] | None = None,
) -> tuple[StockCandidate, ...]:
    excluded_symbols = excluded_symbols or set()
    bucketed = tuple(
        candidate
        for candidate in candidates
        if candidate.symbol not in excluded_symbols
        and candidate.has_market_cap
        and candidate.market_cap >= minimum_market_cap
        and (maximum_market_cap is None or candidate.market_cap < maximum_market_cap)
    )
    if selection_mode == "strict":
        bucketed = tuple(candidate for candidate in bucketed if passes_core_criteria(candidate))
    selected = _top_candidates(bucketed, limit)
    return tuple(_with_selection_label(candidate, selection_mode) for candidate in selected)


def _with_selection_label(candidate: StockCandidate, selection_mode: str) -> StockCandidate:
    if not candidate.failed_rules:
        label = "Strict Pass"
    elif selection_mode == "ranked" and candidate.score >= 55:
        label = "Ranked Candidate"
    else:
        label = "Watchlist"
    return StockCandidate(
        symbol=candidate.symbol,
        company_name=candidate.company_name,
        market_cap_bucket=candidate.market_cap_bucket,
        market_cap=candidate.market_cap,
        score=candidate.score,
        indicators=candidate.indicators,
        passed_rules=candidate.passed_rules,
        failed_rules=candidate.failed_rules,
        note=candidate.note,
        has_price_history=candidate.has_price_history,
        has_market_cap=candidate.has_market_cap,
        has_volume_data=candidate.has_volume_data,
        has_52_week_low=candidate.has_52_week_low,
        skipped_reason=candidate.skipped_reason,
        selection_label=label,
        fundamentals=candidate.fundamentals,
    )


def _resolve_selection_mode(data_mode: str, selection_mode: str | None) -> str:
    if selection_mode in {"strict", "ranked"}:
        return selection_mode
    return "ranked" if data_mode == "real" else "strict"


def _dedupe_candidates(candidates: tuple[StockCandidate, ...]) -> tuple[StockCandidate, ...]:
    deduped: dict[str, StockCandidate] = {}
    for candidate in candidates:
        if candidate.symbol not in deduped or candidate.score > deduped[candidate.symbol].score:
            deduped[candidate.symbol] = candidate
    return tuple(sorted(deduped.values(), key=lambda item: item.score, reverse=True))


def _section_warnings(
    mega_rock: tuple[StockCandidate, ...],
    large_cap: tuple[StockCandidate, ...],
    small_cap: tuple[StockCandidate, ...],
) -> tuple[str, ...]:
    warnings = []
    if len(mega_rock) < MEGA_ROCK_TARGET:
        warnings.append(f"Mega Rock section has {len(mega_rock)}/{MEGA_ROCK_TARGET} picks.")
    if len(large_cap) < SECTION_TARGET:
        warnings.append(f"Large-cap section has {len(large_cap)}/{SECTION_TARGET} picks.")
    if len(small_cap) < SECTION_TARGET:
        warnings.append(f"Small/mid-cap section has {len(small_cap)}/{SECTION_TARGET} picks.")
    return tuple(warnings)


def _candidate_to_row(candidate: StockCandidate) -> dict[str, str | float]:
    indicators = candidate.indicators
    return {
        "symbol": candidate.symbol,
        "company_name": candidate.company_name,
        "market_cap_bucket": candidate.market_cap_bucket,
        "market_cap": round(candidate.market_cap, 2),
        "score": candidate.score,
        "latest_close": indicators.latest_close,
        "rsi_14": round(indicators.rsi_14, 2),
        "low_proximity": round(indicators.low_proximity, 4),
        "volume_avg_10": round(indicators.volume_avg_10, 2),
        "previous_volume_avg_10": round(indicators.previous_volume_avg_10, 2),
        "ema_8": round(indicators.ema_8, 2),
        "sma_10": round(indicators.sma_10, 2),
        "sma_50": round(indicators.sma_50, 2),
        "sma_150": round(indicators.sma_150, 2),
        "ma_roc_50": round(indicators.ma_roc_50, 4),
        "ma_roc_150": round(indicators.ma_roc_150, 4),
        "bollinger_lower": round(indicators.bollinger_lower, 2),
        "bollinger_upper": round(indicators.bollinger_upper, 2),
        "passed_rules": ";".join(candidate.passed_rules),
        "failed_rules": ";".join(candidate.failed_rules),
        "has_price_history": str(candidate.has_price_history),
        "has_market_cap": str(candidate.has_market_cap),
        "has_volume_data": str(candidate.has_volume_data),
        "has_52_week_low": str(candidate.has_52_week_low),
        "skipped_reason": candidate.skipped_reason,
        "selection_label": candidate.selection_label,
        "guardrail": _guardrail_label(candidate),
        "quick_ratio": _candidate_quick_ratio(candidate),
        "net_cash_debt": _candidate_net_cash_debt(candidate),
        "share_change_percent": _candidate_share_change(candidate),
        "evidence_agreement": f"{candidate_evidence_agreement(candidate):.2f}",
        "top_bullish_signal": top_evidence_signal(candidate, "bullish"),
        "top_caution_signal": top_evidence_signal(candidate, "bearish"),
        "note": candidate.note,
    }


def _guardrail_label(candidate: StockCandidate) -> str:
    fundamentals = candidate.fundamentals
    if fundamentals is None:
        return "Insufficient Data"
    quick_ratio = fundamentals.quick_ratio
    net_cash = fundamentals.net_cash
    share_change = fundamentals.shares_outstanding_change_percent
    if quick_ratio is None or net_cash is None or share_change is None:
        return "Insufficient Data"
    if quick_ratio < 0.75 or share_change >= 0.20:
        return "Red Flag"
    if quick_ratio < 1.0 or share_change >= 0.10 or (candidate.market_cap > 0 and net_cash < -candidate.market_cap * 0.25):
        return "Caution"
    if net_cash > 0 and quick_ratio >= 1.5 and share_change <= 0.02:
        return "Strong Balance Sheet"
    return "Acceptable"


def _candidate_quick_ratio(candidate: StockCandidate) -> str:
    if candidate.fundamentals is None or candidate.fundamentals.quick_ratio is None:
        return "unavailable"
    return f"{candidate.fundamentals.quick_ratio:.2f}"


def _candidate_net_cash_debt(candidate: StockCandidate) -> str:
    if candidate.fundamentals is None or candidate.fundamentals.net_cash is None:
        return "unavailable"
    value = candidate.fundamentals.net_cash
    label = "Net Cash" if value >= 0 else "Net Debt"
    return f"{label} ${abs(value) / 1_000_000_000:.2f}B"


def _candidate_share_change(candidate: StockCandidate) -> str:
    if candidate.fundamentals is None or candidate.fundamentals.shares_outstanding_change_percent is None:
        return "unavailable"
    return f"{candidate.fundamentals.shares_outstanding_change_percent:.2%}"
