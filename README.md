# Atlas OS

Atlas OS is a local-first Python scaffold for multi-division AI workflows with human approval gates.

The first implementation target is the GreenRock Analysts Monthly Report. Phase 0 uses mock/sample data only and does not connect to market data, email, client files, brokerage accounts, OneDrive, Gmail, or credential stores.

## Local Install

```bash
cd ~/Desktop/atlas-os
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Offline Local Run

If the editable install cannot fetch Python build tools, use the included local launcher:

```bash
cd ~/Desktop/atlas-os
export PATH="$PWD/bin:$PATH"
atlas --help
atlas status
atlas morning-brief
atlas greenrock sample-report
```

## CLI

```bash
atlas --help
atlas status
atlas greenrock sample-report
atlas greenrock run-screen
atlas greenrock run-screen --data mock
atlas greenrock run-screen --data real --selection ranked
atlas greenrock candidates
atlas greenrock report-draft
atlas greenrock report-draft --data mock
atlas greenrock report-draft --data real
atlas greenrock report-draft --data real --selection ranked
atlas greenrock report-draft --data real --selection strict
atlas greenrock latest-report
atlas greenrock latest-report --print
atlas greenrock latest-run
atlas greenrock latest-candidates
atlas greenrock picks-board
atlas greenrock score AAPL
atlas greenrock review
atlas greenrock open-latest
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
atlas greenrock open-pdf <approval_id>
atlas greenrock cleanup-drafts
atlas greenrock cleanup-drafts --dry-run
atlas greenrock universe list
atlas greenrock universe add TSLA PLTR
atlas greenrock universe remove TSLA
atlas greenrock universe reset-mega-rock
atlas greenrock universe reset-large-cap
atlas greenrock universe reset-small-mid
atlas greenrock universe reset-all
atlas greenrock universe validate
atlas approvals list
atlas approvals pending
atlas approvals latest
atlas approvals show 1
atlas approvals approve 1
atlas approvals reject 1
atlas dashboard
atlas serve
atlas runs list
atlas runs show <run_id>
atlas artifacts list
atlas artifacts show <artifact_id>
atlas audit list
atlas audit show <audit_id>
```

You can also run the CLI without installing the console script:

```bash
python -m atlas_os.cli --help
python -m atlas_os.cli greenrock sample-report
python -m atlas_os.cli greenrock run-screen
```

## GreenRock Local Screening

GreenRock screening defaults to mock data and calculates SMA, EMA, RSI, 2.5 standard deviation Bollinger Bands, 52-week low proximity, 10-day average volume trend, and moving average rate of change.

```bash
atlas greenrock run-screen --data mock
atlas greenrock candidates
atlas greenrock report-draft --data mock
```

The local screener writes:

- `.atlas/output/greenrock/<run_id>/greenrock_candidates.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_mega_rock.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_large_cap.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_small_cap.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_report_draft.md`

Draft reports remain local, approval-gated, and blocked from client-facing use. Mock mode is the default.

## Atlas Research Pipeline and Universe Manager

Universe Manager owns research populations before GreenRock scanning, staging, and reporting:

```text
Universe Providers -> Universe Builder -> Master Universe -> Evidence Engine -> Ranking Engine -> Staging -> GreenRock Reports
```

Providers currently include expanded QQQ, S&P 500, Russell-style small-cap, Micro/Moonshot, and Personal Watchlists. They merge into a duplicate-safe Master Universe stored locally at `.atlas/output/atlas/research/master_universe.csv`. The browser page is available at `/greenrock/universe`.

Atlas Market Engine also classifies scan candidates by archetype: Mega, Large, Mid, Small, Micro, Meme, and Special Situation. Market Pulse is available at `/greenrock/market-pulse`.

Population scans are broader research screens sourced from Universe Manager, separate from curated report picks.

```bash
atlas greenrock population reset-all
atlas greenrock population list
atlas greenrock population master
atlas greenrock market-pulse
atlas greenrock stage-from-market-pulse --overwrite-staging
atlas greenrock report-from-market-pulse --overwrite-staging
atlas greenrock stage-analyst-slate --overwrite-staging
atlas greenrock report-analyst-slate --overwrite-staging
atlas greenrock memory summary
atlas greenrock memory ticker SOFI
atlas greenrock memory movers
atlas morning-brief
atlas greenrock archetypes audit
atlas greenrock universe health
atlas greenrock universe cleanup-failures --dry-run
atlas greenrock population add micro_moonshot GRRR PI
atlas greenrock population validate
atlas greenrock scan --population all
atlas greenrock scan --population micro_moonshot
atlas greenrock scan-promote <scan_id> SOFI --list watchlist
atlas greenrock staging add SOFI --bucket small_mid
atlas greenrock staging ready
atlas greenrock report-from-staging --allow-underfilled
```

Populations are stored locally under `.atlas/output/greenrock/populations/`:

- `qqq.csv`
- `sp500.csv`
- `russell2000.csv`
- `micro_moonshot.csv`

Scan outputs are stored under `.atlas/output/greenrock/scans/<scan_id>/` as `scan_results.csv` and `scan_summary.md`. Scans require the configured real provider, fail safely when it is unavailable, and do not create reports, approvals, emails, or publications. The preferred GreenRock report path is now: Scan, Stage, Generate Draft, Approve, Export PDF.

Scan rows are ranked candidates with GreenRock Score, Confidence, Evidence Agreement, Guardrail, Research Priority, Rank, Percentile, Universe Membership, and Market Archetype. Scan summaries show total configured tickers, fetched/scored tickers, skipped tickers, provider failures, duplicates removed, and ranked count.

Universe Manager UI uses filters and pagination for the Master Universe instead of showing only the first alphabetical sample. Filter by provider, market-cap bucket, archetype, or ticker search. Provider failures from the latest successful scan are summarized separately so stale/delisted/acquired names do not dominate the page. Cleanup is dry-run by default; confirmed cleanup only removes failed tickers from editable local population seed CSVs.

Market Pulse can stage a report slate from the latest successful scan with `atlas greenrock stage-from-market-pulse` or the `/greenrock/market-pulse` button. It selects the top 1 Mega, top 11 Large, and top 11 combined Mid/Small/Micro candidates, preserving score, confidence, Evidence Agreement, Guardrail, Research Priority, top signals, scan ID, and source into staging. Existing staging is never replaced without explicit overwrite/confirmation.

Scanner actions can either save a ticker into Watchlist, Ranked Candidates, Strict Review, Mega Rock Candidate Pool, Large Cap Watchlist, or Small/Mid Watchlist, or stage selected scan rows directly into the final report slate. Promotion and staging are duplicate-safe, show market-cap bucket warnings where applicable, and write only to local GreenRock CSVs.

Bucket-specific saves are guarded by available market-cap bucket data: mismatched Mega Rock, Large Cap, or Small/Mid saves are blocked with a suggested destination. Personal Watchlist is the manual fallback for unknown or intentionally tracked names.

Report Candidate Staging is the next local curation layer:

```bash
atlas greenrock staging list
atlas greenrock staging add SOFI --bucket small_mid
atlas greenrock staging move SOFI --bucket research
atlas greenrock staging remove SOFI
atlas greenrock staging ready
atlas greenrock staging enrich
atlas greenrock report-from-staging --allow-underfilled
```

Staging stores candidates locally at `.atlas/output/greenrock/staging/report_candidates.csv` with bucket, source, score, confidence, Evidence Agreement, Guardrail, Research Priority, top signals, timestamp, and operator notes. Staging does not create reports, approvals, PDFs, emails, publications, or client-facing artifacts.

Staged candidates should have analytics before clean report generation. Use `atlas greenrock staging enrich` to refresh missing Score, Confidence, Evidence Agreement, Guardrail, Research Priority, and top signal fields from the configured real provider. `atlas greenrock staging ready` reports both section fill status and analytics completeness.

`atlas greenrock report-from-staging` creates the preferred approval-gated GreenRock draft from staged candidates. It blocks underfilled sections by default; use `--allow-underfilled` to generate a draft that clearly shows readiness warnings. Missing analytics are a separate gate: run `atlas greenrock staging enrich` first, or use `--allow-missing-analytics` only for an intentional draft with explicit data warnings. Scanner populations do not automatically feed reports: staged candidates are the curated bridge. Browser review is available at `/greenrock/reports/<run_id>/review`.

`atlas greenrock report-from-market-pulse` is the one-command version of the same gated path: latest scan -> Market Pulse selection -> staging -> normal workflow run -> pending approval -> Review Center. It does not email, publish, trade, create client files, or export a PDF automatically.

Atlas Analyst is the deterministic intelligence layer for staging-sourced reports. GreenRock Score remains the branded score; Atlas Analyst explains rank context, archetype, confidence, Evidence Agreement, prior-scan changes when available, primary bullish/caution evidence, and what to watch next. `atlas greenrock stage-analyst-slate` stages one leader from each available archetype, then fills the remaining report slate by rank. `atlas greenrock report-analyst-slate` uses that slate and creates the same approval-gated draft workflow.

Atlas Memory stores local Market Pulse scan history under `.atlas/output/greenrock/memory/`. After each successful population scan, Atlas records per-ticker rank, percentile, GreenRock Score, Confidence, Evidence Agreement, Research Priority, Guardrail, archetype, signals, provider membership, population, and data source. Memory powers ticker movement, biggest movers, the Market Pulse “Atlas Memory: What Changed” card, Score Calculator “Atlas Memory Snapshot,” Market Pulse “What Changed Since Last Scan,” and Atlas Analyst prior-scan summaries. It is local research context only.

The Atlas Morning Brief is the Command Center attention layer. Open `/atlas/morning-brief` or run `atlas morning-brief` to see latest scan status, universe size, scored and skipped counts, high-confidence and research-priority counts, new archetype leaders, top movers, pending approvals, PDF readiness, and Atlas Inbox-style action items. The page includes action buttons for latest Market Pulse, pending approvals, latest report review, Analyst Slate staging, staging draft generation when ready, and final PDF archive. It does not email, publish, trade, create client files, export PDFs, or call external LLM/API services.

GreenRock branding uses the local asset path `atlas_os/static/greenrock_logo.png`. Atlas OS branding can use `atlas_os/static/atlas_logo.png`; if it is missing, browser title pages show a fallback Atlas mark and report/PDF generation continues without failing.

## GreenRock Score Calculator

The GreenRock Score Calculator is available in the browser at `/greenrock/score` and from the CLI:

```bash
export ATLAS_MARKET_DATA_PROVIDER=yfinance
python3 -m pip install -e ".[market-data]"
atlas greenrock score AAPL
```

It is preview-only. It does not create reports, approval records, artifacts, emails, publications, or external distribution actions.

The calculator is real-data-only for operators. It shows GreenRock Score, GreenRock Confidence, signal label, research priority, analyst summary with a Finviz button, Bullish Evidence, Bearish Evidence, What to Watch Next, Finviz link, data-quality warnings, All-Time High, and +2/+3/+5/+7 one-year statistical price targets. Real mode requires the configured local market data provider.

GreenRock Score measures the opportunity/dislocation setup. GreenRock Confidence measures evidence reliability based on data completeness, data depth, indicator agreement, volatility/noise, market-cap bucket reliability, and target reliability. Confidence may be high when Score is moderate if evidence is clean, and low when Score is high if the data is shallow or conflicted. Research Priority is a local analyst workflow label: Immediate Review, This Week, Interesting, Monitor, or Ignore.

## GreenRock Real Data Mode

Phase 4A/4C adds a production-shaped yfinance market data adapter and local ticker watchlist management while keeping mock mode as the default.

```bash
atlas greenrock report-draft --data real
atlas greenrock report-draft --data real --selection ranked
atlas greenrock report-draft --data real --selection strict
```

Real mode fails safely unless a provider is configured locally. A failed configuration attempt does not create a report, approval, artifact, email, publication, or external action.

Selection mode defaults to `strict` for mock data and `ranked` for real data. Strict mode requires all GreenRock criteria. Ranked mode scores the available watchlists and fills the best available candidates when strict criteria would leave sections empty.

Optional first provider:

```bash
python3 -m pip install -e ".[market-data]"
```

Then configure local-only placeholders in `.env` or your shell:

```text
ATLAS_GREENROCK_DEFAULT_DATA_MODE=mock
ATLAS_MARKET_DATA_PROVIDER=yfinance
ATLAS_GREENROCK_REAL_TICKERS=
```

When `ATLAS_GREENROCK_REAL_TICKERS` is blank, real mode uses the local GreenRock watchlist CSVs stored under `.atlas/output/greenrock/universes/`:

- `mega_rock.csv`
- `large_cap.csv`
- `small_mid_cap.csv`

Browser watchlist pages also include Watchlist and Personal Watchlist CSVs under `.atlas/output/greenrock/watchlists/`. `/greenrock/watchlists` supports safe manual removal with confirmation.

Manage the watchlists locally:

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

## GreenRock Picks Board

The Picks Board is a local dashboard view for the latest GreenRock report run:

```bash
atlas greenrock picks-board
atlas serve
```

Then open:

```text
http://127.0.0.1:8000/greenrock/picks
```

The board displays one featured Mega Rock pick, eleven large-cap picks, and eleven small/mid-cap picks when available. Current report mode ranks configured watchlists; population scans are available separately and do not replace the report workflow yet. It includes ticker, company name, market cap, price, GreenRock Score, Evidence Agreement, top bullish signal, top caution signal, signal label, concise Fundamental Guardrail fields, RSI, 52-week low distance, Bollinger Band status, volume acceleration, screening rationale, and Finviz links. The page is local-only, clearly labels MOCK or REAL data, and does not publish externally.

## GreenRock Score Calculator

Preview a single ticker score without creating a workflow run, report, approval, artifact, email, or publication:

```bash
atlas greenrock score AAPL
```

In the Command Center, open:

```text
http://127.0.0.1:8000/greenrock/score
```

The calculator shows GreenRock Score, GreenRock Confidence, Evidence Agreement, Fundamental Guardrails, signal label, research priority, analyst summary, Bullish Evidence, Bearish Evidence, neutral/watch items, What to Watch Next, Finviz link, data-quality warnings, and one-year statistical price targets. The methodology is documented in [GreenRock Score Methodology](docs/GREENROCK_SCORE_METHODOLOGY.md). Real mode requires the configured market data provider and fails safely if unavailable.

The GreenRock Evidence Engine structures each existing signal with a category, direction, strength, numeric contribution, and plain-English explanation. Evidence Agreement measures whether the available technical, fundamental, and data-quality signals align or conflict. What to Watch Next lists validation items such as moving-average reclaim, RSI improvement, volume continuation, recent-low support, and statistical target context. These are local research aids only and are not investment advice or a price forecast.

Fundamental Guardrails are light survivability checks, not a full valuation model. They review net cash/debt, quick ratio, and share-count change where available. They primarily affect GreenRock Confidence; GreenRock Score remains technical-first with only a small capped `fundamental_guardrail_adjustment`.

Real-data market-cap sections are:

- Mega Rock: market cap at or above $1T.
- Large Cap: $10B to below $1T.
- Small/Mid: below $10B.

Reports clearly state `Data Mode: MOCK` or `Data Mode: REAL` and show the data source, such as `yfinance:greenrock_watchlists`. Real-data reports are still draft-only and blocked until human approval. If a section produces fewer than its target picks, the report and Picks Board show a data quality warning. For staging-sourced reports, missing analytics are treated separately from underfilled sections and should be resolved with `atlas greenrock staging enrich`.

## Approval Queue

GreenRock screening, report-draft, and staging-sourced report commands persist a local workflow run to SQLite, store artifact records, and create a pending approval for the draft report.

```bash
atlas greenrock report-draft
atlas greenrock report-from-staging --allow-underfilled
atlas approvals list
atlas approvals show <id>
atlas approvals approve <id>
atlas approvals reject <id>
```

Report drafts remain blocked from client-facing use while their approval status is `pending` or `rejected`.

## Run Inspection

Workflow runs persist step states, artifacts, report records, and audit log entries in local SQLite.

```bash
atlas runs list
atlas runs show <run_id>
atlas artifacts list
atlas artifacts show <artifact_id>
atlas audit list
atlas audit show <audit_id>
```

GreenRock report drafting uses step states of `initialized`, `running`, `completed`, `failed`, and `blocked_for_approval`. The draft report step remains blocked until the linked approval is approved.

## GreenRock Report Draft

`atlas greenrock report-draft` creates a professional mock GreenRock Analysts Monthly Report draft in the run-specific output folder:

```text
.atlas/output/greenrock/<run_id>/greenrock_report_draft.md
```

The draft includes an executive summary, methodology, large-cap and small-cap candidate tables, per-name screening rationale, risk notes, human approval language, mock-data language, and compliance-friendly disclaimers. It avoids guarantees and direct personalized recommendations.

## Analyst Shortcuts

These commands provide quick review views without needing to inspect raw database records:

```bash
atlas greenrock latest-report
atlas greenrock latest-report --print
atlas greenrock latest-run
atlas greenrock latest-candidates
atlas greenrock review
atlas greenrock open-latest
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
atlas greenrock open-pdf <approval_id>
atlas approvals pending
atlas approvals latest
atlas dashboard
```

`latest-report` finds the newest GreenRock report by workflow run timestamp. `latest-candidates` summarizes the latest run-specific large-cap and small/mid-cap CSV files. `review` shows the latest run, report path, approval status, top candidates, and pending approval ID. `open-latest` opens the latest report on macOS or prints the path on unsupported systems. `dashboard` shows recent runs, pending approvals, artifact counts, and the latest GreenRock report path.

## Approved PDF Export

PDF export is allowed only after the linked report approval is approved:

```bash
atlas approvals approve <approval_id>
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
atlas greenrock open-pdf <approval_id>
```

Approved exports are saved beside the Markdown report:

```text
.atlas/output/greenrock/<run_id>/greenrock_report_final.pdf
```

Repeated PDF exports update/reuse the same PDF path and do not create duplicate `report_final_pdf` artifact records for the same run.

Approved GreenRock PDFs include a branded cover page with GreenRock logo when available, Atlas OS mark/logo when available, report title, date, data mode, candidate source, and approval/status disclaimer. PDF export remains blocked until approval.

## Final Approved Packet

Use the final packet view after approval to confirm the report, PDF, artifacts, and safety status:

```bash
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
```

Pending or rejected approvals are not treated as final packets. Use `atlas greenrock open-pdf <approval_id>` to open an exported approved PDF on macOS, or to see the next step if no PDF exists yet.

`atlas dashboard` also shows the latest GreenRock approval status, final PDF status, and final PDF path when exported.

## Report Lifecycle Cleanup

Use cleanup after final PDFs are exported or after several draft runs have accumulated:

```bash
atlas greenrock cleanup-drafts --dry-run
atlas greenrock cleanup-drafts
```

Cleanup keeps the latest GreenRock draft run/artifacts and preserves all approved final PDFs. Older draft Markdown and CSV files are removed locally and their artifact records are marked archived. Approval records and audit logs are preserved.

## Atlas Command Center

Phase 3B upgrades the local browser dashboard into Atlas Mission Control:

```bash
atlas serve
```

Then open:

```text
http://127.0.0.1:8000
```

The home page is the Atlas Inbox. It is designed to answer: what needs attention now?

Command Center pages:

- `/` Atlas Inbox with branded Command Center title, attention counters, actionable cards, recent workflow feed, and navigation.
- `/projects` project directory for GreenRock Analysts, Variance Capital / The Bat Signal, GreenRock Insurance, and Atlas Core.
- `/greenrock` report review console with the preferred Generate Draft From Staging path, legacy/sample report buttons, latest run/report/PDF status, candidate summaries, approval actions, local artifact open links, and PDF export after approval.
- `/greenrock/reports/<run_id>/review` GreenRock Report Review Center with branded title page, report metadata, source disclosure, candidate tables, evidence notes, approval controls, and approved-only PDF controls.
- `/greenrock/discovery` guided GreenRock discovery workflow showing Discovery Scan, Review Results, Stage Candidates, Generate Draft Report, Human Approval, and Export PDF.
- `/greenrock/picks` GreenRock Picks Board with the featured Mega Rock pick, 11 large-cap picks, 11 small/mid-cap picks, Evidence Agreement, top signals, Fundamental Guardrail fields, Finviz links, and explicit data-mode labeling.
- `/greenrock/scanner` GreenRock Market Scanner for population scans, latest scan metadata, quick filters, ranked results, Finviz links, deliberate promote-to-list review, and direct scan-to-staging.
- `/greenrock/market-pulse` latest successful scan overview by archetype, with one-click staging of top Market Pulse candidates, Atlas Analyst report slate generation, and approval-gated draft generation from staging.
- `/atlas/morning-brief` branded operator summary of latest scan health, Atlas Memory movers, pending approvals, PDF readiness, action buttons, and linked Atlas Inbox items.
- `/greenrock/watchlists` local GreenRock watchlist overview with ticker counts, tickers, Finviz links, promotion source, and latest promoted timestamp when available.
- `/greenrock/staging` Report Candidate Staging page for final local curation into Mega Rock, Large Cap, Small/Mid, Research Only, and Excluded buckets before approval-gated report drafts.
- `/greenrock/score` GreenRock Score Calculator with confidence, Evidence Agreement, Fundamental Guardrails, research priority, evidence cards, watch-next notes, methodology explanation, and preview-only score breakdown.
- `/greenrock/final-reports` final PDF archive for approved exported GreenRock PDFs.
- `/tasks` local kanban-style manual task board with backlog, in progress, awaiting review, and completed columns.
- `/agents` planned agent HUD with inactive/planned status labels.
- `/reports` local report and artifact index with links into the GreenRock Report Review Center.

Browser approval/rejection actions require a confirmation page before updating local SQLite records. PDF export remains blocked until the linked report approval is approved.

The web app is local development mode only. It uses mock data, keeps the human approval gate mandatory, and does not include publish, send, email, external API, or credential controls.

## Using Atlas Command Center

```bash
atlas serve
```

Open `http://127.0.0.1:8000`, review the Atlas Inbox, then use the GreenRock Report Review Center at `/greenrock/reports/<run_id>/review` to inspect draft metadata, source disclosure, candidates, approval state, and PDF controls. Use `http://127.0.0.1:8000/greenrock/discovery` for the preferred Scan, Stage, Generate Draft, Approve, Export PDF workflow, `http://127.0.0.1:8000/greenrock/scanner` for population discovery, `http://127.0.0.1:8000/greenrock/watchlists` for local research queues, `http://127.0.0.1:8000/greenrock/staging` for report candidate staging and approval-gated draft generation, and `http://127.0.0.1:8000/greenrock/picks` for the GreenRock Picks Board. Use the task board for manual operator tasks only; it does not trigger autonomous execution.

## Operator Docs

- [Operator Runbook](docs/OPERATOR_RUNBOOK.md)
- [GreenRock Product Notes](docs/GREENROCK_PRODUCT_NOTES.md)
- [GreenRock Score Methodology](docs/GREENROCK_SCORE_METHODOLOGY.md)
- [Monthly Report Release Checklist](docs/MONTHLY_REPORT_RELEASE_CHECKLIST.md)
- [Data Sources](docs/DATA_SOURCES.md)

## Tests

```bash
python3 -m unittest discover
```

## Safety Rule

Atlas OS must not publish, email, distribute, or otherwise release client-facing material without explicit human approval. Mock mode remains the default; real-data mode is optional, local, and still approval-gated.

## Documentation

Planning documents live in [docs/README.md](docs/README.md).
