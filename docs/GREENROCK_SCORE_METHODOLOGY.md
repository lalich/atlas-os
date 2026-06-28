# GreenRock Score Methodology

GreenRock Score is a 0-100 technical dislocation score used by Atlas OS to rank review candidates for GreenRock Analysts workflows. It is a screening and prioritization aid only. It is not a recommendation, guarantee, or client-facing conclusion.

GreenRock Score and GreenRock Confidence are separate:

- GreenRock Score measures the opportunity/dislocation setup.
- GreenRock Confidence measures data quality, data depth, signal agreement, and reliability of the score.
- Fundamental Guardrails measure survivability and recovery support from light balance-sheet and dilution checks.
- Confidence may be high when Score is moderate if the evidence is clean and complete.
- Confidence may be low when Score is high if the data is shallow, noisy, incomplete, or internally conflicted.

## Current Component Weights

| Component | Current Weight | Purpose | Future Tuning Notes |
|---|---:|---|---|
| 52-week low proximity | 20 points | Rewards names trading close to their 52-week low, which is the core dislocation setup. | Test alternate low-distance curves and sector-adjusted ranges. |
| Bollinger Band setup | 20 points | Rewards price location closer to the lower 2.5 standard deviation Bollinger Band. | Evaluate persistence below/near the lower band and false-positive rates. |
| RSI | 15 points | Rewards lower RSI values below the neutral threshold. | Calibrate RSI cutoffs by volatility regime and market-cap group. |
| Volume acceleration | 15 points | Rewards improving 10-day average volume versus the prior 10-day average. | Add liquidity floors and distinguish accumulation from event-driven volume spikes. |
| Moving average structure | 20 points | Rewards dislocated moving-average structure, including 8 EMA below 10 SMA, 50 DMA below 150 DMA, and improving 50 DMA rate of change versus 150 DMA. | Split trend damage and trend repair into separate sub-scores. |
| Bullish / Bearish Evidence | 10 points | Shows explicit setup support and research cautions that affect interpretation of the setup. | Add calibrated evidence weights for missing data, extreme liquidity risk, or stale prices once production controls exist. |

The component total is capped at 100.

Fundamental Guardrails can add a small capped `fundamental_guardrail_adjustment` after the technical component score:

| Guardrail | Score Adjustment |
|---|---:|
| Strong Balance Sheet | +2 |
| Acceptable | +1 |
| Caution | -2 |
| Red Flag | -5 |
| Insufficient Data | 0 |

This adjustment is intentionally small. GreenRock Score remains technical-first; strong fundamentals do not by themselves create a high dislocation score.

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

## Research Priority

Research Priority is a local workflow label, not investment advice. It combines GreenRock Score, GreenRock Confidence, liquidity/data quality, signal label, and strict/ranked/watchlist classification.

| Priority | Meaning |
|---|---|
| Immediate Review | Highest-scoring, higher-confidence strict-pass setups for near-term analyst review. |
| This Week | Strong higher-confidence setups that merit timely research attention. |
| Interesting | Watchlist-level setups with enough confidence for research follow-up. |
| Monitor | Lower-scoring or lower-confidence setups worth tracking but not urgent. |
| Ignore | Weak setup or low confidence under current GreenRock criteria. |

## GreenRock Confidence Bands

| Confidence Range | Band |
|---:|---|
| 90-100 | Very High Confidence |
| 75-89 | High Confidence |
| 60-74 | Moderate Confidence |
| 40-59 | Low Confidence |
| Below 40 | Very Low Confidence |

Confidence considers:

- Data completeness: price history, volume history, market cap, All-Time High, 52-week low, and 5-year target availability.
- Data depth: full 5-year history receives stronger confidence than 3-5 years, 1-3 years, or less than 1 year.
- Indicator agreement: confidence improves when multiple GreenRock indicators point in the same direction and falls when signals conflict.
- Signal stability: volatile price action and erratic volume reduce confidence.
- Bucket reliability: missing or borderline market cap lowers confidence.
- Target reliability: limited target history, missing ATH, or unavailable statistical targets lower confidence.
- Fundamental Guardrails: net cash, strong quick ratio, stable shares, and complete fundamental data can raise confidence; net debt, weak quick ratio, dilution, and incomplete liquidity metrics can lower confidence.

## Fundamental Guardrails

