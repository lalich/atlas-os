# Atlas OS v0.9.0 — Executive Workflow

## Release Status

Feature complete; Managing Director review completed July 12, 2026.

## Summary

Atlas OS v0.9.0 makes The Wall and GreenRock workflow faster and more authoritative through approved-report Picks Board persistence, Smart Staging Replacement, clearer executive state, and unified workflow history while preserving Atlas's local-only, human-controlled safety boundary.

## Product Changes

- The GreenRock Picks Board now represents the most recent approved GreenRock report only.
- Draft reports, scans, staging edits, and unapproved workflows no longer change the Picks Board.
- The Picks Board has a clear empty state when no approved report exists.
- Malformed newer approved report artifacts fail closed and preserve the last valid approved board where available.
- Browser staging now offers Smart Staging Replacement when a target report section is full.
- Command Center now surfaces approved picks state, persistent executive context, and a concise executive timeline below Atlas Inbox.
- The Wall remains the fast daily-intelligence view, preserving Daily Intelligence through Atlas Inbox ahead of Agent Room and System Status.
- The most recently viewed GreenRock report-agent workflow is remembered locally and safely.

## Picks Board Policy

Atlas selects approved GreenRock reports from the canonical approval records, then hydrates board sections from that report's run-specific candidate CSV artifacts. Section rows may fill empty fields from the same approved run's `candidates_csv` and compatible historical field aliases. The board changes only after a newer valid report receives Managing Director approval. If timestamp data is incomplete, Atlas uses deterministic fallback ordering from the approval/report identifiers.

Approved staging-sourced reports display the research fields persisted in their CSV artifacts, including score, confidence, evidence agreement, research priority, guardrail, source scan, section, and top signals. Financial or technical values remain unavailable when the approved run did not store them. `atlas greenrock picks-board --diagnostics` exposes the selected approval, artifact roles, headers, ticker matching, and unavailable-field reasons for local developer review.

## Smart Staging Replacement

When a section is full, Atlas shows only the current candidates in that target section and requires the operator to choose which one to replace. The replacement blocks duplicates, rejects stale confirmations if the section changed, preserves row order, and records the completed action in the local audit trail.

## Safety Boundary

This release does not add email sending, website publishing, subscriber delivery, client contact, CRM action, brokerage activity, order construction, credential handling, external LLM/API calls, automatic approval, or automatic distribution. Approval still does not trigger distribution.

## Verification

The release was prepared against targeted Picks Board, staging, Wall, CLI, and workflow tests plus the full repository unittest suite.
