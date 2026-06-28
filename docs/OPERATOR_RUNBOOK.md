# Atlas OS Operator Runbook

## Generate a GreenRock Report

```bash
cd ~/Desktop/atlas-os
export PATH="$PWD/bin:$PATH"
atlas greenrock report-draft
```

The report is written to:

```text
.atlas/output/greenrock/<run_id>/greenrock_report_draft.md
```

The default workflow uses mock data and creates a pending approval record.

To make the data mode explicit:

```bash
atlas greenrock report-draft --data mock
```

## Attempt Real Data Mode Safely

Real mode is optional and fail-closed.

```bash
atlas greenrock report-draft --data real
atlas greenrock report-draft --data real --selection ranked
atlas greenrock report-draft --data real --selection strict
```

If no provider is configured, Atlas prints a blocked message and does not create a report, approval, artifact, email, publication, or external action.

Optional local yfinance setup:

```bash
python3 -m pip install -e ".[market-data]"
export ATLAS_MARKET_DATA_PROVIDER=yfinance
export ATLAS_GREENROCK_REAL_TICKERS=
atlas greenrock report-draft --data real
```

With `ATLAS_GREENROCK_REAL_TICKERS` blank, Atlas uses the local Mega Rock candidate pool, large-cap watchlist, and small/mid-cap watchlist.

Real mode defaults to ranked selection, which scores available names and fills the best available candidates when strict criteria leave sections short. Strict mode remains available for comparison and may return fewer picks.

Real-data reports still remain draft-only and blocked for human approval.

## Manage GreenRock Watchlists

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

The watchlists are local only and stored at:

- `.atlas/output/greenrock/universes/mega_rock.csv`
- `.atlas/output/greenrock/universes/large_cap.csv`
- `.atlas/output/greenrock/universes/small_mid_cap.csv`

## Inspect Candidates

```bash
atlas greenrock latest-candidates
atlas greenrock picks-board
atlas greenrock score AAPL
atlas greenrock review
```

Use `latest-candidates` for a quick large-cap and small/mid-cap summary. Use `picks-board` for the latest 23-slot Picks Board summary and browser URL. Use `review` for latest run status, report path, approval status, and top candidate names.

## Open GreenRock Picks Board

```bash
atlas serve
```

Open:

```text
http://127.0.0.1:8000/greenrock/picks
```

The Picks Board shows one featured Mega Rock pick, eleven large-cap picks, and eleven small/mid-cap picks when available. It includes section counts, Finviz links, GreenRock Scores, signal labels, technical fields, and a clear mock/real data badge. Current real mode ranks configured watchlists, not the entire U.S. public market; a full-market scanner is planned. If a section is incomplete, Atlas shows a data quality warning. It is local-only and not a publishing surface.

Real-data buckets are Mega Rock at $1T+, large cap from $10B to below $1T, and small/mid below $10B.

## Score Any Ticker

CLI:

```bash
export ATLAS_MARKET_DATA_PROVIDER=yfinance
python3 -m pip install -e ".[market-data]"
atlas greenrock score AAPL
```

Browser:

```text
http://127.0.0.1:8000/greenrock/score
```

The score calculator is real-data-only for operators and preview-only unless you explicitly save the ticker to a local GreenRock list. It shows GreenRock Score, GreenRock Confidence, Fundamental Guardrails, signal label, research priority, analyst summary, Bullish Evidence, Bearish Evidence, What to Watch Next, data quality warnings, explained component scores, All-Time High, +2/+3/+5/+7 one-year statistical price targets, and a Finviz link. The methodology is documented in `docs/GREENROCK_SCORE_METHODOLOGY.md`. Calculating a score does not create a report, approval, artifact, email, publication, or external distribution action.

GreenRock Score measures opportunity/dislocation. GreenRock Confidence measures evidence reliability using data completeness, data depth, indicator agreement, volatility/noise, bucket reliability, and target reliability. Confidence can be high when Score is moderate, and it can be low when Score is high but the evidence is shallow or conflicted. Research Priority is a local review label only: Immediate Review, This Week, Interesting, Monitor, or Ignore.

Fundamental Guardrails are light survivability checks. They use available net cash/debt, quick ratio, and share-count change data to label Strong Balance Sheet, Acceptable, Caution, Red Flag, or Insufficient Data. Fundamentals primarily affect Confidence; the technical GreenRock Score receives only a small capped adjustment. Missing fundamental data lowers evidence confidence but is not treated as automatic business weakness.

The browser page includes a separate Save Ticker to List control for Watchlist, Ranked Candidates, Strict Review, Mega Rock Candidate Pool, Large Cap Watchlist, and Small/Mid Watchlist. Saving is local only, ignores duplicate tickers in the same list, and shows a warning when available market cap does not fit the selected bucket.

If the provider is missing, Atlas shows setup instructions and leaves workflow state unchanged:

```bash
export ATLAS_MARKET_DATA_PROVIDER=yfinance
python3 -m pip install -e ".[market-data]"
```

In the browser, the applicable rank band displays the current ticker score in parentheses. Statistical targets below the All-Time High are styled pink, while targets above the All-Time High are styled GreenRock green. If All-Time High or standard deviation data cannot be calculated cleanly, Atlas shows a data-quality warning instead of a target table.

