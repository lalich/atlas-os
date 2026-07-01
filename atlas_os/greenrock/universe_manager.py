"""Atlas research universe management.

The manager is intentionally file-backed and provider-oriented so GreenRock can
use it today while future Atlas divisions can consume the same master universe.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from atlas_os.greenrock.market_engine import classify_market_archetype, expanded_population_tickers
from atlas_os.greenrock.population import (
    ALL_POPULATION,
    GREENROCK_POPULATION_LABELS,
    GREENROCK_POPULATION_NAMES,
    load_population,
    normalize_ticker,
)
from atlas_os.greenrock.universe import GREENROCK_PLACEMENT_LABELS, placement_path


PERSONAL_WATCHLISTS_PROVIDER = "personal_watchlists"
BUCKET_MEGA = "mega"
BUCKET_LARGE = "large"
BUCKET_SMALL_MID = "small_mid"
BUCKET_MICRO = "micro"
BUCKET_UNKNOWN = "unknown"


@dataclass(frozen=True)
class UniverseProviderSnapshot:
    name: str
    tickers: tuple[str, ...]
    source: str
    status: str
    health: str
    last_refresh: str

    @property
    def ticker_count(self) -> int:
        return len(self.tickers)


@dataclass(frozen=True)
class MasterUniverseRow:
    ticker: str
    provider_membership: tuple[str, ...]
    market_cap_bucket: str
    market_archetype: str
    sector: str
    last_refresh: str
    health: str


@dataclass(frozen=True)
class MasterUniverse:
    rows: tuple[MasterUniverseRow, ...]
    providers: tuple[UniverseProviderSnapshot, ...]
    duplicates_removed: int
    last_refresh: str
    path: Path

    @property
    def size(self) -> int:
        return len(self.rows)


class UniverseProvider(Protocol):
    name: str
    source: str

    def refresh(self, output_dir: Path) -> UniverseProviderSnapshot:
        """Return the current provider population."""


class PopulationUniverseProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.source = f"local_population_csv:{name}"

    def refresh(self, output_dir: Path) -> UniverseProviderSnapshot:
        population = load_population(output_dir, self.name)
        return UniverseProviderSnapshot(
            name=self.name,
            tickers=expanded_population_tickers(self.name, population.tickers),
            source=str(population.path),
            status="ready" if population.tickers else "empty",
            health="healthy" if population.tickers else "warning",
            last_refresh=_path_timestamp(population.path),
        )


class PersonalWatchlistsProvider:
    name = PERSONAL_WATCHLISTS_PROVIDER
    source = "local_watchlist_csvs"

    def refresh(self, output_dir: Path) -> UniverseProviderSnapshot:
        tickers: list[str] = []
        paths: list[str] = []
        for list_key in GREENROCK_PLACEMENT_LABELS:
            path = placement_path(output_dir, list_key)
            if not path.exists():
                continue
            paths.append(str(path))
            tickers.extend(_read_tickers(path))
        normalized = tuple(dict.fromkeys(tickers))
        return UniverseProviderSnapshot(
            name=self.name,
            tickers=normalized,
            source=", ".join(paths) if paths else self.source,
            status="ready" if normalized else "empty",
            health="healthy" if normalized else "warning",
            last_refresh=_latest_timestamp(tuple(Path(path) for path in paths)),
        )


class UniverseManager:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.providers: dict[str, UniverseProvider] = {}

    def register_provider(self, provider: UniverseProvider) -> None:
        if not provider.name:
            raise ValueError("Universe provider name is required.")
        self.providers[provider.name] = provider

    def refresh_populations(self) -> tuple[UniverseProviderSnapshot, ...]:
        return tuple(provider.refresh(self.output_dir) for provider in self.providers.values())

    def build_master_universe(self, market_caps: dict[str, float] | None = None, sectors: dict[str, str] | None = None) -> MasterUniverse:
        snapshots = self.refresh_populations()
        memberships: dict[str, list[str]] = {}
        duplicate_entries = 0
        for snapshot in snapshots:
            seen_in_provider: set[str] = set()
            for raw_ticker in snapshot.tickers:
                ticker = normalize_ticker(raw_ticker)
                if not ticker:
                    continue
                if ticker in seen_in_provider:
                    duplicate_entries += 1
                    continue
                seen_in_provider.add(ticker)
                if ticker in memberships:
                    duplicate_entries += 1
                memberships.setdefault(ticker, []).append(snapshot.name)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = tuple(
            MasterUniverseRow(
                ticker=ticker,
                provider_membership=tuple(provider_names),
                market_cap_bucket=classify_market_cap_bucket(
                    market_caps.get(ticker) if market_caps else None,
                    tuple(provider_names),
                ),
                market_archetype=classify_market_archetype(
                    ticker,
                    market_caps.get(ticker) if market_caps else None,
                    tuple(provider_names),
                ),
                sector=(sectors or {}).get(ticker, ""),
                last_refresh=now,
                health=_row_health(tuple(provider_names), snapshots),
            )
            for ticker, provider_names in sorted(memberships.items())
        )
        master = MasterUniverse(
            rows=rows,
            providers=snapshots,
            duplicates_removed=duplicate_entries,
            last_refresh=now,
            path=master_universe_path(self.output_dir),
        )
        save_master_universe(master)
        return master

    def master_universe(self) -> MasterUniverse:
        return self.build_master_universe()

    def tickers_for_population(self, population: str) -> tuple[str, ...]:
        normalized = population.strip().lower()
        if normalized == ALL_POPULATION:
            return tuple(row.ticker for row in self.master_universe().rows)
        if normalized == PERSONAL_WATCHLISTS_PROVIDER:
            snapshot = self.providers[PERSONAL_WATCHLISTS_PROVIDER].refresh(self.output_dir)
            return snapshot.tickers
        if normalized not in self.providers:
            raise ValueError(f"Unknown Atlas research population: {population}")
        return self.providers[normalized].refresh(self.output_dir).tickers

    def membership_by_ticker(self) -> dict[str, tuple[str, ...]]:
        master = self.master_universe()
        return {row.ticker: row.provider_membership for row in master.rows}


def default_universe_manager(output_dir: Path) -> UniverseManager:
    manager = UniverseManager(output_dir)
    for name in GREENROCK_POPULATION_NAMES:
        manager.register_provider(PopulationUniverseProvider(name))
    manager.register_provider(PersonalWatchlistsProvider())
    return manager


def master_universe_path(output_dir: Path) -> Path:
    return Path(output_dir) / "atlas" / "research" / "master_universe.csv"


def load_master_universe(output_dir: Path) -> MasterUniverse:
    path = master_universe_path(output_dir)
    if not path.exists():
        return default_universe_manager(output_dir).master_universe()
    rows: list[MasterUniverseRow] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            rows.append(
                MasterUniverseRow(
                    ticker=normalize_ticker(row.get("ticker", "")),
                    provider_membership=tuple(
                        item for item in row.get("provider_membership", "").split("|") if item
                    ),
                    market_cap_bucket=row.get("market_cap_bucket", BUCKET_UNKNOWN),
                    market_archetype=row.get("market_archetype", ""),
                    sector=row.get("sector", ""),
                    last_refresh=row.get("last_refresh", ""),
                    health=row.get("health", "unknown"),
                )
            )
    last_refresh = rows[0].last_refresh if rows else ""
    return MasterUniverse(
        rows=tuple(row for row in rows if row.ticker),
        providers=(),
        duplicates_removed=0,
        last_refresh=last_refresh,
        path=path,
    )


def save_master_universe(master: MasterUniverse) -> None:
    master.path.parent.mkdir(parents=True, exist_ok=True)
    with master.path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "ticker",
                "provider_membership",
                "market_cap_bucket",
                "market_archetype",
                "sector",
                "last_refresh",
                "health",
            ],
        )
        writer.writeheader()
        for row in master.rows:
            writer.writerow(
                {
                    "ticker": row.ticker,
                    "provider_membership": "|".join(row.provider_membership),
                    "market_cap_bucket": row.market_cap_bucket,
                    "market_archetype": row.market_archetype,
                    "sector": row.sector,
                    "last_refresh": row.last_refresh,
                    "health": row.health,
                }
            )


def classify_market_cap_bucket(market_cap: float | None, memberships: tuple[str, ...] = ()) -> str:
    if market_cap is not None and market_cap > 0:
        if market_cap >= 1_000_000_000_000:
            return BUCKET_MEGA
        if market_cap >= 10_000_000_000:
            return BUCKET_LARGE
        if market_cap >= 300_000_000:
            return BUCKET_SMALL_MID
        return BUCKET_MICRO
    if "qqq" in memberships or "sp500" in memberships:
        return BUCKET_LARGE
    if "russell2000" in memberships:
        return BUCKET_SMALL_MID
    if "micro_moonshot" in memberships:
        return BUCKET_MICRO
    return BUCKET_UNKNOWN


def provider_label(name: str) -> str:
    if name == PERSONAL_WATCHLISTS_PROVIDER:
        return "Personal Watchlists"
    return GREENROCK_POPULATION_LABELS.get(name, name)


def _read_tickers(path: Path) -> tuple[str, ...]:
    tickers: list[str] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            ticker = normalize_ticker(row.get("ticker", ""))
            if ticker:
                tickers.append(ticker)
    return tuple(tickers)


def _path_timestamp(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _latest_timestamp(paths: tuple[Path, ...]) -> str:
    timestamps = tuple(_path_timestamp(path) for path in paths if path.exists())
    return max(timestamps) if timestamps else ""


def _row_health(provider_names: tuple[str, ...], snapshots: tuple[UniverseProviderSnapshot, ...]) -> str:
    health_by_provider = {snapshot.name: snapshot.health for snapshot in snapshots}
    if any(health_by_provider.get(name) == "healthy" for name in provider_names):
        return "healthy"
    return "warning"
