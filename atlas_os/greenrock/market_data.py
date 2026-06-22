"""Market data provider abstractions for GreenRock screening."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import date
from typing import Iterable

from atlas_os.greenrock.models import MockStock, PriceBar
from atlas_os.greenrock.sample_data import load_mock_stocks


class MarketDataConfigurationError(RuntimeError):
    """Raised when a requested market data mode is not safely configured."""


class MarketDataProvider(ABC):
    data_mode: str
    source_name: str

    @abstractmethod
    def fetch_stocks(self) -> tuple[MockStock, ...]:
        """Return stock bars shaped for the GreenRock indicator engine."""


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

    def __init__(self, tickers: Iterable[str]) -> None:
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
                "Real mode requires ATLAS_GREENROCK_REAL_TICKERS, for example AAPL,MSFT."
            )

    def fetch_stocks(self) -> tuple[MockStock, ...]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MarketDataConfigurationError(
                "Real mode provider 'yfinance' is configured but the optional yfinance package "
                "is not installed. Install atlas-os with the market-data extra or use --data mock."
            ) from exc

        stocks: list[MockStock] = []
        for ticker in self.tickers:
            instrument = yf.Ticker(ticker)
            history = instrument.history(period="1y", interval="1d", auto_adjust=False)
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

            info = getattr(instrument, "info", {}) or {}
            market_cap = float(info.get("marketCap") or 0)
            company_name = str(info.get("shortName") or info.get("longName") or ticker)
            stocks.append(
                MockStock(
                    symbol=ticker,
                    company_name=company_name,
                    market_cap=market_cap,
                    prices=tuple(bars[-252:]),
                )
            )

        if not stocks:
            raise MarketDataConfigurationError(
                "Real market data request returned no usable 252-day price histories."
            )
        return tuple(stocks)


def get_market_data_provider(data_mode: str) -> MarketDataProvider:
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
    tickers = os.getenv("ATLAS_GREENROCK_REAL_TICKERS", "")
    return YFinanceMarketDataProvider(tickers.split(","))


def _to_date(value) -> date:
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])