## Open Latest Report

```bash
atlas greenrock latest-report
atlas greenrock open-latest
```

On macOS, `open-latest` opens the Markdown report file. On unsupported systems, Atlas prints the report path.

## Review Approval Queue

```bash
atlas approvals pending
atlas approvals latest
atlas approvals show <approval_id>
```

Report drafts remain blocked while pending or rejected.

## Approve or Reject

```bash
atlas approvals approve <approval_id>
atlas approvals reject <approval_id>
```

Only approve after human review. Approval updates the linked workflow run and report status.

## Export Approved PDF

PDF export is blocked until the linked approval is approved.

```bash
atlas approvals approve <approval_id>
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
```

The approved PDF is saved to the same run folder as the Markdown report:

```text
.atlas/output/greenrock/<run_id>/greenrock_report_final.pdf
```

The export creates a `report_final_pdf` artifact record. Do not export pending or rejected reports for client-facing use.

PDF export is idempotent for a run: rerunning export updates/reuses `greenrock_report_final.pdf` and does not create duplicate `report_final_pdf` artifact records.

## Review Final Packet

```bash
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
```

The final packet shows approval status, approval timestamp, run ID, Markdown path, PDF path if exported, artifact list, mock-data disclaimer, and human approval confirmation. Pending or rejected approvals are clearly marked as not final.

## Open Approved PDF

```bash
atlas greenrock open-pdf <approval_id>
```

This opens the exported PDF on macOS only when the approval is approved and the PDF artifact exists. If no PDF exists, Atlas prints the export command to run next.

## Clean Up Older Drafts

Preview cleanup first:

```bash
atlas greenrock cleanup-drafts --dry-run
```

Run cleanup:

```bash
atlas greenrock cleanup-drafts
```

Cleanup preserves the latest GreenRock draft run/artifacts and every approved final PDF. Older draft Markdown and CSV files are removed locally and their artifact records are marked archived. Approval records and audit logs are not deleted.

## Dashboard Final Status

```bash
atlas dashboard
```

The dashboard shows the latest GreenRock report status, approval status, final PDF status, and final PDF path when exported.

## Using Atlas Command Center

```bash
atlas serve
```

Open `http://127.0.0.1:8000` in a browser.

The home page is the Atlas Inbox. Start there to see what needs attention now:

- Pending approvals.
- Reports ready for PDF export.
- Completed workflows.
- Failed workflows.
- Manual tasks awaiting action.
- Clearly labeled placeholders for future Insurance, Bat Signal, and critique workflows.

Use the navigation cards for:

- Project Directory.
- GreenRock Analysts.
- Task Board.
- Agent Monitor.
- Approvals.
- Artifacts / Reports.

On the GreenRock page, use **Run Mock Report** to generate a default mock-data draft or **Run Real Report** to attempt the configured real-data provider. Both buttons create a pending approval only after the workflow succeeds and neither button bypasses human review.

For browser real mode:

```bash
python3 -m pip install -e ".[market-data]"
export ATLAS_MARKET_DATA_PROVIDER=yfinance
export ATLAS_GREENROCK_REAL_TICKERS=
atlas serve
```

Open `http://127.0.0.1:8000/greenrock`, then click **Run Real Report**. With `ATLAS_GREENROCK_REAL_TICKERS` blank, Atlas uses the local GreenRock watchlists. If the provider is not configured, the browser shows a blocked message and no run, artifact, report, or approval is created.

Open `http://127.0.0.1:8000/greenrock/final-reports` to review the final PDF archive.

## Approve or Reject in Browser

Open `http://127.0.0.1:8000/greenrock`.

Pending report approvals show approve/reject buttons. Each button opens a confirmation page before any local record is changed. Approval only updates the local approval queue and unlocks local final packet/PDF work; it does not publish, send, email, or distribute anything.

PDF export from the browser is available only after approval. It creates or reuses the run-specific `greenrock_report_final.pdf`.

## Open Reports and PDFs

On the GreenRock and Reports pages, use the local open links for Markdown reports, CSV artifacts, and approved PDFs. On macOS, Atlas attempts to open the file locally. On unsupported systems, keep using the displayed path.

## Manage Manual Tasks

Open `http://127.0.0.1:8000/tasks`.

Manual tasks can be created with a title, division, notes, and a local state. The kanban columns are backlog, in progress, awaiting review, and completed. This board is an operator placeholder only and does not trigger autonomous execution.

## Review Planned Agents

Open `http://127.0.0.1:8000/agents`.

The monitor lists planned agents as inactive placeholders until they are explicitly implemented.

## Commit and Push Changes

```bash
git status
git add .
git commit -m "Describe the Atlas OS change"
git push
```

Review generated files before committing. Local databases, output reports, logs, and temporary files should stay ignored.

## Safety Reminders

- Use mock mode by default unless real-data mode has been explicitly configured for local testing.
- Do not access client files, credentials, OneDrive, Gmail, IBKR, or external APIs.
- Do not send email or distribute reports from Atlas OS.
- Do not treat mock output as investment advice.
- No client-facing report may be published without explicit human approval.
