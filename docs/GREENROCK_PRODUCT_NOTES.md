# GreenRock Product Notes

## Report Improvements

- Add richer summary language for sector, factor, and volatility context once approved data sources exist.
- Add one-page executive summary formatting for faster subscriber review.
- Add candidate-level change tracking versus the prior month.

## Data Improvements

- Replace mock data with an approved market data provider only after vendor, credential, and compliance controls are defined.
- Add survivorship-bias checks, liquidity thresholds, and history completeness checks.
- Store raw input snapshots for reproducibility.
- Maintain separate concepts for population scans, curated watchlists, and report picks. Population scans broaden the source universe but should not bypass report approval gates.
- Keep Micro/Moonshot population storage editable for non-index-style names that may not appear in standard index populations.
- Keep scanner promotion and direct scan-to-staging local-only and duplicate-safe. Promotion should write selected scan tickers into GreenRock list CSVs, and staging should write only to the report candidate staging CSV without creating reports, approvals, PDFs, emails, publications, or client-facing files.
- Treat the discovery workflow as Scan, Stage, Generate Draft, Approve, Export PDF. The `/greenrock/discovery` page should help operators understand which stage they are in and what the next local action is.
- Keep `/greenrock/scanner` focused on discovery: latest scan metadata, quick filters, ranked candidates, direct scan-to-staging, batch promotion, Finviz links, data-quality warnings, and clear no-report/no-approval language.
- Keep `/greenrock/watchlists` focused on curated research queues: ticker counts, tickers, Finviz links, promotion source, latest promoted timestamp when promotion metadata exists, and confirmed manual removal.
- Store promotion metadata as a sidecar CSV so existing watchlist/universe CSV files remain simple ticker lists.
- Keep `/greenrock/staging` as the final human curation layer before approval-gated report generation. It should allow bucket moves, removals, notes, readiness checks, and source context without creating report runs, approvals, PDFs, emails, publications, or client-facing artifacts.
- Keep staging buckets explicit: Mega Rock Candidate, Large Cap Candidate, Small/Mid Candidate, Research Only, and Excluded. Count targets should remain visible for the three report candidate buckets.
- Staging-sourced report generation may create normal workflow runs, report artifacts, and pending approvals only after explicit operator confirmation. It must not publish, email, or export a PDF automatically.
- Scanner populations should not automatically feed reports. Staging is the preferred curated bridge into approval-gated draft generation.
- Keep staging-sourced report tables editorial and readable: compact main columns, clean empty-bucket sentences, long bullish/caution signals moved into candidate notes or an appendix, and green table headers with yellow text.
- Keep bucket-list guardrails firm. If market-cap bucket data conflicts with Mega Rock, Large Cap, or Small/Mid destinations, block the save and suggest the proper list or Personal Watchlist.

## Scoring Improvements

- Make the GreenRock Score Calculator the primary operator surface for explaining why a name qualifies before it appears in a report.
- Keep `/greenrock/score` structured as a reusable product surface: hero score card, confidence card, Evidence Agreement card, Evidence Engine section, Fundamental Guardrails card, research priority badge, analyst summary, Bullish Evidence, Bearish Evidence, Neutral / Watch Items, What to Watch Next, ticker input, rank bands, score breakdown cards, 1-year statistical price targets, Finviz link, real-data badge, save-to-list area, and data-quality warnings.
- Preserve preview-only behavior for score calculation. It must not create reports, approvals, artifacts, emails, publications, or client-facing files. Saving a ticker writes only to local GreenRock list CSVs.
- Keep analyst intelligence deterministic and template-based. Do not use LLM/API calls for the score calculator summary.
- Calibrate GreenRock Confidence as evidence reliability, not just data existence. It should vary across tickers based on data depth, signal agreement, volatility/noise, bucket reliability, and target reliability.
- Keep the GreenRock Evidence Engine structured. Evidence items should preserve name, category, direction, strength, numeric contribution, and explanation so UI, CLI, reports, and future subscriber surfaces can explain why a score changed.
- Treat Evidence Agreement as a separate alignment metric. It should clarify why strong technical setups may still have moderate Confidence when fundamentals, target reliability, or data quality conflict.
- Keep Fundamental Guardrails light and confidence-weighted. Net cash/debt, quick ratio, and share-count change should support survivability analysis without turning GreenRock into a full valuation model.
- Keep GreenRock Score technical-first. Fundamental Guardrails may add only a small capped score adjustment, while materially affecting Confidence when evidence is strong, weak, incomplete, or conflicted.
- Calibrate GreenRock Score against historical mock fixtures first, then approved historical data later.
- Add sub-scores for trend, dislocation, participation, volatility, and liquidity.
- Track score stability across multiple lookback windows.
- Add chart overlays for score components so subscribers can visually compare dislocation, momentum, volume, and moving-average structure.
- Prepare future `www.greenrockam.com` adaptation by keeping calculator HTML sections semantic and CSS classes product-oriented, without adding external publishing from Atlas OS.
- Add future report sourcing controls so approved operators can choose latest population scan outputs as upstream candidates without replacing human review or approval gates.
- Keep the Analyst Summary Finviz button visible as a research convenience, not a recommendation or publication action.

## Future Options Analysis

- Add optional options-market context only after approved data access exists.
- Potential fields: implied volatility rank, skew, liquidity, open interest, and event-risk flags.
- Keep options commentary educational and risk-aware, not personalized.

## Future PDF/Graphics/Branding

- Keep branded PDF export readable as report quality stabilizes.
- Use the local GreenRock logo at `atlas_os/static/greenrock_logo.png` across browser pages and report/PDF output when available; missing logos must not break report generation.
- Consider charts for price versus moving averages, Bollinger Bands, RSI, and volume trend.
- Add GreenRock visual styling, cover page, section dividers, and score legends.

## Compliance Review Notes

- Maintain human approval gating before publication or distribution.
- Avoid guarantees, promissory language, and personalized recommendations.
- Clearly label mock data and internal workflow testing until production data sources are approved.
- Preserve audit logs, run IDs, artifacts, and approval records for every report draft.
