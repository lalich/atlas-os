# GreenRock Derivative Workbench

Phase 10A introduced the research-only GreenRock Derivative Workbench at `/greenrock/derivatives`. Phases 10B-10F added transparent ranking factors, exclusions, cross-window context, read-only position context, and strategy intent labels without adding brokerage execution.

## Safety Boundary

The Derivative Workbench is local-only. It does not trade, place broker/API orders, send email or Slack messages, publish, create client files, call external LLM/API services, create reports, insert options into reports, approve drafts, or export PDFs.

Outputs are research context only. Use language such as research fit, contract quality, scenario profile, timing alignment, position context, and strategy intent. Do not use buy/sell/hold language, personalized recommendations, ACT mechanics, spreads, ratios, or LEAPS in this Workbench.

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

Each Top Research row also serializes concise `score_factors` and `ranking_rationale` values so the UI and `analysis.json` explain why a contract surfaced.

## OTM Top Research Guardrail

Top Research lists are OTM-only by default:

- Top Research Calls require strike greater than the current underlying price.
- Top Research Puts require strike less than the current underlying price.

ITM and deep ITM contracts are retained in raw chain snapshots (`calls.csv` and `puts.csv`) for inspection and future research, but they are excluded from Top Research Calls/Puts in this phase. The Contract Research Score favors OTM contracts that are reasonably near the underlying and penalizes extremely far OTM, very wide spreads, low/no open interest and volume, missing IV/model data, and near-worthless lottery-style contracts.

This remains research-only ranking language, not a recommendation.

## Developer Notes: Phase 10A-10F Stack

- OTM-only Top Research: Calls must have strikes above the underlying price, and puts must have strikes below it. The raw `calls.csv` and `puts.csv` files preserve the full chain, including ITM and excluded contracts.
- Exclusions: Contracts filtered out of Top Research are serialized under `excluded_calls` and `excluded_puts` with reasons such as ITM/ATM, missing IV, poor liquidity, wide spread, unusable premium, or missing/invalid quote data.
- Scoring and rationale: Ranked contracts expose weighted factor scores for liquidity, spread quality, IV condition, OTM distance/proximity, premium quality, window fit, timing alignment, and scenario behavior. `ranking_rationale` is short UI copy derived from those factors.
- Cross-window intelligence: `cross_window` compares same-snapshot 30D/60D/90D Top Research cohorts using expiration/DTE windows. It classifies an idea as strengthening, stable, weakening, isolated, or insufficient_data without creating synthetic history.
- Position context: The optional local `greenrock/derivatives/position_context.csv` file is read-only. If present, it can add shares, average cost, existing option exposure, direction, and research flags; missing context never blocks Top Research.
- Strategy intent mapping: `strategy_intent`, `intent_rationale`, `manifesto_alignment`, and `position_context_alignment` are read-only labels for research framing, such as income_overlay, cash_secured_entry, downside_hedge, speculative_convexity, avoid_conflict, or research_only.
- No brokerage execution: The Workbench has no order construction, broker API integration, credential handling, or trading action. Strategy intent is not an execution instruction.

## Scenario Lab

The P/L scenario lab shows premium at risk, breakeven, intrinsic/extrinsic value, theoretical value, dollar P/L, percent P/L, Greeks, and assumptions. The scenario grid evaluates underlying moves from `-15%` to `+20%` across today, one-third through remaining life, two-thirds through remaining life, and expiration.

## Options Manifesto

Atlas Wall shows a compact Options Manifesto, not detailed chain tables. It summarizes staged tickers analyzed, healthy chains, warnings, timing leader, strongest 30D/60D/90D research setups, material IV/liquidity warning, and updated timestamp. If no analysis exists, it shows a neutral empty state with a link to Derivative Workbench.
