# Atlas OS v0.8.0-alpha — Agent Orchestration

## Release Status

Feature complete; pending Managing Director user review.

## Summary

Atlas OS v0.8.0-alpha formalizes the local GreenRock report-agent orchestration stack. The release coordinates research, derivative context, portfolio context, risk review, compliance review, report assembly, and Chief-of-Staff summary while preserving a strict human approval boundary.

## Major Capabilities

- Agent registry for GreenRock report roles
- Dependency-based workflow orchestration
- Structured local handoffs
- Workflow state tracking
- Artifact and audit visibility
- Append-only approval and rejection records
- Chief-of-Staff summary
- Review-only report dry runs
- Scheduled local draft reports
- Derivative Workbench context where available
- Disabled Distribution Agent
- No automatic external action

## Agent Dependency Graph

```text
market_scout
    ├── derivative_analyst
    └── portfolio_analyst
            ↓
       risk_officer
            ↓
    compliance_reviewer
            ↓
       report_writer
            ↓
 atlas_chief_of_staff
```

## Workflow States

```text
pending
running
blocked
completed
failed
awaiting_human_approval
approved
rejected
```

## Approval Model

Approval and rejection require explicit CLI actions. Records are append-only for the report-agent workflow, and duplicate decisions for the same workflow are blocked.

Approval confirms human review state only. Approval must not trigger distribution.

## Distribution Lock Behavior

Before approval:

```text
blocked
runnable=false
reason=missing_explicit_approval_record
```

After approval:

```text
blocked
runnable=false
reason=distribution_disabled_in_phase_11c
```

The legacy reason string is retained for compatibility even though release terminology is now used for product documentation.

## CLI Commands Available

```bash
atlas version
atlas roadmap
atlas greenrock report-dry-run
atlas greenrock report-schedule preview
atlas greenrock report-schedule run-due
atlas greenrock agents run-report
atlas greenrock agents status
atlas greenrock agents status <workflow-id>
atlas greenrock agents approve <workflow-id>
atlas greenrock agents reject <workflow-id>
```

## Safety Boundaries

This release does not add email sending, website publishing, Shopify integration, subscriber routing, brokerage activity, order construction, CRM/client contact, credential handling, external LLM/API action, automatic approval, or automatic distribution.

Every generated report remains draft/review-only until explicit human action. Even after approval, distribution remains disabled.

## Testing Status

The release was prepared against the repository unittest suite. Final verified test totals are recorded in the release commit report.

## Known Limitations

- Local-only workflow execution
- No email
- No publishing
- No Shopify integration
- No subscriber routing
- No brokerage actions
- No orders
- No CRM or client contact
- No credential handling
- No external LLM or API action
- No automatic distribution
- Distribution Agent intentionally disabled

## Upgrade or Migration Notes

No data migration is required for v0.8.0-alpha. Existing report dry runs, schedule behavior, Derivative Workbench outputs, raw derivative CSV snapshots, and approval-gated report draft behavior are preserved.

## Managing Director Review Checklist

1. Run `atlas version`.
2. Run `atlas roadmap`.
3. Run `atlas greenrock agents run-report`.
4. Inspect `atlas greenrock agents status <workflow-id>`.
5. Open the generated workflow JSON and handoff files.
6. Confirm the Chief-of-Staff summary is readable.
7. Confirm approval remains explicit.
8. Confirm distribution remains blocked before and after approval.

## Proposed Next-Release Direction

v0.9 — Publishing and Distribution Foundations should remain planning-level until separately implemented, tested, reviewed, and approved. Potential scope includes approved-content publishing queue design, publish artifact preparation, delivery audit records, subscriber routing abstractions, compliance validation gates, explicit post-approval execution command design, and Shopify integration planning.
