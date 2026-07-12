# Atlas OS Data Sources

Atlas OS defaults to mock data. Real market data is optional, local, and still approval-gated.

## GreenRock Data Modes

Mock mode:

```bash
atlas greenrock report-draft --data mock
```

Mock mode uses local generated sample price and volume history. It does not call external APIs and remains the default.

Real mode:

```bash
atlas greenrock report-draft --data real
atlas greenrock report-draft --data real --selection ranked
atlas greenrock report-draft --data real --selection strict
```

Real mode is production-shaped but fail-closed. If no provider is configured, Atlas prints a blocked message and does not create a report, approval, artifact, email, publication, or external action.

The browser Command Center uses the same modes. On `http://127.0.0.1:8000/greenrock`, **Run Mock Report** sends `data_mode=mock` and **Run Real Report** sends `data_mode=real`.

## Selection Modes

Mock mode defaults to `strict`, requiring all GreenRock criteria. Real mode defaults to `ranked`, which scores all valid names in each universe and selects the best available candidates when strict criteria produce too few names. `strict` remains available for real mode when the operator wants to see only full criteria passes.

## Optional Provider Settings

For the first real provider, Atlas supports a yfinance adapter.

Local `.env` placeholders:

```text
ATLAS_MARKET_DATA_PROVIDER=yfinance
ATLAS_GREENROCK_REAL_TICKERS=
```

Atlas loads simple local `.env` values when present and does not override shell environment variables. The provider name is not a secret; credentials must never be committed.

One-copy setup:

```bash
export ATLAS_MARKET_DATA_PROVIDER=yfinance
python3 -m pip install -e ".[market-data]"
```

Check setup:

```bash
atlas doctor
```

Doctor reports virtualenv status, command path, provider setting, yfinance availability, logo presence, output directory writability, database initialization, latest scan availability, and Atlas Memory availability.

When `ATLAS_GREENROCK_REAL_TICKERS` is blank, Atlas uses the local GreenRock watchlist CSVs.

## Options Data

Phase 10A uses the same local provider setting for research-only options chain diagnostics and snapshots:

```bash
atlas greenrock derivatives doctor AAPL
atlas greenrock derivatives analyze AAPL
```

The Derivative Workbench checks underlying price, expirations, calls, puts, bid, ask, last, volume, open interest, implied volatility, contract symbol, strike, and expiration. Missing fields are reported as missing; Atlas does not fake option-chain values.

Successful snapshots are stored locally at `.atlas/output/greenrock/derivatives/snapshots/<ticker>/<snapshot_id>/` with metadata, expirations, calls, puts, analysis, and derivative agent update JSON/CSV files.

Listed U.S. equity options default to American binomial tree pricing. Missing dividend yield uses `0%` with a documented assumption, and missing risk-free rate uses the local default constant `4%`. Missing or invalid implied volatility makes the model unavailable for that contract instead of inventing volatility. Barone-Adesi-Whaley is deferred as a future comparison model.

Derivative analysis never creates reports, approvals, PDFs, emails, Slack messages, publications, trading actions, broker/API orders, client files, credentials, or external LLM/API calls.

## GreenRock Watchlists

GreenRock watchlists are operator-managed local ticker lists used as the default real-mode source set for the Picks Board and report.

```bash
atlas greenrock universe list
atlas greenrock universe add TSLA PLTR
atlas greenrock universe remove TSLA
atlas greenrock universe reset-mega-rock
atlas greenrock universe reset-large-cap
atlas greenrock universe reset-small-mid
atlas greenrock universe reset-all
atlas greenrock universe validate
```

Watchlists are stored locally at:

```text
.atlas/output/greenrock/universes/mega_rock.csv
.atlas/output/greenrock/universes/large_cap.csv
.atlas/output/greenrock/universes/small_mid_cap.csv
```

The Mega Rock candidate pool starts from likely $1T+ public companies only. Large-cap and small/mid-cap watchlists are configured review lists. Inclusion does not imply suitability, recommendation, or expected performance.

Optional dependency:

```bash
python3 -m pip install -e ".[market-data]"
```

## Picks Board Data Source