Fundamental Guardrails are light viability checks, not a full valuation model. They do not produce buy, sell, or hold conclusions and do not replace analyst review.

Fields used where available:

- Cash and equivalents.
- Total debt.
- Net cash or net debt.
- Net cash per share.
- Quick ratio, or current assets minus inventory divided by current liabilities when the provider supplies the inputs.
- Current assets, inventory, and current liabilities.
- Current and prior shares outstanding.
- Shares outstanding change percent.
- Fundamental data source and warnings.

Labels:

| Label | Interpretation |
|---|---|
| Strong Balance Sheet | Positive net cash, quick ratio at or above 1.5, and stable or declining share count. |
| Acceptable | Manageable debt, quick ratio at or above 1.0, and no major dilution signal. |
| Caution | Net debt appears meaningful, quick ratio is below 1.0, or share count is expanding meaningfully. |
| Red Flag | Liquidity weakness, major dilution, or severe leverage appears in available data. |
| Insufficient Data | Key inputs are missing; this lowers evidence confidence but does not automatically imply weakness. |

Fundamentals primarily affect GreenRock Confidence because they help assess survivability and recovery support. A ticker can have a high GreenRock Score with low Confidence if the technical setup is attractive but the evidence quality or guardrails are weak. A ticker can also have moderate Score with higher Confidence if data depth, indicator agreement, and guardrails are clean.

## Current Formula Notes

- 52-week low proximity contributes more points as price gets closer to the 52-week low within the current 10% proximity band.
- Bollinger Band setup contributes more points as price is positioned nearer the lower 2.5 standard deviation band than the upper band.
- RSI contributes more points as RSI falls below 50.
- Volume acceleration contributes more points as current 10-day average volume improves versus the prior 10-day average.
- Moving average structure combines short-term dislocation, longer-term trend damage, and early improvement in the 50 DMA rate of change.
- Bullish Evidence and Bearish Evidence are always explained in the calculator output. Bullish examples include price below the lower 2.5 standard deviation Bollinger Band, strong volume acceleration, and unusually deep dislocation near the 52-week low. Bearish examples include missing data, extreme illiquidity, insufficient price history, weak market-cap data, and moving average structure that is not aligned with GreenRock criteria.
- Bullish and bearish fundamental evidence is shown separately inside Fundamental Guardrails, including balance-sheet quality, quick ratio context, share-count stability or dilution, confidence impact, and data warnings.

## Analyst Intelligence Notes

The score calculator also generates deterministic, template-based analyst intelligence without any LLM or API call:

- Bullish Evidence: metric-driven observations that support the GreenRock setup.
- Bearish Evidence: metric-driven cautions or data-quality concerns.
- What to Watch Next: continuation and validation items such as reclaiming moving averages, improving RSI, volume continuation, holding recent lows, and movement toward statistical target levels.
- Analyst Summary: a short plain-English summary built from score, confidence, evidence, and research priority.

## 1-Year Statistical Price Target Notes

The score calculator displays a 1-Year Statistical Price Targets table for the requested ticker when sufficient price history is available. These are statistical targets, not forecasts or guarantees.

| Field | Meaning |
|---|---|
| Current price | Latest available close used by the local score preview. |
| All-Time High | Highest close available in the full price history returned by the provider, not the 52-week high. |
| Historical lookback | Previous 5 years of price history where available. |
| Horizon | 1 year. |
| +2, +3, +5, +7 SD target | Current price plus the selected number of annualized standard deviations based on prior 5-year daily return behavior. |

Targets below the available All-Time High are styled pink because they remain below a prior high-water mark. Targets above the available All-Time High are styled GreenRock green. If the provider returns limited history, Atlas warns that ATH is based on available provider history and is not guaranteed full exchange history. If All-Time High or standard deviation data cannot be calculated cleanly, Atlas shows a data-quality warning instead of implying precision.

## Data Quality Notes

Atlas OS tracks whether a ticker has usable price history, market cap, volume data, and a 52-week low. Missing or weak data should be treated as a review warning. Real-data score previews and reports remain local, approval-gated where applicable, and not approved for publication.

## Future Refinement Ideas

- Add score history across runs.
- Add component trend charts.
- Add sector-relative ranking.
- Add liquidity and spread penalties.
- Add volatility-regime adjustments.
- Backtest component weights against approved historical datasets before any production use.
