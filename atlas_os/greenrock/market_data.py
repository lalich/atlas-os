"""Market data provider abstractions for GreenRock screening."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Iterable

from atlas_os.greenrock.models import MockStock, PriceBar
from atlas_os.greenrock.sample_data import load_mock_stocks
from atlas_os.greenrock.universe import load_greenrock_universes


class MarketDataConfigurationError(RuntimeError):
    """Raised when a requested market data mode is not safely configured."""


class MarketDataProvider(ABC):
    data_mode: str
    source_name: str

    @abstractmethod
    def fetch_stocks(self) -> tuple[MockStock, ...]:
        """Return stock bars shaped for the GreenRock indicator engine."""

    def fetch_grouped_stocks(self) -> dict[str, tuple[MockStock, ...]] | None:
        """Return optional section-specific stock bars for GreenRock report sections."""
        return None


class MockMarketDataProvider(MarketDataProvider):
    data_mode = "mock"
    source_name = "mock_sample_data"

    def fetch_stocks(self) -> tuple[MockStock, ...]:
        return load_mock_stocks()


class RealMarketDataProvider(MarketDataProvider):
    data_mode = "real"
    source_name = "real_market_data_placeholder"

    def fetch_stocks(self) -> tuple[MockStock, ...]:
        raise MarketDataConfigurationError(
            "Real market data provider is not configured. Set ATLAS_MARKET_DATA_PROVIDER "
            "and ATLAS_GREENROCK_REAL_TICKERS, or run with --data mock."
        )


class YFinanceMarketDataProvider(RealMarketDataProvider):
    source_name = "yfinance"

    def __init__(self, tickers: Iterable[str], universe_name: str | None = None) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:
            raise MarketDataConfigurationError(
                "Real mode provider 'yfinance' is configured but the optional yfinance package "
                "is not installed. Install atlas-os with the market-data extra or use --data mock."
            ) from exc
        self.tickers = tuple(ticker.strip().upper() for ticker in tickers if ticker.strip())
        if not self.tickers:
            raise MarketDataConfigurationError(
                "Real mode requires tickers from ATLAS_GREENROCK_REAL_TICKERS or the local Mega Rock universe."
            )
        self.universe_name = universe_name
        self.source_name = (
            f"yfinance:{universe_name}" if universe_name else "yfinance:env_tickers"
        )

    def fetch_stocks(self) -> tuple[MockStock, ...]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MarketDataConfigurationError(
                "Real mode provider 'yfinance' is configured but the optional yfinance package "
                "is not installed. Install atlas-os with the market-data extra or use --data mock."
            ) from exc
        if hasattr(yf, "set_tz_cache_location"):
            yf.set_tz_cache_location("/tmp/atlas-yfinance-cache")

        stocks: list[MockStock] = []
        for ticker in self.tickers:
            instrument = yf.Ticker(ticker)
            history = instrument.history(period="18mo", interval="1d", auto_adjust=False)
            if history is None or history.empty:
                continue

            closes = history["Close"].dropna()
            volumes = history["Volume"].fillna(0)
            bars = [
                PriceBar(
                    date=_to_date(index),
                    close=float(close),
                    volume=int(volumes.loc[index]),
                )
                for index, close in closes.items()
            ]
            if len(bars) < 252:
                continue

            try:
                info = getattr(instrument, "info", {}) or {}
            except Exception:
                info = {}
            raw_market_cap = info.get("marketCap")
            market_cap = float(raw_market_cap or 0)
            company_name = str(info.get("shortName") or info.get("longName") or ticker)
            stocks.append(
                MockStock(
                    symbol=ticker,
                    company_name=company_name,
                    market_cap=market_cap,
                    prices=tuple(bars[-252:]),
                    has_price_history=True,
                    has_market_cap=raw_market_cap is not None and market_cap > 0,
                    has_volume_data=any(bar.volume > 0 for bar in bars[-252:]),
                    has_52_week_low=True,
                    skipped_reason="" if raw_market_cap else "missing_market_cap",
                )
            )

        if not stocks:
            raise MarketDataConfigurationError(
                "Real market data request returned no usable 252-day price histories."
            )
        return tuple(stocks)


class SectionedMarketDataProvider(MarketDataProvider):
    """Market data provider wrapper for section-specific GreenRock universes."""

    data_mode = "real"

    def __init__(self, providers: dict[str, MarketDataProvider], source_name: str) -> None:
        self.providers = providers
        self.source_name = source_name

    def fetch_stocks(self) -> tuple[MockStock, ...]:
        grouped = self.fetch_grouped_stocks() or {}
        seen = set()
        stocks: list[MockStock] = []
        for group_stocks in grouped.values():
            for stock in group_stocks:
                if stock.symbol not in seen:
                    stocks.append(stock)
                    seen.add(stock.symbol)
        return tuple(stocks)

    def fetch_grouped_stocks(self) -> dict[str, tuple[MockStock, ...]]:
        return {name: provider.fetch_stocks() for name, provider in self.providers.items()}


def get_market_data_provider(data_mode: str, output_dir: Path | None = None) -> MarketDataProvider:
    normalized_mode = data_mode.strip().lower()
    if normalized_mode == "mock":
        return MockMarketDataProvider()
    if normalized_mode != "real":
        raise MarketDataConfigurationError("Data mode must be 'mock' or 'real'.")

    provider_name = os.getenv("ATLAS_MARKET_DATA_PROVIDER", "").strip().lower()
    if not provider_name:
        raise MarketDataConfigurationError(
            "Real market data provider is not configured. Set ATLAS_MARKET_DATA_PROVIDER "
            "and ATLAS_GREENROCK_REAL_TICKERS, or run with --data mock."
        )
    if provider_name != "yfinance":
        raise MarketDataConfigurationError(
            f"Unsupported market data provider: {provider_name}. Supported provider: yfinance."
        )
    tickers = tuple(
        ticker.strip()
        for ticker in os.getenv("ATLAS_GREENROCK_REAL_TICKERS", "").split(",")
        if ticker.strip()
    )
    if tickers:
        return YFinanceMarketDataProvider(tickers)
    if output_dir is None:
        raise MarketDataConfigurationError(
            "Real mode requires ATLAS_GREENROCK_REAL_TICKERS or an output directory for GreenRock universes."
        )
    universes = load_greenrock_universes(output_dir)
    providers = {
        name: YFinanceMarketDataProvider(universe.tickers, universe_name=universe.name)
        for name, universe in universes.items()
    }
    return SectionedMarketDataProvider(providers, source_name="yfinance:greenrock_universes")


def _to_date(value) -> date:
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])
