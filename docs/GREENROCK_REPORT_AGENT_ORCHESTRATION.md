# GreenRock Report Agent Orchestration

Atlas OS includes a local-only orchestration workflow for GreenRock draft report generation. It was introduced in v0.8.0-alpha and remains supported in v0.9.1.

Originally implemented during Phase 11C and released as Atlas OS v0.8.0-alpha.

## Commands

- `atlas greenrock agents run-report`
- `atlas greenrock agents status [workflow-id]`
- `atlas greenrock agents approve <workflow-id> [--approver NAME] [--note TEXT]`
- `atlas greenrock agents reject <workflow-id> [--approver NAME] [--note TEXT]`

## Agent Graph

The runnable local workflow is deterministic:

1. `market_scout`
2. `derivative_analyst`
3. `portfolio_analyst`
4. `risk_officer`
5. `compliance_reviewer`
6. `report_writer`
7. `atlas_chief_of_staff`

`distribution_agent` is registered for future checks only. It remains disabled and non-runnable in v0.9.1.

## Workflow States

Supported states are:

- `pending`
- `running`
- `blocked`
- `completed`
- `failed`
- `awaiting_human_approval`
- `approved`
- `rejected`

Successful report-agent runs stop at `awaiting_human_approval`.

## Local Artifacts

Each workflow writes local JSON under:

`.atlas/output/greenrock/report_agents/<workflow-id>/`

Each agent handoff records:

- agent role
- task ID
- input artifact references
- output artifact references
- start and completion timestamps
- status
- warnings
- errors

The report writer uses the existing review-only dry-run report generator and writes drafts under the existing `report_dry_runs` directory.

## Approval Records

Approval and rejection require explicit CLI actions. Atlas writes append-only approval decision records to:

`.atlas/output/greenrock/report_agents/approvals.jsonl`

Duplicate decisions for the same workflow are blocked.

## Safety Boundary

This workflow does not send email, publish, contact clients, update CRM records, call a broker, construct orders, handle credentials, or call external LLM/API services.

Approval does not trigger distribution. Even after approval, the `distribution_agent` remains blocked with reason `distribution_disabled_in_phase_11c`. That compatibility reason string is retained from the original Phase 11C implementation.
