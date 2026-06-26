# GreenRock Score Methodology

GreenRock Score is a 0-100 technical dislocation score used by Atlas OS to rank review candidates for GreenRock Analysts workflows. It is a screening and prioritization aid only. It is not a recommendation, guarantee, or client-facing conclusion.

## Current Component Weights

| Component | Current Weight | Purpose | Future Tuning Notes |
|---|---:|---|---|
| 52-week low proximity | 20 points | Rewards names trading close to their 52-week low, which is the core dislocation setup. | Test alternate low-distance curves and sector-adjusted ranges. |
| Bollinger Band setup | 20 points | Rewards price location closer to the lower 2.5 standard deviation Bollinger Band. | Evaluate persistence below/near the lower band and false-positive rates. |
| RSI | 15 points | Rewards lower RSI values below the neutral threshold. | Calibrate RSI cutoffs by volatility regime and market-cap group. |
| Volume acceleration | 15 points | Rewards improving 10-day average volume versus the prior 10-day average. | Add liquidity floors and distinguish accumulation from event-driven volume spikes. |
| Moving average structure | 20 points | Rewards dislocated moving-average structure, including 8 EMA below 10 SMA, 50 DMA below 150 DMA, and improving 50 DMA rate of change versus 150 DMA. | Split trend damage and trend repair into separate sub-scores. |
| Bonus / penalty factors | 10 points | Shows explicit score adjustments and data-quality risks that affect confidence in the setup. | Add calibrated penalties for missing data, extreme liquidity risk, or stale prices once production controls exist. |

The component total is capped at 100.

## Signal Labels

| Score Range | Label |
|---:|---|
| 85-100 | Exceptional |
| 70-84 | Strong |
| 55-69 | Watchlist |
| Below 55 | Excluded or Low Priority |

## Selection Labels

| Label | Meaning |
|---|---|
| Strict Pass | The ticker passed all current GreenRock screening rules. |
| Ranked Candidate | The ticker did not pass every strict rule but ranked high enough to appear in ranked real-data selection mode. |
| Watchlist | The ticker is visible for review but carries weaker score/rule support. |

## Current Formula Notes

- 52-week low proximity contributes more points as price gets closer to the 52-week low within the current 10% proximity band.
- Bollinger Band setup contributes more points as price is positioned nearer the lower 2.5 standard deviation band than the upper band.
- RSI contributes more points as RSI falls below 50.
- Volume acceleration contributes more points as current 10-day average volume improves versus the prior 10-day average.
- Moving average structure combines short-term dislocation, longer-term trend damage, and early improvement in the 50 DMA rate of change.
- Bonus / penalty factors are always explained in the calculator output. Bonus examples include price below the lower 2.5 standard deviation Bollinger Band, strong volume acceleration, and unusually deep dislocation near the 52-week low. Penalty-risk examples include missing data, extreme illiquidity, insufficient price history, weak market-cap data, and moving average structure that is not aligned with GreenRock criteria.

## Price Target Notes

The score calculator also displays a Standard Deviation Price Targets table for the requested ticker when sufficient price history is available.

| Field | Meaning |
|---|---|
| Current price | Latest available close used by the local score preview. |
| All-time high | Highest close available in the fetched price history. |
| +2, +3, +5, +7 SD target | Current price plus the selected number of standard deviations from the available close history. |

Targets below the available all-time high are styled pink because they remain below a prior high-water mark. Targets above the available all-time high are styled GreenRock green. If all-time high or standard deviation data cannot be calculated cleanly, Atlas shows a data-quality warning instead of implying precision.

## Data Quality Notes

Atlas OS tracks whether a ticker has usable price history, market cap, volume data, and a 52-week low. Missing or weak data should be treated as a review warning. Real-data score previews and reports remain local, approval-gated where applicable, and not approved for publication.

## Future Refinement Ideas

- Add score history across runs.
- Add component trend charts.
- Add sector-relative ranking.
- Add liquidity and spread penalties.
- Add volatility-regime adjustments.
- Backtest component weights against approved historical datasets before any production use.
