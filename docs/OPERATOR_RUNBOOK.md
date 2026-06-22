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
```

If no provider is configured, Atlas prints a blocked message and does not create a report, approval, artifact, email, publication, or external action.

Optional local yfinance setup:

```bash
python3 -m pip install -e ".[market-data]"
export ATLAS_MARKET_DATA_PROVIDER=yfinance
export ATLAS_GREENROCK_REAL_TICKERS=AAPL,MSFT,NVDA
atlas greenrock report-draft --data real
```

Real-data reports still remain draft-only and blocked for human approval.

## Inspect Candidates

```bash
atlas greenrock latest-candidates
atlas greenrock review
```

Use `latest-candidates` for a quick large-cap and small/mid-cap summary. Use `review` for latest run status, report path, approval status, and top candidate names.

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

On the GreenRock page, use **Run GreenRock Report** to generate a new local draft through the normal workflow. The button creates a pending approval and does not bypass human review.

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
