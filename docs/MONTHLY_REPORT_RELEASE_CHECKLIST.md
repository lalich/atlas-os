# Monthly Report Release Checklist

Use this checklist for the local GreenRock monthly report workflow. Preferred path: Scan, Stage, Generate Draft, Approve, Export PDF. Atlas OS defaults to local approval-gated behavior and explicitly labels mock or real data when used.

## 1. Generate Draft

```bash
atlas greenrock report-ready
atlas greenrock report-workbench
atlas greenrock report-tasks
atlas greenrock staging ready
atlas greenrock staging enrich
atlas greenrock report-from-staging
atlas greenrock report-from-staging --allow-underfilled
atlas greenrock stage-analyst-slate --overwrite-staging
atlas greenrock report-analyst-slate --overwrite-staging
```

Legacy/sample drafts remain available for comparison:

```bash
atlas greenrock report-draft
```

- Confirm the command completed successfully.
- Open `/greenrock/report-workbench` and confirm the production timeline, readiness state, next operator action, task records, latest scan, Daily Intelligence status, staged Analyst Slate, Candidate Review, pending approvals, and PDF status.
- Record any local candidate decisions (`accepted`, `deferred`, `research`, `excluded`) needed for the Human Intelligence Layer. Confirm they do not alter GreenRock Score, canonical rank, staging, approval, or PDF gates.
- Confirm staged analytics are complete before report generation. Underfilled sections and missing analytics are different warnings.
- Record the `run_id` and `approval_id`.
- Confirm the report path is run-specific.
- Confirm the Candidate Source Disclosure identifies staging, watchlist, scanner/population, mock, or real-data sourcing as applicable.
- If using the Atlas Analyst slate, confirm the draft highlights archetype leaders before compact remaining-candidate tables.

## 2. Review Report

```bash
atlas greenrock review
atlas greenrock latest-report --print
```

- Open `/greenrock/reports/<run_id>/review` in the browser.
- Confirm the review page shows the branded Atlas OS / GreenRock title section, report metadata, source disclosure, candidate tables, evidence notes, approval status, and PDF status.
- Review executive summary, methodology, tables, rationale, risks, and disclaimers.
- Confirm data mode and data source are clearly labeled.
- Confirm candidate source, selection mode, source lists, and scan IDs are clearly labeled where applicable.
- If using staging mode, confirm readiness warnings, staging notes, source lists, and scan IDs appear only where available.
- If using `--allow-missing-analytics`, confirm each affected ticker has an explicit staging data warning.
- Confirm the main report tables stay readable and move long bullish/caution signals into candidate notes or an appendix.
- Confirm Atlas Analyst summaries are deterministic, cite GreenRock Score/Confidence/Evidence Agreement, and avoid transaction-action or personalized recommendation language.
- Confirm prior-scan comparison either shows rank/score/confidence/evidence changes or says no prior scan comparison is available.
- Confirm empty staged buckets show a clean sentence rather than placeholder rows.
- Confirm green-filled table headers render with yellow text in Markdown/HTML/PDF views where styling applies.
- Confirm data-mode and human-approval disclaimers are present.
- Confirm no personalized recommendations, guarantees, or promissory language are present.

## 3. Inspect Candidates

```bash
atlas greenrock latest-candidates
```

- Review large-cap and small/mid-cap candidate lists.
- Confirm GreenRock Scores and signal labels look reasonable for the selected data mode.

## 4. Approve Or Reject

```bash
atlas approvals show <approval_id>
atlas approvals approve <approval_id>
```

Reject instead if the report should not advance:

```bash
atlas approvals reject <approval_id>
```

In the browser, use the approve/reject controls on `/greenrock/reports/<run_id>/review`; each action still requires confirmation and returns to the same review page.

## 5. Export Final PDF

```bash
atlas greenrock export-pdf <approval_id>
```

Optionally open after export:

```bash
atlas greenrock export-pdf <approval_id> --open
```

PDF export is approved-only and idempotent for the run.

In the browser, export from `/greenrock/reports/<run_id>/review` only after approval. Before approval, the PDF control should show a blocked state.

- Confirm the exported PDF cover includes report title, date, data mode, candidate source, and approval/status disclaimer.
- Confirm missing optional logo assets do not block PDF export.

## 6. Verify Final Packet

```bash
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
```

- Confirm approval status is `approved`.
- Confirm Markdown and PDF paths are present.
- Confirm `report_final_pdf` appears exactly once in the artifact list.
- Confirm data-mode and human-approval confirmations are present.

## 7. Dashboard Check

```bash
atlas dashboard
atlas morning-brief
```

- Confirm latest GreenRock report status.
- Confirm final PDF status is `exported`.
- Confirm pending approvals are expected.
- Open `/atlas/morning-brief` and confirm action buttons point to Market Pulse, pending approvals, latest report review, Analyst Slate staging, staging draft generation when ready, and final PDF archive.
- Confirm `/atlas/morning-brief` and `/atlas/wall` show the GreenRock report readiness block.

## 8. Optional Draft Cleanup

Preview cleanup:

```bash
atlas greenrock cleanup-drafts --dry-run
```

Run cleanup only after confirming the latest draft and final PDFs should be preserved:

```bash
atlas greenrock cleanup-drafts
```

- Confirm the latest draft is still available.
- Confirm final PDFs are still available in the archive.
- Confirm approval records and audit logs remain intact.

## Safety

- Use mock mode by default unless real-data mode has been explicitly configured for local testing.
- Do not send email.
- Do not access client files or credentials.
- Do not publish any client-facing material without explicit human approval.
