# Atlas OS Roadmap

## Product Vision

Atlas OS is a governed research operating system that improves human decision-making without replacing the Managing Director's authority.

## Current Release

**v0.9.1 — Executive Workflow**

Status: **Release hardening complete; Managing Director review recorded**

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
- **v0.9.0 — Executive Workflow**
  Makes The Wall and GreenRock workflow more authoritative with approved-report Picks Board persistence, Smart Staging Replacement, persistent executive context, and a unified executive timeline while preserving the local-only safety boundary.
- **v0.9.1 — Executive Workflow hardening**
  Records Managing Director review, aligns package and runtime version metadata, reconciles current documentation, and corrects local artifact path guidance before v1.0 development.

These are capability groupings and retrospective release classifications. They do not claim that exact historical Git releases or tags existed before v0.8.

## Current v0.9 Capabilities

- The Wall as the primary executive command surface
- Approved-report Picks Board source of truth
- Smart Staging Replacement for full report sections
- Persistent local executive context
- Unified executive timeline
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

**v1.0 — GreenRock Operating System**

Planning-level scope should focus on hardening the governed GreenRock operating loop, documentation, compatibility, and operator confidence. External distribution remains disabled until separately implemented, tested, reviewed, and approved.

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
