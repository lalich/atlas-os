# Atlas OS v0.8.1 — Executive UX

## Release Status

Feature complete; pending Managing Director review.

## Summary

Atlas OS v0.8.1 turns the GreenRock web interface into the primary report-workflow review experience. The release keeps the CLI fully supported while making the browser view clearer for understanding latest report state, agent handoffs, warnings, approval status, PDF readiness, and distribution lock behavior.

## Major UX Improvements

- Executive workflow panel on the GreenRock home page
- Human-readable labels for workflow and approval states
- Recent report-agent workflow history
- Agent progress cards for structured handoffs
- Deduplicated workflow warnings with source-agent detail
- Clear draft/PDF availability states
- Web approval and rejection actions using the same domain functions as the CLI
- Friendly unknown-workflow errors in CLI and web views
- More useful candidate empty states
- More readable watchlist presentation
- GreenRock Research / Powered by Atlas OS hierarchy

## Workflow Status Display

Internal states are retained for storage and compatibility, but the web UI maps them to human-readable labels. For example:

- `awaiting_human_approval` displays as `Awaiting Managing Director Review`
- `approved` displays as `Approved`
- `distribution_disabled_in_phase_11c` displays as `Distribution disabled in Atlas OS v0.8.1`

Persisted identifiers and compatibility reason strings are unchanged.

## Web Actions

The GreenRock web interface now exposes local-only actions where safe:

- Open Draft
- Open PDF
- View Workflow Details
- View Chief-of-Staff Summary
- Approve
- Reject

Approval and rejection are explicit POST actions. Rejection requires a reason in the browser. Finalized workflows cannot be approved or rejected again.

## Safety Boundaries

This release does not add email sending, website publishing, Shopify integration, subscriber routing, brokerage activity, order construction, CRM/client contact, credential handling, external LLM/API action, automatic approval, or automatic distribution.

Approval does not trigger distribution. After approval, the Distribution Agent remains blocked.

## Known Limitations

- Workflows remain local-only
- Distribution remains intentionally disabled
- No subscriber routing or Shopify integration exists
- PDF presentation only reflects existing local artifacts
- Report-agent approval is separate from legacy report-draft approval records

## Verification

The release was prepared against targeted web/CLI/report-agent tests and the full repository unittest suite. Final verified test totals are recorded in the release commit report.

## Next Direction

v0.9 remains **Publishing and Distribution Foundations** planning only. No v0.9 implementation is included in this release.