The GreenRock Picks Board at `/greenrock/picks` reads the run-specific CSV artifacts for the most recent approved GreenRock report and inherits that approved report's data mode and report data source. A mock report displays a MOCK data badge. A real yfinance report displays REAL data and the report source, such as `yfinance:greenrock_watchlists`.

Board hydration is same-run only. Atlas starts with the approved report's section CSVs, may fill empty fields from that same run's `candidates_csv`, supports compatible historical aliases such as `greenrock_score` and `price`, and marks truly missing values as unavailable in the approved report. Staging-sourced approved reports currently persist curated research fields such as score, confidence, evidence agreement, research priority, guardrail, source scan, approved section, and top signals; market cap, price, RSI, moving averages, and volume remain unavailable when those columns are blank in the approved-run CSVs. Operators can run `atlas greenrock picks-board --diagnostics` to inspect the selected approval, artifact roles, headers, ticker matches, populated counts, and unavailable-field reasons. The board does not use current scans, newer unapproved reports, live providers, or unrelated staging state to fill approved historical board values.

The board targets:

- Mega Rock: 1/1
- Large Cap: 11/11
- Small/Mid: 11/11

Real-data market-cap buckets:

- Mega Rock: market cap at or above $1T.
- Large Cap: $10B to below $1T.
- Small/Mid: below $10B.

If any section is incomplete, the board and report show a data quality warning.

Draft reports, current scans, staging changes, and unapproved workflows do not update the board. If no approved report exists, Atlas shows an intentional empty state. If the newest approved report cannot be parsed safely, Atlas preserves the last valid approved board when one exists and surfaces a warning.

Finviz links are plain outbound reference links in the format:

```text
https://finviz.com/quote.ashx?t=<TICKER>
```

The Picks Board does not publish, email, distribute, or bypass approval.

## Universe Manager and Population Scanner Data Source

Universe Manager owns Atlas research populations. Population = broad provider source. Master Universe = duplicate-safe merge of expanded QQQ, S&P 500, Russell-style small-cap, Micro/Moonshot, and Personal Watchlist providers. Watchlist = manually curated list. Report picks = final staged output in the approval-gated report workflow.

Atlas Research Pipeline:

```text
Universe Providers -> Universe Builder -> Master Universe -> Evidence Engine -> Ranking Engine -> Staging -> GreenRock Reports
```

Local population files:

- `.atlas/output/greenrock/populations/qqq.csv`
- `.atlas/output/greenrock/populations/sp500.csv`
- `.atlas/output/greenrock/populations/russell2000.csv`
- `.atlas/output/greenrock/populations/micro_moonshot.csv`
- `.atlas/output/greenrock/watchlists/*.csv`
- `.atlas/output/greenrock/universes/*.csv`
- `.atlas/output/atlas/research/master_universe.csv`

The Micro/Moonshot population is editable and includes non-index-style names such as GRRR, PI, ENPH, NIO, SOFI, RKT, AFRM, OPEN, FUBO, CHPT, MARA, RIOT, HOOD, UPST, DKNG, LC, LMND, RUN, STEM, and ENVX.

Population scans use the configured real market-data provider and write:

- `.atlas/output/greenrock/scans/<scan_id>/scan_results.csv`
- `.atlas/output/greenrock/scans/<scan_id>/scan_summary.md`

`atlas greenrock scan --population all` scans the Master Universe. QQQ, S&P 500, Russell 2000, and Micro/Moonshot remain individually scannable.

Scan rows include GreenRock Score, Confidence, Evidence Agreement, Fundamental Guardrail, Research Priority, Rank, Percentile, Universe Membership, Market Archetype, market-cap bucket, top bullish signal, top caution signal, data-quality warnings, and Finviz links. Scan summaries include total configured tickers, fetched/scored tickers, skipped tickers, provider failures, duplicates removed, and ranked count. Scans are local research outputs and do not create report approvals, emails, publications, or client-facing materials.

Successful scans also write provider-failure health metadata when available:

- `.atlas/output/greenrock/scans/<scan_id>/scan_failures.csv`

Older scans without `scan_failures.csv` are still readable; Atlas infers failed tickers by comparing configured universe tickers against ranked scan rows.

