"""Local GreenRock ticker watchlist management."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


DEFAULT_UNIVERSE_NAME = "mega_rock"
MEGA_ROCK_UNIVERSE = "mega_rock"
LARGE_CAP_UNIVERSE = "large_cap"
SMALL_MID_CAP_UNIVERSE = "small_mid_cap"
GREENROCK_UNIVERSE_NAMES = (
    MEGA_ROCK_UNIVERSE,
    LARGE_CAP_UNIVERSE,
    SMALL_MID_CAP_UNIVERSE,
)

MEGA_ROCK_TICKERS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "AVGO",
)

LARGE_CAP_TICKERS: tuple[str, ...] = (
    "JPM",
    "LLY",
    "COST",
    "WMT",
    "XOM",
    "UNH",
    "HD",
    "MA",
    "PG",
    "NFLX",
    "CRM",
    "AMD",
    "ADBE",
    "ORCL",
    "CSCO",
    "BAC",
    "KO",
    "PEP",
    "MCD",
    "DIS",
)

SMALL_MID_CAP_TICKERS: tuple[str, ...] = (
    "SOFI",
    "RKT",
    "AFRM",
    "OPEN",
    "FUBO",
    "CHPT",
    "MARA",
    "RIOT",
    "HOOD",
    "UPST",
    "DKNG",
    "LC",
    "LMND",
    "RUN",
    "STEM",
    "ENVX",
)

DEFAULT_UNIVERSE_TICKERS = {
    MEGA_ROCK_UNIVERSE: MEGA_ROCK_TICKERS,
    LARGE_CAP_UNIVERSE: LARGE_CAP_TICKERS,
    SMALL_MID_CAP_UNIVERSE: SMALL_MID_CAP_TICKERS,
}


@dataclass(frozen=True)
class TickerUniverse:
    name: str
    tickers: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class WatchlistValidation:
    duplicate_tickers: tuple[str, ...]
    probable_bucket_mismatches: tuple[str, ...]
    warnings: tuple[str, ...]


def universe_path(output_dir: Path, name: str = DEFAULT_UNIVERSE_NAME) -> Path:
    return Path(output_dir) / "greenrock" / "universes" / f"{name}.csv"


def load_ticker_universe(output_dir: Path, name: str = DEFAULT_UNIVERSE_NAME) -> TickerUniverse:
    path = universe_path(output_dir, name)
    if not path.exists():
        save_ticker_universe(output_dir, DEFAULT_UNIVERSE_TICKERS.get(name, MEGA_ROCK_TICKERS), name=name)
    tickers: list[str] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            ticker = normalize_ticker(row.get("ticker", ""))
            if ticker:
                tickers.append(ticker)
    return TickerUniverse(name=name, tickers=tuple(dict.fromkeys(tickers)), path=path)


def save_ticker_universe(
    output_dir: Path,
    tickers: tuple[str, ...],
    name: str = DEFAULT_UNIVERSE_NAME,
) -> TickerUniverse:
    path = universe_path(output_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = tuple(dict.fromkeys(normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)))
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["ticker"])
        writer.writeheader()
        for ticker in normalized:
            writer.writerow({"ticker": ticker})
    return TickerUniverse(name=name, tickers=normalized, path=path)


def add_tickers(output_dir: Path, tickers: tuple[str, ...], name: str = DEFAULT_UNIVERSE_NAME) -> TickerUniverse:
    universe = load_ticker_universe(output_dir, name)
    merged = universe.tickers + tuple(normalize_ticker(ticker) for ticker in tickers)
    return save_ticker_universe(output_dir, merged, name=name)


def remove_tickers(output_dir: Path, tickers: tuple[str, ...], name: str = DEFAULT_UNIVERSE_NAME) -> TickerUniverse:
    universe = load_ticker_universe(output_dir, name)
    remove_set = {normalize_ticker(ticker) for ticker in tickers}
    kept = tuple(ticker for ticker in universe.tickers if ticker not in remove_set)
    return save_ticker_universe(output_dir, kept, name=name)


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def load_greenrock_universes(output_dir: Path) -> dict[str, TickerUniverse]:
    return {name: load_ticker_universe(output_dir, name) for name in GREENROCK_UNIVERSE_NAMES}


def reset_universe(output_dir: Path, name: str) -> TickerUniverse:
    return save_ticker_universe(output_dir, DEFAULT_UNIVERSE_TICKERS[name], name=name)


def reset_all_universes(output_dir: Path) -> dict[str, TickerUniverse]:
    return {name: reset_universe(output_dir, name) for name in GREENROCK_UNIVERSE_NAMES}


def validate_watchlists(output_dir: Path) -> WatchlistValidation:
    watchlists = load_greenrock_universes(output_dir)
    ticker_locations: dict[str, list[str]] = {}
    warnings: list[str] = []
    mismatches: list[str] = []
    for name, universe in watchlists.items():
        for ticker in universe.tickers:
            ticker_locations.setdefault(ticker, []).append(name)
            if ticker == "SPCE":
                warnings.append("SPCE is Virgin Galactic, not SpaceX.")

    duplicate_tickers = tuple(
        ticker for ticker, locations in sorted(ticker_locations.items())
        if len(locations) > 1
    )
    for ticker in duplicate_tickers:
        warnings.append(f"{ticker} appears in multiple watchlists: {', '.join(ticker_locations[ticker])}.")

    for ticker in watchlists[MEGA_ROCK_UNIVERSE].tickers:
        if ticker not in MEGA_ROCK_TICKERS:
            mismatches.append(f"{ticker} is in Mega Rock candidate pool but is not in the default $1T+ candidate seed list.")
    for ticker in watchlists[LARGE_CAP_UNIVERSE].tickers:
        if ticker in MEGA_ROCK_TICKERS:
            mismatches.append(f"{ticker} is in Large Cap watchlist but is also a default Mega Rock candidate.")
    for ticker in watchlists[SMALL_MID_CAP_UNIVERSE].tickers:
        if ticker in MEGA_ROCK_TICKERS or ticker in LARGE_CAP_TICKERS:
            mismatches.append(f"{ticker} is in Small/Mid watchlist but appears in a larger-cap default seed list.")

    return WatchlistValidation(
        duplicate_tickers=duplicate_tickers,
        probable_bucket_mismatches=tuple(mismatches),
        warnings=tuple(dict.fromkeys(warnings)),
    )
