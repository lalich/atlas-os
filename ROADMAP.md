# Atlas OS Roadmap

## Product Vision

Atlas OS is a governed research operating system that improves human decision-making without replacing the Managing Director's authority.

## Current Release

**v0.8.1 — Executive UX**

Status: **Feature complete; pending Managing Director review**

## Completed Release History

- **v0.5 — Core Infrastructure**
  Established the local-first CLI, database-backed workflow records, artifact storage, audit visibility, approval gates, report drafting, and browser Command Center foundations.
- **v0.6 — Derivative Intelligence**
  Established the research-only Derivative Workbench, OTM-only Top Research guardrails, exclusion explainability, calibrated scoring, ranking rationale, cross-window intelligence, read-only position context, and strategy intent mapping.
- **v0.7 — Research Automation**
  Established review-only report dry runs and scheduled local draft generation while keeping email, publishing, brokerage, CRM, and external actions disabled.
- **v0.8 — Agent Orchestration**
  Established the GreenRock report-agent registry, dependency-based workflow orchestration, structured local handoffs, workflow state tracking, approval/rejection records, and Chief-of-Staff summary.
- **v0.8.1 — Executive UX**
  Polishes the GreenRock web interface into an executive workflow review experience with human-readable status, workflow history, agent step presentation, warning deduplication, clearer actions, and safer error handling.

These are capability groupings and retrospective release classifications. They do not claim that exact historical Git releases or tags existed before v0.8.

## Current v0.8 Capabilities

- Agent registry
- Dependency-based workflow orchestration
- Structured handoffs
- Workflow state tracking
- Executive workflow panel
- Human-readable workflow history
- Agent handoff presentation
- Warning deduplication in the web UI
- Artifact and audit visibility
- Append-only approval and rejection records
- Chief-of-Staff summary
- Human approval boundary
- Distribution Agent registered but disabled
- No automatic external action

## Next Release

**v0.9 — Publishing and Distribution Foundations**

Planning-level scope may include:

- Approved-content publishing queue
- Publish artifact preparation
- Delivery audit records
- Subscriber routing abstractions
- Compliance validation gates
- Explicit post-approval execution command
- Shopify integration planning

External distribution remains disabled until separately implemented, tested, reviewed, and approved.

## Target Release

**v1.0 — GreenRock Operating System**

The target is a stable, documented, governed internal research and operating platform for GreenRock and Atlas workflows.

## Platform Invariants

- Research first
- Human-in-the-loop
- Deterministic where possible
- Explainable
- Auditable
- Fail closed
- Modular
- Test-first
- No external action without explicit approval and explicit execution
- Approval alone must not trigger distribution
- Backward compatible unless intentionally versioned

## Release Gates

Every release should require:

1. Feature complete
2. Test complete
3. Documentation complete
4. Managing Director review complete
5. Release commit and tag complete

## Future Considerations

These are noncommittal areas for future review:

- Workflow persistence and resume
- Observability and telemetry
- Plugin SDK
- Subscriber portal
- Shopify commerce integration
- Additional research agents
