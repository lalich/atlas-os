"""Research-only GreenRock derivatives workbench services."""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from atlas_os.greenrock.indicators import (
    average_volume_trend,
    bollinger_bands,
    moving_average_rate_of_change,
    relative_strength_index,
    simple_moving_average,
    week_52_low_proximity,
)
from atlas_os.greenrock.market_data import MarketDataConfigurationError, MarketDataProvider, YFinanceMarketDataProvider
from atlas_os.greenrock.models import MockStock
from atlas_os.greenrock.staging import load_staged_candidates


DEFAULT_RISK_FREE_RATE = 0.04
DEFAULT_BINOMIAL_STEPS = 120
TARGET_DTES = (30, 60, 90)
SNAPSHOT_HEADERS = [
    "contract_symbol",
    "option_type",
    "expiration",
    "strike",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "implied_volatility",
]


@dataclass(frozen=True)
class OptionContract:
    contract_symbol: str
    option_type: str
    expiration: str
    strike: float
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None

    @property
    def premium(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        if self.last is not None and self.last > 0:
            return self.last
        if self.ask is not None and self.ask > 0:
            return self.ask
        if self.bid is not None and self.bid > 0:
            return self.bid
        return None


@dataclass(frozen=True)
class OptionsChainSnapshot:
    ticker: str
    provider: str
    underlying_price: float | None
    price_history: tuple[float, ...]
    volume_history: tuple[int, ...]
    expirations: tuple[str, ...]
    calls: tuple[OptionContract, ...]
    puts: tuple[OptionContract, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExpirationWindow:
    target_dte: int
    expiration: str
    actual_dte: int
    difference: int
    warning: str = ""


@dataclass(frozen=True)
class BinomialResult:
    model_used: str
    model_reason: str
    theoretical_value: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    early_exercise: bool
    intrinsic_value: float
    extrinsic_value: float
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    model_status: str = "available"


@dataclass(frozen=True)
class TimingScore:
    score: float
    components: dict[str, float]
    contributions: dict[str, float]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ContractResearch:
    contract: OptionContract
    score: float
    factors: dict[str, float]
    warnings: tuple[str, ...]
    model: BinomialResult
    breakeven: float


@dataclass(frozen=True)
class ScenarioPoint:
    underlying_price: float
    valuation_date: str
    theoretical_value: float
    dollar_pl: float
    percent_pl: float


@dataclass(frozen=True)
class DerivativeAnalysis:
    ticker: str
    snapshot_id: str
    created_at: str
    provider: str
    underlying_price: float | None
    windows: tuple[ExpirationWindow, ...]
    chain_quality: dict[str, float | int | str]
    timing_score: TimingScore
    top_calls: dict[str, tuple[ContractResearch, ...]]
    top_puts: dict[str, tuple[ContractResearch, ...]]
    scenario_grid: tuple[ScenarioPoint, ...]
    agent_updates: dict[str, str]
    warnings: tuple[str, ...]
    snapshot_path: str


class OptionsDataProvider:
    source_name = "options_provider"

    def fetch_snapshot(self, ticker: str) -> OptionsChainSnapshot:
        raise NotImplementedError


class YFinanceOptionsProvider(OptionsDataProvider):
    source_name = "yfinance:options"

    def __init__(self, ticker: str) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:
            raise MarketDataConfigurationError(
                "Options provider 'yfinance' is configured but the optional yfinance package is not installed. "
                "Install atlas-os with the market-data extra."
            ) from exc
        self.ticker = ticker.strip().upper()

    def fetch_snapshot(self, ticker: str) -> OptionsChainSnapshot:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MarketDataConfigurationError(
                "Options provider 'yfinance' is configured but the optional yfinance package is not installed."
            ) from exc
        instrument = yf.Ticker(ticker.strip().upper())
        history = instrument.history(period="1y", interval="1d", auto_adjust=False)
        price_history = tuple(float(value) for value in history["Close"].dropna().tolist()) if history is not None and not history.empty else ()
        volume_history = tuple(int(value) for value in history["Volume"].fillna(0).tolist()) if history is not None and not history.empty else ()
        underlying_price = price_history[-1] if price_history else None
        expirations = tuple(str(item) for item in getattr(instrument, "options", ()) or ())
        calls: list[OptionContract] = []
        puts: list[OptionContract] = []
        for expiration in expirations:
            chain = instrument.option_chain(expiration)
            calls.extend(_contracts_from_frame(chain.calls, "call", expiration))
            puts.extend(_contracts_from_frame(chain.puts, "put", expiration))
        return OptionsChainSnapshot(
            ticker=ticker.strip().upper(),
            provider=self.source_name,
            underlying_price=underlying_price,
            price_history=price_history,
            volume_history=volume_history,
            expirations=expirations,
            calls=tuple(calls),
            puts=tuple(puts),
        )


def options_provider_configured() -> bool:
    return os.getenv("ATLAS_MARKET_DATA_PROVIDER", "").strip().lower() == "yfinance"


def provider_diagnostics(ticker: str, provider: OptionsDataProvider | None = None) -> dict[str, str | bool | int]:
    diagnostics: dict[str, str | bool | int] = {
        "ticker": ticker.strip().upper(),
        "real_provider_configured": options_provider_configured(),
        "provider": os.getenv("ATLAS_MARKET_DATA_PROVIDER", "").strip().lower() or "missing",
        "underlying_price_available": False,
        "expirations_available": False,
        "calls_available": False,
        "puts_available": False,
        "bid_available": False,
        "ask_available": False,
        "last_available": False,
        "volume_available": False,
        "open_interest_available": False,
        "implied_volatility_available": False,
        "contract_symbol_available": False,
        "strike_available": False,
        "expiration_available": False,
        "status": "blocked",
        "message": "",
    }
    if provider is None and not diagnostics["real_provider_configured"]:
        diagnostics["message"] = "Set ATLAS_MARKET_DATA_PROVIDER=yfinance and install market-data extras."
        return diagnostics
    try:
        snapshot = (provider or YFinanceOptionsProvider(ticker)).fetch_snapshot(ticker)
    except (MarketDataConfigurationError, ValueError) as error:
        diagnostics["message"] = str(error)
        return diagnostics
    contracts = snapshot.calls + snapshot.puts
    diagnostics |= {
        "provider": snapshot.provider,
        "underlying_price_available": snapshot.underlying_price is not None,
        "expirations_available": bool(snapshot.expirations),
        "calls_available": bool(snapshot.calls),
        "puts_available": bool(snapshot.puts),
        "bid_available": any(contract.bid is not None for contract in contracts),
        "ask_available": any(contract.ask is not None for contract in contracts),
        "last_available": any(contract.last is not None for contract in contracts),
        "volume_available": any(contract.volume is not None for contract in contracts),
        "open_interest_available": any(contract.open_interest is not None for contract in contracts),
        "implied_volatility_available": any(contract.implied_volatility is not None for contract in contracts),
        "contract_symbol_available": any(bool(contract.contract_symbol) for contract in contracts),
        "strike_available": any(contract.strike > 0 for contract in contracts),
        "expiration_available": bool(snapshot.expirations),
        "status": "ready" if snapshot.underlying_price and snapshot.expirations and snapshot.calls and snapshot.puts else "blocked",
        "message": "Options chain fields inspected locally.",
    }
    return diagnostics


def create_options_snapshot(
    output_dir: Path,
    ticker: str,
    provider: OptionsDataProvider | None = None,
) -> DerivativeAnalysis:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")
    if provider is None and not options_provider_configured():
        raise MarketDataConfigurationError(
            "Options analysis requires ATLAS_MARKET_DATA_PROVIDER=yfinance. "
            "No staging, reports, approvals, PDFs, email, publishing, trading, or client files were changed."
        )
    snapshot = (provider or YFinanceOptionsProvider(normalized)).fetch_snapshot(normalized)
    return analyze_snapshot(output_dir, snapshot)


def analyze_snapshot(output_dir: Path, snapshot: OptionsChainSnapshot) -> DerivativeAnalysis:
    if snapshot.underlying_price is None or snapshot.underlying_price <= 0:
        raise ValueError("Underlying price is unavailable for options analysis.")
    if not snapshot.expirations:
        raise ValueError("No listed expirations were available for this ticker.")
    if not snapshot.calls and not snapshot.puts:
        raise ValueError("No calls or puts were available for this ticker.")
    snapshot_id = f"deriv-{snapshot.ticker.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    windows = select_expiration_windows(snapshot.expirations)
    timing = derivative_timing_score(snapshot.price_history, snapshot.volume_history)
    top_calls: dict[str, tuple[ContractResearch, ...]] = {}
    top_puts: dict[str, tuple[ContractResearch, ...]] = {}
    warnings = list(snapshot.warnings)
    for window in windows:
        calls = tuple(contract for contract in snapshot.calls if contract.expiration == window.expiration)
        puts = tuple(contract for contract in snapshot.puts if contract.expiration == window.expiration)
        top_calls[str(window.target_dte)] = rank_contracts(calls, "call", snapshot.underlying_price, window.actual_dte, timing)[:5]
        top_puts[str(window.target_dte)] = rank_contracts(puts, "put", snapshot.underlying_price, window.actual_dte, timing)[:5]
        if window.warning:
            warnings.append(window.warning)
    scenario_source = _first_contract(top_calls, top_puts)
    scenario_grid = ()
    if scenario_source:
        scenario_grid = scenario_analysis(scenario_source.contract, snapshot.underlying_price, scenario_source.contract.premium or 0, window_dte(scenario_source.contract.expiration))
    chain_quality = chain_quality_summary(snapshot)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot_dir = derivatives_snapshot_dir(output_dir, snapshot.ticker, snapshot_id)
    analysis = DerivativeAnalysis(
        ticker=snapshot.ticker,
        snapshot_id=snapshot_id,
        created_at=created_at,
        provider=snapshot.provider,
        underlying_price=snapshot.underlying_price,
        windows=windows,
        chain_quality=chain_quality,
        timing_score=timing,
        top_calls=top_calls,
        top_puts=top_puts,
        scenario_grid=scenario_grid,
        agent_updates=agent_updates(snapshot, timing, chain_quality),
        warnings=tuple(warnings),
        snapshot_path=str(snapshot_dir),
    )
    persist_derivative_snapshot(output_dir, snapshot, analysis)
    return analysis


def analyze_staged(output_dir: Path, provider_factory=None) -> tuple[DerivativeAnalysis, ...]:
    analyses: list[DerivativeAnalysis] = []
    for row in load_staged_candidates(output_dir):
        ticker = row.get("ticker", "").strip().upper()
        if not ticker:
            continue
        provider = provider_factory(ticker) if provider_factory else None
        try:
            analyses.append(create_options_snapshot(output_dir, ticker, provider=provider))
        except (MarketDataConfigurationError, ValueError):
            continue
    return tuple(analyses)


def select_expiration_windows(expirations: tuple[str, ...], today: date | None = None) -> tuple[ExpirationWindow, ...]:
    base = today or date.today()
    parsed = sorted((date.fromisoformat(item), item) for item in expirations if _valid_date(item))
    if not parsed:
        raise ValueError("No valid expiration dates were available.")
    windows: list[ExpirationWindow] = []
    for target in TARGET_DTES:
        selected_date, selected_raw = min(parsed, key=lambda item: abs((item[0] - base).days - target))
        actual = max(0, (selected_date - base).days)
        diff = actual - target
        warning = f"{target}D target selected {abs(diff)} day(s) away from target." if abs(diff) > 10 else ""
        windows.append(ExpirationWindow(target, selected_raw, actual, diff, warning))
    return tuple(windows)


def price_american_binomial(
    option_type: str,
    underlying: float,
    strike: float,
    dte: float,
    volatility: float | None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = 0.0,
    steps: int = DEFAULT_BINOMIAL_STEPS,
) -> BinomialResult:
    normalized_type = option_type.strip().lower()
    warnings: list[str] = []
    assumptions = [
        "American-style equity option priced with Cox-Ross-Rubinstein binomial tree.",
        f"Risk-free rate {risk_free_rate:.2%}.",
        f"Dividend yield {dividend_yield:.2%}.",
    ]
    if normalized_type not in {"call", "put"}:
        raise ValueError("Option type must be call or put.")
    if underlying <= 0:
        raise ValueError("Underlying price must be positive.")
    if strike <= 0:
        raise ValueError("Strike must be positive.")
    intrinsic = _intrinsic(normalized_type, underlying, strike)
    if dte <= 0:
        return BinomialResult(
            "american_binomial",
            "At expiration, option value equals intrinsic value.",
            intrinsic,
            _expiration_delta(normalized_type, underlying, strike),
            0.0,
            0.0,
            0.0,
            0.0,
            False,
            intrinsic,
            0.0,
            tuple(assumptions),
            ("Near-zero DTE: model sensitivity is high.",),
        )
    if volatility is None or volatility <= 0:
        warnings.append("Missing or invalid implied volatility; model unavailable.")
        return BinomialResult(
            "american_binomial",
            "Listed U.S. equity options default to American binomial, but volatility is required.",
            intrinsic,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            False,
            intrinsic,
            0.0,
            tuple(assumptions),
            tuple(warnings),
            model_status="unavailable",
        )
    years = max(dte / 365.0, 1 / 36500)
    clean_steps = max(3, min(int(steps), 600))
    if dte < 3:
        warnings.append("Very short DTE: binomial outputs are highly sensitive.")
        clean_steps = max(clean_steps, 200)
    value, early = _binomial_value(normalized_type, underlying, strike, years, volatility, risk_free_rate, dividend_yield, clean_steps)
    delta = _finite_delta(normalized_type, underlying, strike, years, volatility, risk_free_rate, dividend_yield, clean_steps)
    gamma = _finite_gamma(normalized_type, underlying, strike, years, volatility, risk_free_rate, dividend_yield, clean_steps)
    theta = _finite_theta(normalized_type, underlying, strike, years, volatility, risk_free_rate, dividend_yield, clean_steps)
    vega = _finite_vol(normalized_type, underlying, strike, years, volatility, risk_free_rate, dividend_yield, clean_steps)
    rho = _finite_rate(normalized_type, underlying, strike, years, volatility, risk_free_rate, dividend_yield, clean_steps)
    return BinomialResult(
        "american_binomial",
        "Listed U.S. equity options default to American-style binomial pricing.",
        round(value, 4),
        round(delta, 4),
        round(gamma, 4),
        round(theta, 4),
        round(vega, 4),
        round(rho, 4),
        early,
        round(intrinsic, 4),
        round(max(0.0, value - intrinsic), 4),
        tuple(assumptions),
        tuple(warnings),
    )


def derivative_timing_score(prices: tuple[float, ...], volumes: tuple[int, ...]) -> TimingScore:
    warnings: list[str] = []
    if len(prices) < 60:
        warnings.append("Limited price history; timing score uses conservative defaults.")
    components = {
        "moving_average_structure": _score_ma(prices),
        "bollinger_setup": _score_bollinger(prices),
        "volume_acceleration": _score_volume(volumes),
        "rsi_momentum": _score_rsi(prices),
        "short_term_trend": _score_trend(prices),
        "low_proximity": _score_low_proximity(prices),
    }
    weights = {
        "moving_average_structure": 0.24,
        "bollinger_setup": 0.22,
        "volume_acceleration": 0.20,
        "rsi_momentum": 0.16,
        "short_term_trend": 0.11,
        "low_proximity": 0.07,
    }
    contributions = {key: components[key] * weights[key] for key in components}
    return TimingScore(round(sum(contributions.values()), 2), components, contributions, tuple(warnings))


def rank_contracts(
    contracts: tuple[OptionContract, ...],
    option_type: str,
    underlying: float,
    dte: int,
    timing: TimingScore,
) -> tuple[ContractResearch, ...]:
    ranked: list[ContractResearch] = []
    for contract in contracts:
        premium = contract.premium
        iv = contract.implied_volatility
        warnings: list[str] = []
        if premium is None:
            warnings.append("Missing usable premium.")
        model = price_american_binomial(option_type, underlying, contract.strike, dte, iv)
        breakeven = contract.strike + (premium or 0) if option_type == "call" else contract.strike - (premium or 0)
        factors = contract_score_factors(contract, option_type, underlying, premium or 0, model, timing)
        score = round(sum(factors.values()) / len(factors), 2)
        ranked.append(ContractResearch(contract, score, factors, tuple(warnings + list(model.warnings)), model, round(breakeven, 4)))
    ranked.sort(key=lambda item: item.score, reverse=True)
    return tuple(ranked)


def contract_score_factors(
    contract: OptionContract,
    option_type: str,
    underlying: float,
    premium: float,
    model: BinomialResult,
    timing: TimingScore,
) -> dict[str, float]:
    spread = _spread_pct(contract)
    liquidity = min(100.0, ((contract.open_interest or 0) / 10) + ((contract.volume or 0) / 2))
    spread_score = max(0.0, 100.0 - spread * 4)
    delta_score = max(0.0, 100.0 - abs(abs(model.delta) - 0.45) * 180)
    theta_score = max(0.0, 100.0 + min(0.0, model.theta) * 18)
    iv_score = 55.0 if contract.implied_volatility is None else max(0.0, 100.0 - abs(contract.implied_volatility - 0.45) * 110)
    breakeven = contract.strike + premium if option_type == "call" else contract.strike - premium
    distance = abs(breakeven - underlying) / underlying if underlying else 1
    return {
        "liquidity": round(liquidity, 2),
        "spread": round(spread_score, 2),
        "delta_range": round(delta_score, 2),
        "theta_burden": round(theta_score, 2),
        "iv_condition": round(iv_score, 2),
        "breakeven_distance": round(max(0.0, 100.0 - distance * 220), 2),
        "timing_alignment": timing.score,
        "scenario_behavior": round(max(0.0, min(100.0, model.extrinsic_value * 8 + timing.score * 0.35)), 2),
    }


def scenario_analysis(
    contract: OptionContract,
    underlying: float,
    premium: float,
    dte: int,
    contracts: int = 1,
) -> tuple[ScenarioPoint, ...]:
    points: list[ScenarioPoint] = []
    today = date.today()
    iv = contract.implied_volatility or 0.0
    for pct in (-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20):
        price = underlying * (1 + pct)
        for fraction in (0.0, 1 / 3, 2 / 3, 1.0):
            remaining = max(0, int(dte * (1 - fraction)))
            valuation_date = today + timedelta(days=int(dte * fraction))
            model = price_american_binomial(contract.option_type, price, contract.strike, remaining, iv)
            dollar = (model.theoretical_value - premium) * 100 * max(1, contracts)
            percent = (model.theoretical_value - premium) / premium * 100 if premium else 0.0
            points.append(ScenarioPoint(round(price, 4), valuation_date.isoformat(), round(model.theoretical_value, 4), round(dollar, 2), round(percent, 2)))
    return tuple(points)


def chain_quality_summary(snapshot: OptionsChainSnapshot) -> dict[str, float | int | str]:
    contracts = snapshot.calls + snapshot.puts
    with_premium = [contract for contract in contracts if contract.premium is not None]
    wide = [contract for contract in contracts if _spread_pct(contract) > 25]
    return {
        "expiration_count": len(snapshot.expirations),
        "call_count": len(snapshot.calls),
        "put_count": len(snapshot.puts),
        "premium_coverage_pct": round(len(with_premium) / len(contracts) * 100, 2) if contracts else 0.0,
        "wide_spread_count": len(wide),
        "status": "healthy" if contracts and len(wide) < max(2, len(contracts) // 2) else "warning",
    }


def persist_derivative_snapshot(output_dir: Path, snapshot: OptionsChainSnapshot, analysis: DerivativeAnalysis) -> None:
    snapshot_dir = derivatives_snapshot_dir(output_dir, snapshot.ticker, analysis.snapshot_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    _write_json(snapshot_dir / "metadata.json", {
        "ticker": snapshot.ticker,
        "snapshot_id": analysis.snapshot_id,
        "created_at": analysis.created_at,
        "provider": snapshot.provider,
        "underlying_price": snapshot.underlying_price,
        "warnings": snapshot.warnings,
    })
    _write_json(snapshot_dir / "expirations.json", {"expirations": snapshot.expirations, "windows": [asdict(item) for item in analysis.windows]})
    _write_contracts(snapshot_dir / "calls.csv", snapshot.calls)
    _write_contracts(snapshot_dir / "puts.csv", snapshot.puts)
    _write_json(snapshot_dir / "analysis.json", analysis_to_dict(analysis))
    _write_json(snapshot_dir / "agent_updates.json", analysis.agent_updates)


def latest_derivative_analysis(output_dir: Path, ticker: str | None = None) -> dict | None:
    root = Path(output_dir) / "greenrock" / "derivatives" / "snapshots"
    if not root.exists():
        return None
    paths = []
    if ticker:
        paths = list((root / ticker.strip().upper()).glob("*/analysis.json"))
    else:
        paths = list(root.glob("*/*/analysis.json"))
    if not paths:
        return None
    latest = max(paths, key=lambda path: path.stat().st_mtime)
    with latest.open(encoding="utf-8") as json_file:
        return json.load(json_file)


def options_manifesto(output_dir: Path) -> dict[str, str | int]:
    latest = latest_derivative_analysis(output_dir)
    if not latest:
        return {
            "status": "empty",
            "staged_tickers_analyzed": 0,
            "healthy_chains": 0,
            "warnings": 0,
            "timing_leader": "none",
            "strongest_30d": "none",
            "strongest_60d": "none",
            "strongest_90d": "none",
            "material_warning": "Run Derivative Workbench analysis.",
            "updated_at": "none",
        }
    return {
        "status": "available",
        "staged_tickers_analyzed": 1,
        "healthy_chains": 1 if latest.get("chain_quality", {}).get("status") == "healthy" else 0,
        "warnings": len(latest.get("warnings", [])),
        "timing_leader": f"{latest.get('ticker', 'none')} {latest.get('timing_score', {}).get('score', '-')}",
        "strongest_30d": _strongest_window(latest, "30"),
        "strongest_60d": _strongest_window(latest, "60"),
        "strongest_90d": _strongest_window(latest, "90"),
        "material_warning": "; ".join(latest.get("warnings", [])[:1]) or "No material IV/liquidity warning in latest analysis.",
        "updated_at": latest.get("created_at", "none"),
    }


def analysis_to_dict(analysis: DerivativeAnalysis) -> dict:
    return {
        "ticker": analysis.ticker,
        "snapshot_id": analysis.snapshot_id,
        "created_at": analysis.created_at,
        "provider": analysis.provider,
        "underlying_price": analysis.underlying_price,
        "windows": [asdict(item) for item in analysis.windows],
        "chain_quality": analysis.chain_quality,
        "timing_score": asdict(analysis.timing_score),
        "top_calls": {key: [_contract_research_dict(item) for item in rows] for key, rows in analysis.top_calls.items()},
        "top_puts": {key: [_contract_research_dict(item) for item in rows] for key, rows in analysis.top_puts.items()},
        "scenario_grid": [asdict(item) for item in analysis.scenario_grid],
        "agent_updates": analysis.agent_updates,
        "warnings": list(analysis.warnings),
        "snapshot_path": analysis.snapshot_path,
    }


def derivatives_snapshot_dir(output_dir: Path, ticker: str, snapshot_id: str) -> Path:
    return Path(output_dir) / "greenrock" / "derivatives" / "snapshots" / ticker.strip().upper() / snapshot_id


def window_dte(expiration: str) -> int:
    try:
        return max(0, (date.fromisoformat(expiration) - date.today()).days)
    except ValueError:
        return 0


def agent_updates(snapshot: OptionsChainSnapshot, timing: TimingScore, chain_quality: dict[str, float | int | str]) -> dict[str, str]:
    return {
        "Chain Agent": f"{len(snapshot.expirations)} expirations, {len(snapshot.calls)} calls, {len(snapshot.puts)} puts.",
        "Timing Agent": f"Derivative Timing Score {timing.score}.",
        "Greeks Agent": "American binomial model selected for listed U.S. equity options.",
        "Scenario Agent": "Scenario grid prepared for research-only single-leg analysis.",
        "Derivatives Analyst": f"Chain quality {chain_quality.get('status', 'unknown')}; outputs are not recommendations.",
    }


def _binomial_value(option_type: str, underlying: float, strike: float, years: float, volatility: float, rate: float, dividend: float, steps: int) -> tuple[float, bool]:
    dt = years / steps
    up = math.exp(volatility * math.sqrt(dt))
    down = 1 / up
    growth = math.exp((rate - dividend) * dt)
    probability = min(1.0, max(0.0, (growth - down) / (up - down)))
    discount = math.exp(-rate * dt)
    values = [_intrinsic(option_type, underlying * (up ** node) * (down ** (steps - node)), strike) for node in range(steps + 1)]
    early = False
    for step in range(steps - 1, -1, -1):
        next_values = []
        for node in range(step + 1):
            stock_price = underlying * (up ** node) * (down ** (step - node))
            continuation = discount * (probability * values[node + 1] + (1 - probability) * values[node])
            exercise = _intrinsic(option_type, stock_price, strike)
            if exercise > continuation + 1e-9:
                early = True
            next_values.append(max(continuation, exercise))
        values = next_values
    return values[0], early


def _finite_delta(option_type: str, underlying: float, strike: float, years: float, volatility: float, rate: float, dividend: float, steps: int) -> float:
    bump = max(0.01, underlying * 0.01)
    high = _binomial_value(option_type, underlying + bump, strike, years, volatility, rate, dividend, steps)[0]
    low = _binomial_value(option_type, max(0.01, underlying - bump), strike, years, volatility, rate, dividend, steps)[0]
    return (high - low) / (2 * bump)


def _finite_gamma(option_type: str, underlying: float, strike: float, years: float, volatility: float, rate: float, dividend: float, steps: int) -> float:
    bump = max(0.01, underlying * 0.01)
    high = _binomial_value(option_type, underlying + bump, strike, years, volatility, rate, dividend, steps)[0]
    mid = _binomial_value(option_type, underlying, strike, years, volatility, rate, dividend, steps)[0]
    low = _binomial_value(option_type, max(0.01, underlying - bump), strike, years, volatility, rate, dividend, steps)[0]
    return (high - 2 * mid + low) / (bump ** 2)


def _finite_theta(option_type: str, underlying: float, strike: float, years: float, volatility: float, rate: float, dividend: float, steps: int) -> float:
    shorter = max(0.0, years - 1 / 365)
    current = _binomial_value(option_type, underlying, strike, years, volatility, rate, dividend, steps)[0]
    next_day = _binomial_value(option_type, underlying, strike, shorter, volatility, rate, dividend, steps)[0] if shorter else _intrinsic(option_type, underlying, strike)
    return next_day - current


def _finite_vol(option_type: str, underlying: float, strike: float, years: float, volatility: float, rate: float, dividend: float, steps: int) -> float:
    high = _binomial_value(option_type, underlying, strike, years, volatility + 0.01, rate, dividend, steps)[0]
    low = _binomial_value(option_type, underlying, strike, years, max(0.001, volatility - 0.01), rate, dividend, steps)[0]
    return (high - low) / 2


def _finite_rate(option_type: str, underlying: float, strike: float, years: float, volatility: float, rate: float, dividend: float, steps: int) -> float:
    high = _binomial_value(option_type, underlying, strike, years, volatility, rate + 0.01, dividend, steps)[0]
    low = _binomial_value(option_type, underlying, strike, years, volatility, rate - 0.01, dividend, steps)[0]
    return (high - low) / 2


def _intrinsic(option_type: str, underlying: float, strike: float) -> float:
    return max(0.0, underlying - strike) if option_type == "call" else max(0.0, strike - underlying)


def _expiration_delta(option_type: str, underlying: float, strike: float) -> float:
    if option_type == "call":
        return 1.0 if underlying > strike else 0.0
    return -1.0 if underlying < strike else 0.0


def _score_ma(prices: tuple[float, ...]) -> float:
    try:
        latest = prices[-1]
        sma20 = simple_moving_average(prices, 20)
        sma50 = simple_moving_average(prices, 50)
        return _clamp(50 + (latest - sma20) / sma20 * 130 + (sma20 - sma50) / sma50 * 100)
    except (ValueError, ZeroDivisionError, IndexError):
        return 45.0


def _score_bollinger(prices: tuple[float, ...]) -> float:
    try:
        lower, middle, upper = bollinger_bands(prices, 20)
        if upper == lower:
            return 50.0
        position = (prices[-1] - lower) / (upper - lower)
        return _clamp(100 - abs(position - 0.35) * 120)
    except (ValueError, ZeroDivisionError, IndexError):
        return 45.0


def _score_volume(volumes: tuple[int, ...]) -> float:
    try:
        current, prior = average_volume_trend(volumes, 10)
        return _clamp(50 + (current - prior) / prior * 80)
    except (ValueError, ZeroDivisionError):
        return 45.0


def _score_rsi(prices: tuple[float, ...]) -> float:
    try:
        rsi = relative_strength_index(prices)
        return _clamp(100 - abs(rsi - 52) * 1.4)
    except ValueError:
        return 45.0


def _score_trend(prices: tuple[float, ...]) -> float:
    try:
        return _clamp(50 + moving_average_rate_of_change(prices, 10, 10) * 240)
    except (ValueError, ZeroDivisionError):
        return 45.0


def _score_low_proximity(prices: tuple[float, ...]) -> float:
    try:
        _, proximity = week_52_low_proximity(prices)
        return _clamp(100 - proximity * 100)
    except (ValueError, ZeroDivisionError):
        return 45.0


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _spread_pct(contract: OptionContract) -> float:
    if contract.bid is None or contract.ask is None or contract.bid <= 0 or contract.ask <= 0:
        return 100.0
    mid = (contract.bid + contract.ask) / 2
    return (contract.ask - contract.bid) / mid * 100 if mid else 100.0


def _contracts_from_frame(frame, option_type: str, expiration: str) -> tuple[OptionContract, ...]:
    contracts: list[OptionContract] = []
    for _, row in frame.iterrows():
        contracts.append(
            OptionContract(
                contract_symbol=str(row.get("contractSymbol", "")),
                option_type=option_type,
                expiration=expiration,
                strike=_optional_float(row.get("strike")) or 0.0,
                bid=_optional_float(row.get("bid")),
                ask=_optional_float(row.get("ask")),
                last=_optional_float(row.get("lastPrice")),
                volume=_optional_int(row.get("volume")),
                open_interest=_optional_int(row.get("openInterest")),
                implied_volatility=_optional_float(row.get("impliedVolatility")),
            )
        )
    return tuple(contracts)


def _optional_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _first_contract(top_calls: dict[str, tuple[ContractResearch, ...]], top_puts: dict[str, tuple[ContractResearch, ...]]) -> ContractResearch | None:
    candidates = [rows[0] for rows in list(top_calls.values()) + list(top_puts.values()) if rows]
    return max(candidates, key=lambda item: item.score) if candidates else None


def _contract_research_dict(item: ContractResearch) -> dict:
    return {
        "contract": asdict(item.contract),
        "score": item.score,
        "factors": item.factors,
        "warnings": item.warnings,
        "model": asdict(item.model),
        "breakeven": item.breakeven,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_contracts(path: Path, contracts: tuple[OptionContract, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=SNAPSHOT_HEADERS)
        writer.writeheader()
        for contract in contracts:
            writer.writerow(asdict(contract))


def _strongest_window(analysis: dict, window: str) -> str:
    rows = analysis.get("top_calls", {}).get(window, []) + analysis.get("top_puts", {}).get(window, [])
    if not rows:
        return "none"
    best = max(rows, key=lambda item: item.get("score", 0))
    contract = best.get("contract", {})
    return f"{contract.get('option_type', '')} {contract.get('strike', '')} score {best.get('score', '-')}"
