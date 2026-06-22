# Monthly Report Release Checklist

Use this checklist for the local GreenRock monthly report workflow. Atlas OS defaults to mock data; real-data mode remains local, approval-gated, and explicitly labeled when used.

## 1. Generate Draft

```bash
atlas greenrock report-draft
```

- Confirm the command completed successfully.
- Record the `run_id` and `approval_id`.
- Confirm the report path is run-specific.

## 2. Review Report

```bash
atlas greenrock review
atlas greenrock latest-report --print
```

- Review executive summary, methodology, tables, rationale, risks, and disclaimers.
- Confirm mock-data and human-approval disclaimers are present.
- Confirm no personalized recommendations, guarantees, or promissory language are present.

## 3. Inspect Candidates

```bash
atlas greenrock latest-candidates
```

- Review large-cap and small/mid-cap candidate lists.
- Confirm GreenRock Scores and signal labels look reasonable for mock data.

## 4. Approve Or Reject

```bash
atlas approvals show <approval_id>
atlas approvals approve <approval_id>
```

Reject instead if the report should not advance:

```bash
atlas approvals reject <approval_id>
```

## 5. Export Final PDF

```bash
atlas greenrock export-pdf <approval_id>
```

Optionally open after export:

```bash
atlas greenrock export-pdf <approval_id> --open
```

PDF export is approved-only and idempotent for the run.

## 6. Verify Final Packet

```bash
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
```

- Confirm approval status is `approved`.
- Confirm Markdown and PDF paths are present.
- Confirm `report_final_pdf` appears exactly once in the artifact list.
- Confirm mock-data and human-approval confirmations are present.

## 7. Dashboard Check

```bash
atlas dashboard
```

- Confirm latest GreenRock report status.
- Confirm final PDF status is `exported`.
- Confirm pending approvals are expected.

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
