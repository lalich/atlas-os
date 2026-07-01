"""GreenRock population universe storage."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from atlas_os.greenrock.market_engine import expanded_population_tickers


QQQ_POPULATION = "qqq"
SP500_POPULATION = "sp500"
RUSSELL2000_POPULATION = "russell2000"
MICRO_MOONSHOT_POPULATION = "micro_moonshot"
ALL_POPULATION = "all"

GREENROCK_POPULATION_NAMES = (
    QQQ_POPULATION,
    SP500_POPULATION,
    RUSSELL2000_POPULATION,
    MICRO_MOONSHOT_POPULATION,
)

GREENROCK_POPULATION_LABELS = {
    QQQ_POPULATION: "Nasdaq 100 / QQQ",
    SP500_POPULATION: "S&P 500",
    RUSSELL2000_POPULATION: "Russell 2000",
    MICRO_MOONSHOT_POPULATION: "GreenRock Micro/Moonshot",
}

QQQ_TICKERS = (
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "AVGO", "GOOGL", "GOOG", "TSLA", "COST",
    "NFLX", "AMD", "PEP", "ADBE", "CSCO", "TMUS", "INTU", "QCOM", "TXN", "AMAT",
)
SP500_TICKERS = (
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "BRK-B", "LLY", "JPM", "AVGO",
    "XOM", "UNH", "V", "PG", "MA", "COST", "HD", "WMT", "NFLX", "BAC",
)
RUSSELL2000_TICKERS = (
    "SMCI", "CELH", "ELF", "MARA", "RIOT", "UPST", "FUBO", "CHPT", "RUN", "ENVX",
    "STEM", "LC", "LMND", "OPEN", "RKT", "SOFI", "AFRM", "DKNG", "HOOD", "NIO",
)
MICRO_MOONSHOT_TICKERS = (
    "GRRR", "PI", "ENPH", "NIO", "SOFI", "RKT", "AFRM", "OPEN", "FUBO", "CHPT",
    "MARA", "RIOT", "HOOD", "UPST", "DKNG", "LC", "LMND", "RUN", "STEM", "ENVX",
)

DEFAULT_POPULATION_TICKERS = {
    QQQ_POPULATION: QQQ_TICKERS,
    SP500_POPULATION: SP500_TICKERS,
    RUSSELL2000_POPULATION: RUSSELL2000_TICKERS,
    MICRO_MOONSHOT_POPULATION: MICRO_MOONSHOT_TICKERS,
}


@dataclass(frozen=True)
class PopulationUniverse:
    name: str
    label: str
    tickers: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class PopulationValidation:
    duplicate_tickers: tuple[str, ...]
    warnings: tuple[str, ...]


def population_path(output_dir: Path, name: str) -> Path:
    _ensure_valid_population(name)
    return Path(output_dir) / "greenrock" / "populations" / f"{name}.csv"


def load_population(output_dir: Path, name: str) -> PopulationUniverse:
    _ensure_valid_population(name)
    path = population_path(output_dir, name)
    if not path.exists():
        reset_population(output_dir, name)
    tickers: list[str] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            ticker = normalize_ticker(row.get("ticker", ""))
            if ticker:
                tickers.append(ticker)
    return PopulationUniverse(
        name=name,
        label=GREENROCK_POPULATION_LABELS[name],
        tickers=tuple(dict.fromkeys(tickers)),
        path=path,
    )


def load_populations(output_dir: Path) -> dict[str, PopulationUniverse]:
    return {name: load_population(output_dir, name) for name in GREENROCK_POPULATION_NAMES}


def save_population(output_dir: Path, name: str, tickers: tuple[str, ...]) -> PopulationUniverse:
    _ensure_valid_population(name)
    path = population_path(output_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = tuple(dict.fromkeys(normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)))
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["ticker"])
        writer.writeheader()
        for ticker in normalized:
            writer.writerow({"ticker": ticker})
    return PopulationUniverse(
        name=name,
        label=GREENROCK_POPULATION_LABELS[name],
        tickers=normalized,
        path=path,
    )


def reset_population(output_dir: Path, name: str) -> PopulationUniverse:
    return save_population(output_dir, name, DEFAULT_POPULATION_TICKERS[name])


def reset_all_populations(output_dir: Path) -> dict[str, PopulationUniverse]:
    return {name: reset_population(output_dir, name) for name in GREENROCK_POPULATION_NAMES}


def add_population_tickers(output_dir: Path, name: str, tickers: tuple[str, ...]) -> PopulationUniverse:
    population = load_population(output_dir, name)
    return save_population(output_dir, name, population.tickers + tickers)


def remove_population_tickers(output_dir: Path, name: str, tickers: tuple[str, ...]) -> PopulationUniverse:
    population = load_population(output_dir, name)
    remove_set = {normalize_ticker(ticker) for ticker in tickers}
    return save_population(output_dir, name, tuple(ticker for ticker in population.tickers if ticker not in remove_set))


def population_tickers(output_dir: Path, name: str) -> tuple[str, ...]:
    normalized = name.strip().lower()
    if normalized == ALL_POPULATION:
        tickers: list[str] = []
        for population in load_populations(output_dir).values():
            tickers.extend(expanded_population_tickers(population.name, population.tickers))
        return tuple(dict.fromkeys(tickers))
    population = load_population(output_dir, normalized)
    return expanded_population_tickers(population.name, population.tickers)


def validate_populations(output_dir: Path) -> PopulationValidation:
    populations = load_populations(output_dir)
    locations: dict[str, list[str]] = {}
    warnings: list[str] = []
    for name, population in populations.items():
        for ticker in population.tickers:
            locations.setdefault(ticker, []).append(name)
    duplicates = tuple(ticker for ticker, names in sorted(locations.items()) if len(names) > 1)
    for ticker in duplicates:
        warnings.append(f"{ticker} appears in multiple populations: {', '.join(locations[ticker])}.")
    for ticker in MICRO_MOONSHOT_TICKERS:
        if ticker not in populations[MICRO_MOONSHOT_POPULATION].tickers:
            warnings.append(f"{ticker} is missing from the editable Micro/Moonshot population.")
    return PopulationValidation(duplicate_tickers=duplicates, warnings=tuple(warnings))


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _ensure_valid_population(name: str) -> None:
    if name not in GREENROCK_POPULATION_NAMES:
        raise ValueError(f"Unknown GreenRock population: {name}")
