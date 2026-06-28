"""Mock GreenRock sample data for local screening."""

from __future__ import annotations

from datetime import date, timedelta

from atlas_os.greenrock.models import FundamentalSnapshot, MockStock, PriceBar, StockCandidate


SAMPLE_CANDIDATES: tuple[StockCandidate, ...] = ()


def load_mock_stocks() -> tuple[MockStock, ...]:
    large_caps = tuple(
        _build_mock_stock(
            symbol=f"LC{index:02d}",
            company_name=f"Large Cap Mock {index:02d}",
            market_cap=(6 + index) * 1_000_000_000,
            start_price=120 + index,
            drift=0.31 + index * 0.003,
            base_volume=1_200_000 + index * 45_000,
            wiggle=index % 4,
        )
        for index in range(1, 15)
    )
    small_caps = tuple(
        _build_mock_stock(
            symbol=f"SC{index:02d}",
            company_name=f"Small Cap Mock {index:02d}",
            market_cap=(0.7 + index * 0.22) * 1_000_000_000,
            start_price=48 + index,
            drift=0.13 + index * 0.002,
            base_volume=350_000 + index * 20_000,
            wiggle=(index + 2) % 4,
        )
        for index in range(1, 15)
    )
    watchlist_noise = (
        _build_mock_stock(
            symbol="NOISE",
            company_name="Noise Control Sample",
            market_cap=8_500_000_000,
            start_price=70,
            drift=-0.03,
            base_volume=900_000,
            wiggle=1,
            force_fail=True,
        ),
    )
    return large_caps + small_caps + watchlist_noise


def _build_mock_stock(
    symbol: str,
    company_name: str,
    market_cap: float,
    start_price: float,
    drift: float,
    base_volume: int,
    wiggle: int,
    force_fail: bool = False,
) -> MockStock:
    today = date.today()
    prices: list[PriceBar] = []
    price = start_price

    for day in range(252):
        days_ago = 251 - day
        current_date = today - timedelta(days=days_ago)

        if force_fail:
            price = start_price + day * 0.05 + ((day + wiggle) % 7) * 0.08
        elif day < 170:
            price = start_price - day * drift
        elif day < 222:
            price = start_price - 170 * drift - (day - 170) * drift * 0.28
        else:
            price = start_price - 170 * drift - 52 * drift * 0.28 - (day - 222) * drift * 0.08

        price += ((day + wiggle) % 5 - 2) * 0.03

        if not force_fail and day >= 238:
            price -= (day - 237) * 0.045

        volume = base_volume + (day % 11) * 4000
        if not force_fail and day >= 242:
            volume += (day - 241) * int(base_volume * 0.035)

        prices.append(
            PriceBar(
                date=current_date,
                close=round(max(price, 2.0), 2),
                volume=int(volume),
            )
        )

    return MockStock(
        symbol=symbol,
        company_name=company_name,
        market_cap=market_cap,
        prices=tuple(prices),
        fundamentals=FundamentalSnapshot(
            cash_and_equivalents=market_cap * 0.08,
            total_debt=market_cap * 0.03,
            net_cash=market_cap * 0.05,
            net_cash_per_share=2.50,
            quick_ratio=1.6 if not force_fail else 0.95,
            current_assets=market_cap * 0.12,
            inventory=market_cap * 0.02,
            current_liabilities=market_cap * 0.0625,
            shares_outstanding_current=100_000_000,
            shares_outstanding_prior=99_500_000 if not force_fail else 88_000_000,
            shares_outstanding_change_percent=0.005 if not force_fail else 0.1364,
            fundamental_data_source="mock_sample_data",
            fundamental_data_warnings=(),
        ),
    )
