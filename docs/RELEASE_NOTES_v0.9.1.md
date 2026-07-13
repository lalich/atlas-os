# Atlas OS v0.9.1 — Executive Workflow Hardening

## Release Status

Release hardening complete; Managing Director review recorded July 12, 2026.

## Summary

Atlas OS v0.9.1 closes the v0.9 release cycle before v1.0 development. It aligns package and runtime version metadata, records the Managing Director review milestone, reconciles current documentation with the implemented repository, and corrects GreenRock report-agent artifact paths.

## Changes

- Align `pyproject.toml`, `VERSION`, runtime version output, tests, README, and roadmap on v0.9.1.
- Mark the initial implementation roadmap as a historical planning baseline rather than an active delivery schedule.
- Mark the future expansion roadmap as noncommittal and subordinate to the current governed safety boundary.
- Update the documentation index to identify GreenRock as the active implemented division under `atlas_os/greenrock/`.
- Correct report-agent paths to `.atlas/output/greenrock/report_agents/`.
- Preserve prior release notes as historical records while recording completion of the v0.9.0 Managing Director review.

## Safety Boundary

This patch adds no external action. Atlas OS still does not send email, publish, contact clients, update CRM systems, place trades or broker orders, handle credentials, call external LLM/API services, approve automatically, or distribute reports automatically. Approval alone does not trigger distribution.

## Next Release

v1.0 — GreenRock Operating System. Scope begins with hardening the governed GreenRock operating loop, compatibility, recovery, documentation, and operator confidence.
