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

- Make the GreenRock Score Calculator the primary operator surface for explaining why a name qualifies before it appears in a report.
- Keep `/greenrock/score` structured as a reusable product surface: hero score card, confidence card, Fundamental Guardrails card, research priority badge, analyst summary, Bullish Evidence, Bearish Evidence, What to Watch Next, ticker input, rank bands, score breakdown cards, 1-year statistical price targets, Finviz link, real-data badge, save-to-list area, and data-quality warnings.
- Preserve preview-only behavior for score calculation. It must not create reports, approvals, artifacts, emails, publications, or client-facing files. Saving a ticker writes only to local GreenRock list CSVs.
- Keep analyst intelligence deterministic and template-based. Do not use LLM/API calls for the score calculator summary.
- Calibrate GreenRock Confidence as evidence reliability, not just data existence. It should vary across tickers based on data depth, signal agreement, volatility/noise, bucket reliability, and target reliability.
- Keep Fundamental Guardrails light and confidence-weighted. Net cash/debt, quick ratio, and share-count change should support survivability analysis without turning GreenRock into a full valuation model.
- Keep GreenRock Score technical-first. Fundamental Guardrails may add only a small capped score adjustment, while materially affecting Confidence when evidence is strong, weak, incomplete, or conflicted.
- Calibrate GreenRock Score against historical mock fixtures first, then approved historical data later.
- Add sub-scores for trend, dislocation, participation, volatility, and liquidity.
- Track score stability across multiple lookback windows.
- Add chart overlays for score components so subscribers can visually compare dislocation, momentum, volume, and moving-average structure.
- Prepare future `www.greenrockam.com` adaptation by keeping calculator HTML sections semantic and CSS classes product-oriented, without adding external publishing from Atlas OS.

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
