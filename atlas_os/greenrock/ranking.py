"""Ranking helpers for Atlas research candidates."""

from __future__ import annotations


RANKING_FIELDS = (
    "rank",
    "percentile",
    "universe_membership",
)


def rank_candidate_rows(
    rows: tuple[dict[str, str], ...],
    membership_by_ticker: dict[str, tuple[str, ...]] | None = None,
) -> tuple[dict[str, str], ...]:
    total = len(rows)
    ranked: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        ticker = row.get("symbol", "").upper()
        percentile = _percentile(index, total)
        membership = tuple((membership_by_ticker or {}).get(ticker, ()))
        ranked.append(
            dict(row)
            | {
                "rank": str(index),
                "percentile": f"{percentile:.2f}",
                "universe_membership": "|".join(membership),
            }
        )
    return tuple(ranked)


def _percentile(rank: int, total: int) -> float:
    if total <= 1:
        return 100.0 if total == 1 else 0.0
    return round((total - rank) / (total - 1) * 100, 2)
