# Atlas OS — Master Plan

## Overview

Atlas OS is a multi-division operating system that automates research, reporting, publishing, and administrative workflows across GreenRock Asset Management, Variance Capital, The Bat Signal, and GreenRock Insurance.

This document defines the architecture, division scope, build phases, and operating principles for the platform.

---

## Guiding Principles

1. **Human oversight first** — All client-facing communication requires human approval before delivery.
2. **Division isolation** — Each division owns its domain logic; shared infrastructure lives in `core/`.
3. **Agent orchestration** — Specialized agents execute tasks; Atlas Core routes, schedules, and monitors work.
4. **Incremental delivery** — Ship working workflows early; expand features in defined phases.
5. **Auditability** — Every automated action is logged with inputs, outputs, and approval status.

---

## Repository Structure

```
atlas-os/
├── docs/           # Mission, plans, specs, runbooks
├── greenrock/      # Division 1: GreenRock Analysts
├── batsignal/      # Division 2: The Bat Signal
├── insurance/      # Division 3: GreenRock Insurance
├── agents/         # Agent definitions, prompts, and tooling
├── core/           # Orchestration, shared utilities, approval gates
└── archive/        # Retired code and deprecated workflows
```

---

## Division 1: GreenRock Analysts

**Location:** `greenrock/`

### Objective

Generate a monthly research report identifying:

* 11 stocks above $5B market cap
* 11 stocks below $5B market cap

using GreenRock technical screening criteria.

### Phase 1 — Screening & Report Draft

- [ ] Define GreenRock technical screening criteria (documented in `greenrock/`)
- [ ] Build stock universe filter (market cap split at $5B)
- [ ] Run screening pipeline and rank candidates
- [ ] Produce draft report (structured markdown or JSON)

### Phase 2 — Publication & Delivery

- [ ] PDF generation
- [ ] Options analysis module
- [ ] Subscriber delivery workflow
- [ ] Website publication integration

### Key Agents

| Agent | Responsibility |
|-------|----------------|
| Screener | Apply technical criteria to stock universe |
| Analyst | Draft commentary and rankings for selected names |
| Publisher | Format and queue report for human review |

---

## Division 2: The Bat Signal

**Location:** `batsignal/`

### Objective

Generate daily baseball betting intelligence based on:

* Reversion statistics
* HR probability
* HRR probability
* Bankroll management

### Phase 1 — Daily Intelligence

- [ ] Ingest daily game and player data
- [ ] Compute reversion, HR, and HRR probability models
- [ ] Apply bankroll management rules
- [ ] Produce daily intelligence brief for review

### Phase 2 — Product & Community

- [ ] Subscription tiers
- [ ] Results tracking and performance ledger
- [ ] Community access layer

### Key Agents

| Agent | Responsibility |
|-------|----------------|
| Data | Fetch and normalize game/player stats |
| Modeler | Run probability and reversion calculations |
| Risk | Apply bankroll sizing and exposure limits |
| Publisher | Format daily brief for human review |

---

## Division 3: GreenRock Insurance

**Location:** `insurance/`

### Objective

Automate:

* Carrier follow-ups
* Prospect management
* Policy tracking
* Renewal reminders

### Phase 1 — CRM & Reminders

- [ ] Prospect pipeline (status, notes, next action)
- [ ] Policy registry (carrier, term, renewal date)
- [ ] Automated renewal reminder queue
- [ ] Carrier follow-up task generation

### Phase 2 — Communication Automation

- [ ] Draft follow-up emails and messages (approval-gated)
- [ ] Carrier response tracking
- [ ] Renewal workflow automation

### Key Agents

| Agent | Responsibility |
|-------|----------------|
| CRM | Manage prospects and policy records |
| Scheduler | Generate follow-up and renewal tasks |
| Comms | Draft client/carrier messages for approval |

---

## Atlas Core

**Location:** `core/` and `agents/`

Atlas Core is the central orchestration layer. It assigns tasks to specialized agents, enforces approval gates, and coordinates cross-division scheduling.

### Responsibilities

- Task routing and agent dispatch
- Workflow scheduling (daily, monthly, event-driven)
- Human approval queue for all outbound communication
- Logging, error handling, and retry logic
- Shared integrations (email, storage, notifications)

### Approval Gate

No client-facing message, report, or publication leaves Atlas OS without explicit human approval. The approval queue lives in Core and blocks downstream delivery until cleared.

---

## Build Sequence

| Priority | Division | Milestone |
|----------|----------|-----------|
| 1 | Core | Orchestration skeleton, approval gate, logging |
| 2 | GreenRock Analysts | Monthly screening report (draft) |
| 3 | The Bat Signal | Daily intelligence brief (draft) |
| 4 | GreenRock Insurance | Prospect/policy tracking + renewal reminders |
| 5 | All | Publication, delivery, and subscription features |

---

## Open Questions

- Variance Capital scope and division placement
- Data sources and API credentials per division
- Preferred stack (language, hosting, database)
- Subscriber and community platform choices

---

## Related Documents

- [README.md](./README.md) — Documentation index
- [PRD.md](./PRD.md) — Product requirements
- [SYSTEM_ARCHITECTURE.md](./SYSTEM_ARCHITECTURE.md) — System architecture
- [REPOSITORY_STRUCTURE.md](./REPOSITORY_STRUCTURE.md) — Repository layout
- [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md) — Agent design
- [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) — Build phases
- [FUTURE_EXPANSION_ROADMAP.md](./FUTURE_EXPANSION_ROADMAP.md) — Long-term expansion
