"""Local GreenRock screening engine using mock data only."""

from __future__ import annotations

import csv
from pathlib import Path

from atlas_os.greenrock.criteria import evaluate_stock, passes_core_criteria
from atlas_os.greenrock.models import StockCandidate, ScreeningResult
from atlas_os.greenrock.sample_data import SAMPLE_CANDIDATES, load_mock_stocks


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
    "note",
]


def run_sample_screen() -> ScreeningResult:
    if SAMPLE_CANDIDATES:
        selected = tuple(sorted(SAMPLE_CANDIDATES, key=lambda item: item.mock_score, reverse=True))
        return ScreeningResult(selected=selected, all_candidates=selected)
    return run_screen()


def run_screen() -> ScreeningResult:
    all_candidates = tuple(evaluate_stock(stock) for stock in load_mock_stocks())
    eligible = tuple(candidate for candidate in all_candidates if passes_core_criteria(candidate))
    large_cap = _top_by_bucket(eligible, "large_cap", limit=11)
    small_cap = _top_by_bucket(eligible, "small_cap", limit=11)
    selected = large_cap + small_cap
    return ScreeningResult(
        selected=selected,
        all_candidates=all_candidates,
        large_cap=large_cap,
        small_cap=small_cap,
    )


def write_screen_outputs(result: ScreeningResult, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "all": output_dir / "greenrock_candidates.csv",
        "large_cap": output_dir / "greenrock_large_cap.csv",
        "small_cap": output_dir / "greenrock_small_cap.csv",
    }
    write_candidates_csv(result.all_candidates, paths["all"])
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
    return tuple(sorted(bucket_candidates, key=lambda item: item.score, reverse=True)[:limit])


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
        "note": candidate.note,
    }