The browser discovery flow is:

1. Universe Providers: QQQ, S&P 500, Russell 2000, Micro/Moonshot, and Personal Watchlists.
2. Universe Builder: merge sources, remove duplicates, classify buckets, and track provider health.
3. Master Universe: source of truth for `--population all`.
4. Scanner: local discovery engine.
5. Ranking Engine: rank candidates with score, confidence, agreement, guardrail, priority, rank, percentile, membership, and archetype.
6. Market Pulse: review ranked opportunities by Mega, Large, Mid, Small, Micro, Meme, and Special Situation.
7. Staging: curated bridge into report generation.
8. Report: later approval-gated publication draft.

Market Pulse and archetype audits derive missing archetypes for older scan CSVs from ticker, market cap, and universe membership, so successful historical scans remain useful.

Promotion from scans writes only to local list storage and optional local metadata:

- `.atlas/output/greenrock/watchlists/<list>.csv`
- `.atlas/output/greenrock/universes/<bucket>.csv`
- `.atlas/output/greenrock/watchlists/promotion_metadata.csv`

Promotion metadata records ticker, destination list, scan ID, score, confidence, Evidence Agreement, Research Priority, Fundamental Guardrail, and promoted timestamp. It is a local research organization aid, not a report, approval, email, publication, or client artifact.

## Score Calculator Data Source

The GreenRock Score Calculator at `/greenrock/score` and `atlas greenrock score <ticker>` is real-data-only for operators. It fetches only the requested ticker through the configured provider and fails closed if the provider is unavailable.

When the provider is not configured, the browser Score Calculator shows a neutral setup card with provider status and the setup command. This is normal setup state, not a workflow failure.

Configure locally:

```bash
export ATLAS_MARKET_DATA_PROVIDER=yfinance
python3 -m pip install -e ".[market-data]"
atlas greenrock score AAPL
atlas greenrock score-audit AAPL
```

The calculator does not create workflow runs, reports, approvals, artifacts, emails, publications, or distribution actions.

Use `atlas greenrock score-audit <ticker...>` to verify a score against the local data path. The audit prints provider/source, raw technical inputs, component weights, evidence contributions, capped guardrail adjustment, data-quality warnings, Confidence, Evidence Agreement, and whether the Score Calculator, latest Market Pulse scan row, staging enrichment row, and latest report candidate row agree. A mismatch is reported as `Score path mismatch detected.`

The canonical score path is shared by the Score Calculator, population scanner/Market Pulse, staging enrichment, and report candidate data. Population scans now store the same adjusted GreenRock Score and Confidence that the calculator previews for the same provider data.

If the operator explicitly saves a scored ticker to a GreenRock list, Atlas writes only to local CSV storage. Subscriber-style lists are stored under `.atlas/output/greenrock/watchlists/`, and bucket lists reuse `.atlas/output/greenrock/universes/`.

Score components:

- 52-week low proximity.
- Bollinger Band setup.
- RSI.
- Volume acceleration.
- Moving average structure.
- Bullish / Bearish Evidence for setup support and research cautions.

Fundamental Guardrails:

- Strong Balance Sheet.
- Acceptable.
- Caution.
- Red Flag.
- Insufficient Data.

Guardrails use available cash and equivalents, total debt, net cash/debt, quick ratio or liquidity inputs, and current versus prior shares outstanding. They are light viability checks, not full valuation analysis. They primarily affect GreenRock Confidence; the GreenRock Score remains technical-first with only a small capped guardrail adjustment. Strong fundamentals can support a recovery thesis, but they do not guarantee reversion. Missing fundamental data lowers evidence confidence but does not automatically imply weakness.

Do not commit real credentials or private data. The yfinance scaffold does not require an API key, but any future paid provider settings must stay local.

## Safety Rules

- No client files.
- No IBKR trading.
- No Gmail.
- No email sending.
- No auto-publishing.
- No recommendations sent anywhere.
- Human approval remains mandatory for every report draft.

Real-data reports are still drafts. They must not be used client-facing until a human approves the linked Atlas OS approval record and the appropriate review process is complete.
