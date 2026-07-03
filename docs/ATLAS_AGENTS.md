# Atlas Agent Orchestration

Phase 8A introduces the first real Atlas OS agent orchestration layer. Agents are local, safe, inspectable workflow operators. They coordinate existing Atlas OS surfaces without becoming autonomous external actors.

## Safe Local Mode

Agents may:

- Read local Atlas OS workflow state.
- Create local agent run records.
- Create local Atlas Inbox items.
- Create local snapshots or local artifacts only when an explicit workflow already permits them.
- Point the operator to approval-gated next steps.

Agents may not:

- Send email.
- Publish or distribute material.
- Trade or place broker/API orders.
- Read or write client files.
- Commit credentials.
- Call external LLM/API services.
- Bypass report approval gates.
- Bypass PDF export gates.

Report Agent can say `Report draft can be generated` when staging and analytics are ready. It does not generate a report automatically. The operator must still invoke the existing report workflow, approve the draft, and export any PDF explicitly.

## Agent Model

Each configured agent has:

- `agent_id`
- `name`
- `division`
- `responsibility`
- `status`: `idle`, `running`, `completed`, `failed`, or `blocked`
- `last_run_at`
- `last_message`
- `current_task`
- `output_summary`
- `health`

Configured agents:

- Market Agent: checks provider status, references the latest Market Pulse scan, and reports universe size, scored count, skipped count, and provider failures.
- Evidence Agent: summarizes latest Market Pulse evidence and identifies top rank movers, score improvers, confidence improvers, and evidence improvers.
- Memory Agent: verifies Atlas Memory state, summarizes scan-history changes, and identifies new archetype leaders.
- Report Agent: checks Analyst Slate/staging readiness and recommends whether a report draft can be generated. It does not generate the report.
- QA Agent: flags provider failures, missing analytics, underfilled or overfilled staging buckets, pending approvals, and approved reports missing PDFs.
- Inbox Agent: turns safe findings into local Atlas Inbox items.

## Agent Runs

Run records are stored locally as JSON:

```text
.atlas/output/agents/runs/<run_id>.json
.atlas/output/agents/cycles/<cycle_id>.json
.atlas/output/agents/agent_state.json
```

Each run records:

- `run_id`
- `agent_id`
- `started_at`
- `completed_at`
- `status`
- `inputs`
- `outputs`
- `warnings`
- `errors`
- `related_scan_id`
- `related_report_run_id`
- `related_approval_id`

## Cycle Flow

`atlas agents run` executes agents sequentially:

```text
Market -> Evidence -> Memory -> Report -> QA -> Inbox
```

The cycle is deliberately deterministic and local. Later agents can reference prior agent outputs from the same cycle. The Inbox Agent runs last so it can convert findings into operator-visible local items.

After a cycle, Atlas writes a cycle summary with:

- `cycle_id`
- `started_at`
- `completed_at`
- completed, failed, and blocked agent counts
- inbox items created
- warnings
- top operator actions
- run IDs
- cycle-to-cycle diff

The diff compares the latest cycle with the prior summary:

- new inbox items
- resolved or dismissed items
- new provider failures
- changed pending approval counts
- new scan and memory changes
- report readiness changes

CLI:

```bash
atlas agents list
atlas agents run
atlas agents status
atlas agents cycles
atlas agents cycle <cycle_id>
atlas agents show <run_id>
```

Browser:

- `/agents` shows cards, status, task, latest message, health, output summary, and run history.
- `/` shows the Agent Cycle card and a confirmed `Run Agent Cycle` action.
- `/atlas/morning-brief` shows latest agent run summary, health cards, inbox items, and Last Agent Cycle timestamp.

## Atlas Inbox

Atlas Inbox is local item storage for operator attention:

```text
.atlas/output/atlas/inbox/items.json
```

Fields:

- `item_id`
- `created_at`
- `source_agent`
- `severity`: `info`, `warning`, `critical`, or `action`
- `title`
- `detail`
- `target_url`
- `status`: `open`, `dismissed`, or `completed`
- `related_agent_run_id`
- `related_scan_id`
- `related_report_run_id`
- `related_approval_id`
- `created_reason`

CLI:

```bash
atlas inbox list
atlas inbox show <item_id>
atlas inbox dismiss <item_id>
atlas inbox complete <item_id>
```

Browser:

- `/atlas/inbox` lists open items and supports local dismissal/completion.
- `/atlas/inbox/<item_id>` shows provenance and why the item exists.
- The dashboard and Morning Brief surface open agent-created items.

Inbox Agent may create items such as:

- Review latest Market Pulse
- Stage Analyst Slate
- Review pending approval
- Export approved PDF
- Provider failures require cleanup
- Staging underfilled
- Morning Brief snapshot available

## Approval Gates

Agents never approve, reject, publish, export PDFs, or create client-facing files. Approval and PDF export remain explicit operator actions:

```text
Generate Draft -> Human Approval -> Export PDF
```

Agent output can recommend a next step or link to a local page, but it cannot cross the gate.

## Future Autonomy Roadmap

Future phases can add more autonomy only by preserving the same gate model:

- Phase 8B: richer agent health diagnostics and cycle comparison snapshots.
- Phase 8C: operator-configurable agent schedules that still run local-only.
- Phase 8D: richer Inbox routing, item completion, and run-to-item provenance.
- Phase 9: optional external integrations only after credentials, permissions, audit logging, compliance, and per-action human approval are designed.

Until those controls exist, Atlas agents remain local workflow operators only.
