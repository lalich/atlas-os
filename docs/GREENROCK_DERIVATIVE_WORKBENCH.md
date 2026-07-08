# GreenRock Derivative Workbench

Phase 10A adds the first research-only GreenRock Derivative Workbench at `/greenrock/derivatives`.

## Safety Boundary

The Derivative Workbench is local-only. It does not trade, place broker/API orders, send email or Slack messages, publish, create client files, call external LLM/API services, create reports, insert options into reports, approve drafts, or export PDFs.

Outputs are research context only. Use language such as research fit, contract quality, scenario profile, and timing alignment. Do not use buy/sell/hold language, personalized recommendations, ACT mechanics, spreads, ratios, or LEAPS in Phase 10A.

## Scope

- Analyze staged GreenRock tickers.
- Analyze a manual ticker without mutating staging, watchlists, reports, approvals, PDFs, universes, or client files.
- Focus on nearest practical 30 / 60 / 90 day listed expirations.
- Rank bounded single-leg calls and puts only.
- Store local snapshots under `.atlas/output/greenrock/derivatives/snapshots/<ticker>/<snapshot_id>/`.

## Provider Diagnostics

CLI:

```bash
atlas greenrock derivatives doctor AAPL
```

Doctor checks local provider configuration and whether the chain exposes underlying price, expirations, calls, puts, bid, ask, last, volume, open interest, implied volatility, contract symbol, strike, and expiration. Missing values are not faked.

## Snapshot Commands

```bash
atlas greenrock derivatives snapshot AAPL
atlas greenrock derivatives analyze AAPL
atlas greenrock derivatives analyze-staged
```

Each successful snapshot writes:

- `metadata.json`
- `expirations.json`
- `calls.csv`
- `puts.csv`
- `analysis.json`
- `agent_updates.json`

## Model

The primary model is an American-style Cox-Ross-Rubinstein binomial tree for listed U.S. equity options. It supports calls, puts, early exercise checks, dividend yield, risk-free rate, volatility, DTE, configurable steps, intrinsic value, extrinsic value, and finite-difference Greeks.

If dividend yield is missing, Phase 10A uses `0%` with a documented assumption. If risk-free rate is missing, Phase 10A uses the local default constant `4%` with a documented assumption. Missing or invalid IV makes model status unavailable rather than inventing volatility.

Barone-Adesi-Whaley is deferred as a future comparison/fast approximation. It does not produce Phase 10A numbers.

## Scores

Derivative Timing Score is separate from GreenRock Score and does not mutate Score, Confidence, Evidence Agreement, staging, ranking, or reports.

Initial weights:

- Moving-average structure: 24%
- Bollinger setup: 22%
- Volume acceleration: 20%
- RSI / momentum: 16%
- Short-term trend acceleration: 11%
- 52-week low proximity: 7%

Contract Research Score considers liquidity, bid/ask spread percentage, open interest, volume, delta range, theta burden, IV condition, breakeven distance, timing alignment, and scenario behavior. It does not rank by cheapest premium or maximum upside alone.

## OTM Top Research Guardrail

Phase 10A Top Research lists are OTM-only by default:

- Top Research Calls require strike greater than the current underlying price.
- Top Research Puts require strike less than the current underlying price.

ITM and deep ITM contracts are retained in raw chain snapshots (`calls.csv` and `puts.csv`) for inspection and future research, but they are excluded from Top Research Calls/Puts in this phase. The Contract Research Score favors OTM contracts that are reasonably near the underlying and penalizes extremely far OTM, very wide spreads, low/no open interest and volume, missing IV/model data, and near-worthless lottery-style contracts.

This remains research-only ranking language, not a recommendation.

## Scenario Lab

The P/L scenario lab shows premium at risk, breakeven, intrinsic/extrinsic value, theoretical value, dollar P/L, percent P/L, Greeks, and assumptions. The scenario grid evaluates underlying moves from `-15%` to `+20%` across today, one-third through remaining life, two-thirds through remaining life, and expiration.

## Options Manifesto

Atlas Wall shows a compact Options Manifesto, not detailed chain tables. It summarizes staged tickers analyzed, healthy chains, warnings, timing leader, strongest 30D/60D/90D research setups, material IV/liquidity warning, and updated timestamp. If no analysis exists, it shows a neutral empty state with a link to Derivative Workbench.
