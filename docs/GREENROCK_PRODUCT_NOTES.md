# GreenRock Product Notes

## Report Improvements

- Add richer summary language for sector, factor, and volatility context once approved data sources exist.
- Add one-page executive summary formatting for faster subscriber review.
- Add candidate-level change tracking versus the prior month.

## Data Improvements

- Replace mock data with an approved market data provider only after vendor, credential, and compliance controls are defined.
- Add survivorship-bias checks, liquidity thresholds, and history completeness checks.
- Store raw input snapshots for reproducibility.

## Scoring Improvements

- Calibrate GreenRock Score against historical mock fixtures first, then approved historical data later.
- Add sub-scores for trend, dislocation, participation, volatility, and liquidity.
- Track score stability across multiple lookback windows.

## Future Options Analysis

- Add optional options-market context only after approved data access exists.
- Potential fields: implied volatility rank, skew, liquidity, open interest, and event-risk flags.
- Keep options commentary educational and risk-aware, not personalized.

## Future PDF/Graphics/Branding

- Add branded PDF export after Markdown report quality stabilizes.
- Consider charts for price versus moving averages, Bollinger Bands, RSI, and volume trend.
- Add GreenRock visual styling, cover page, section dividers, and score legends.

## Compliance Review Notes

- Maintain human approval gating before publication or distribution.
- Avoid guarantees, promissory language, and personalized recommendations.
- Clearly label mock data and internal workflow testing until production data sources are approved.
- Preserve audit logs, run IDs, artifacts, and approval records for every report draft.

