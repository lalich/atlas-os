# Atlas OS — Product Requirements Document

**Version:** 1.0  
**Status:** Draft  
**First Implementation Target:** GreenRock Analysts Monthly Report

---

## 1. Executive Summary

Atlas OS is a multi-division AI operating system that orchestrates specialized agents to automate research, reporting, publishing, and administrative workflows across four business units:

1. GreenRock Asset Management
2. Variance Capital
3. The Bat Signal
4. GreenRock Insurance

The platform prioritizes **human oversight**, **auditability**, and **division isolation** while sharing a common orchestration layer (Atlas Core).

The first shippable product is the **GreenRock Analysts Monthly Report**: an automated pipeline that screens equities, selects 22 names (11 large-cap, 11 small-cap), drafts research commentary, and queues a report for human approval before publication.

---

## 2. Problem Statement

### Current State

- Research report production is manual, repetitive, and time-bound to a monthly cadence.
- Screening criteria exist informally but are not codified or consistently applied.
- Cross-division workflows (betting intelligence, insurance CRM) share no common orchestration or approval infrastructure.
- Client-facing outputs lack a unified approval gate.

### Desired State

- Agents execute repeatable workflows on schedule with deterministic inputs and logged outputs.
- Humans review and approve all client-facing deliverables before release.
- Each division owns domain logic; Core owns routing, scheduling, and governance.
- New divisions and workflows can be added without rewriting Core.

---

## 3. Goals & Non-Goals

### Goals

| ID | Goal |
|----|------|
| G1 | Automate the GreenRock Analysts monthly screening and report draft by end of Phase 1 |
| G2 | Enforce human approval on all outbound, client-facing content |
| G3 | Provide a reusable orchestration layer for future divisions |
| G4 | Maintain full audit trail of agent actions, inputs, and approvals |
| G5 | Support scheduled (cron) and on-demand workflow execution |

### Non-Goals (Phase 1)

- Subscriber billing or payment processing
- Public website CMS integration
- Real-time trading or order execution
- Fully autonomous client communication without human review
- Mobile applications

---

## 4. Stakeholders

| Stakeholder | Role | Interest |
|-------------|------|----------|
| GreenRock research team | Primary user | Monthly report quality and timeliness |
| Operations / admin | Approver | Workflow reliability and audit logs |
| Bat Signal subscribers (future) | Consumer | Daily intelligence accuracy |
| Insurance team (future) | User | CRM automation and renewal tracking |
| Variance Capital (future) | User | TBD — separate mandate, shared infrastructure |

---

## 5. User Personas

### 5.1 Research Analyst (GreenRock)

- **Needs:** A draft monthly report with 22 screened stocks, ranked and annotated.
- **Pain:** Manual screening across large universes; inconsistent application of criteria.
- **Success:** Receives a review-ready draft by a fixed monthly deadline with clear rationale per pick.

### 5.2 Approver / Principal

- **Needs:** Single queue of pending deliverables with diff/history and one-click approve/reject.
- **Pain:** Content scattered across email, docs, and ad-hoc tools.
- **Success:** All client-facing output passes through one approval gate with full context.

### 5.3 System Operator

- **Needs:** Visibility into workflow runs, failures, and agent logs.
- **Pain:** No centralized job monitoring.
- **Success:** Dashboard or CLI showing run status, retries, and error alerts.

---

## 6. Product Scope by Division

### 6.1 GreenRock Analysts (MVP — Phase 1)

**Objective:** Generate a monthly research report with 11 stocks above $5B market cap and 11 stocks below $5B market cap using GreenRock technical screening criteria.

**Deliverables:**

- Screened and ranked stock lists (large-cap / small-cap)
- Per-stock technical summary and selection rationale
- Assembled report draft (Markdown → PDF in Phase 2)
- Approval queue entry for human sign-off

### 6.2 The Bat Signal (Phase 2+)

**Objective:** Daily baseball betting intelligence (reversion stats, HR/HRR probability, bankroll management).

### 6.3 GreenRock Insurance (Phase 3+)

**Objective:** Carrier follow-ups, prospect management, policy tracking, renewal reminders.

### 6.4 Variance Capital (Future)

**Objective:** TBD. Treated as a distinct division with shared Atlas Core infrastructure. Scope to be defined in [FUTURE_EXPANSION_ROADMAP.md](./FUTURE_EXPANSION_ROADMAP.md).

---

## 7. Functional Requirements — GreenRock Analysts Monthly Report

### 7.1 Universe & Screening

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-GR-01 | System shall ingest a configurable US equity universe (exchange-listed, minimum liquidity threshold TBD) | P0 |
| FR-GR-02 | System shall split universe at $5B market cap into large-cap and small-cap buckets | P0 |
| FR-GR-03 | System shall apply GreenRock technical screening criteria (config-driven; see §7.6) | P0 |
| FR-GR-04 | System shall rank candidates within each bucket and select top 11 per bucket | P0 |
| FR-GR-05 | System shall exclude stocks failing hard filters (e.g., insufficient history, halted symbols) | P0 |
| FR-GR-06 | System shall log screening inputs, filter results, and final selections | P0 |

### 7.2 Analysis & Report Generation

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-GR-07 | Analyst agent shall produce per-stock commentary: setup, key levels, catalyst context | P0 |
| FR-GR-08 | System shall assemble a structured report (title, date, large-cap section, small-cap section, disclaimers) | P0 |
| FR-GR-09 | Report shall include data snapshot timestamp and criteria version used | P0 |
| FR-GR-10 | System shall support on-demand re-run with overridden parameters (date, criteria version) | P1 |
| FR-GR-11 | System shall generate PDF from approved Markdown (Phase 2) | P2 |

### 7.3 Approval & Publication

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-GR-12 | Completed report draft shall enter the Core approval queue automatically | P0 |
| FR-GR-13 | No report shall be marked "published" without explicit human approval | P0 |
| FR-GR-14 | Approver shall be able to reject with comments, triggering revision workflow (Phase 2) | P1 |
| FR-GR-15 | Approved report shall be stored in durable artifact storage with version ID | P0 |
| FR-GR-16 | Subscriber email delivery (Phase 2) | P2 |
| FR-GR-17 | Website publication integration (Phase 2) | P2 |

### 7.4 Scheduling

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-GR-18 | Monthly workflow shall run on a configurable schedule (default: first business day of month, 06:00 ET) | P0 |
| FR-GR-19 | Operator shall trigger manual run via CLI or API | P0 |
| FR-GR-20 | Failed runs shall retry with exponential backoff (max 3 attempts) | P1 |

### 7.5 Core Platform (Cross-Cutting)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-CORE-01 | All workflow runs shall have a unique run ID and structured logs | P0 |
| FR-CORE-02 | All agent outputs shall be persisted as artifacts linked to run ID | P0 |
| FR-CORE-03 | Approval queue shall support pending / approved / rejected states | P0 |
| FR-CORE-04 | Secrets (API keys) shall not be stored in repository; use environment or secret manager | P0 |
| FR-CORE-05 | Division workflows shall not directly call other divisions' internal modules | P0 |

### 7.6 GreenRock Technical Screening Criteria (Placeholder)

> **Action required:** Research team to supply canonical criteria. Architecture assumes config-driven rules.

Proposed structure (to be validated):

```yaml
version: "1.0"
hard_filters:
  min_avg_volume: 500000
  min_price: 5.00
  min_history_days: 252
signals:
  - name: trend_strength
    weight: 0.25
  - name: relative_strength
    weight: 0.25
  - name: volume_confirmation
    weight: 0.20
  - name: momentum
    weight: 0.15
  - name: volatility_regime
    weight: 0.15
ranking:
  method: weighted_score
  select_top_n: 11
```

---

## 8. Non-Functional Requirements

| ID | Category | Requirement |
|----|----------|-------------|
| NFR-01 | Reliability | Monthly workflow completes successfully ≥99% of scheduled runs |
| NFR-02 | Performance | Full screening + draft report ≤60 minutes for universe ≤8,000 symbols |
| NFR-03 | Auditability | 100% of agent actions retrievable by run ID for 24 months |
| NFR-04 | Security | Role-based access to approval queue; no public exposure of draft artifacts |
| NFR-05 | Maintainability | Screening criteria updatable via config without code deploy (Phase 2) |
| NFR-06 | Portability | Core orchestration division-agnostic; GreenRock logic isolated in `greenrock/` |
| NFR-07 | Cost | LLM token usage logged per run; budget alerts configurable |

---

## 9. Data Requirements

### 9.1 Market Data (GreenRock MVP)

| Data | Source (TBD) | Frequency | Fields |
|------|--------------|-----------|--------|
| Equity universe | Vendor API | Daily | symbol, exchange, market cap |
| OHLCV history | Vendor API | Daily | open, high, low, close, volume |
| Corporate actions | Vendor API | As needed | splits, dividends |

### 9.2 Internal Data

| Entity | Storage | Retention |
|--------|---------|-----------|
| Workflow runs | PostgreSQL or SQLite (dev) | 24 months |
| Artifacts (reports, screening output) | Object storage / local filesystem (dev) | Indefinite (approved); 90 days (draft) |
| Approval records | PostgreSQL | Indefinite |
| Agent logs | Structured log store | 24 months |

---

## 10. User Flows

### 10.1 Monthly Report (Happy Path)

```mermaid
sequenceDiagram
    participant Scheduler
    participant Core as Atlas Core
    participant Screener as Screener Agent
    participant Analyst as Analyst Agent
    participant Publisher as Publisher Agent
    participant Queue as Approval Queue
    participant Human as Approver

    Scheduler->>Core: Trigger monthly_greenrock_report
    Core->>Screener: Run screening workflow
    Screener-->>Core: 22 selected symbols + scores
    Core->>Analyst: Draft commentary per symbol
    Analyst-->>Core: Structured analysis artifacts
    Core->>Publisher: Assemble report draft
    Publisher-->>Core: Report artifact (Markdown)
    Core->>Queue: Enqueue for approval
    Human->>Queue: Review and approve
    Queue-->>Core: Approval recorded
    Core-->>Core: Mark run complete; store final artifact
```

### 10.2 Approval Rejection (Phase 2)

1. Approver rejects with comments.
2. Core routes back to Analyst agent with rejection context.
3. Revised draft re-enters approval queue.

---

## 11. Success Metrics

### Phase 1 (MVP)

| Metric | Target |
|--------|--------|
| Report draft delivered by deadline | 100% of months |
| Human edit time vs. fully manual baseline | ≥50% reduction |
| Screening reproducibility (same inputs → same output) | 100% |
| Approval gate bypass incidents | 0 |

### Long-Term

| Metric | Target |
|--------|--------|
| Divisions onboarded to Atlas Core | 4 |
| Client-facing sends without approval | 0 |
| Mean time to add new scheduled workflow | <2 weeks |

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Screening criteria not formally documented | High | Block MVP on criteria sign-off; use versioned config |
| Market data vendor outage | High | Retry logic; fallback vendor (Phase 2) |
| LLM hallucination in commentary | High | Structured output schema; human approval gate; cite data fields only |
| Scope creep into publication/delivery | Medium | Strict Phase 1 boundary; defer PDF/subscriber features |
| Variance Capital scope undefined | Low | Document as future division; no Phase 1 dependency |

---

## 13. Dependencies & Assumptions

### Dependencies

- GreenRock technical screening criteria documented and signed off
- Market data API access provisioned
- LLM API access provisioned
- Designated approver(s) identified

### Assumptions

- Monthly cadence is sufficient for MVP (no intraday updates)
- English-only reports for Phase 1
- Single approver role sufficient for MVP
- Runs on scheduled infrastructure (local cron → cloud scheduler in production)

---

## 14. Out of Scope (Explicit)

- Options analysis module
- Subscriber management and billing
- Public-facing website CMS
- Real-time market data streaming
- Variance Capital workflows
- Bat Signal and Insurance divisions (Phase 1)

---

## 15. Open Questions

| # | Question | Owner | Blocking? |
|---|----------|-------|-----------|
| OQ-1 | Canonical GreenRock technical screening criteria | Research team | Yes |
| OQ-2 | Market data vendor selection | Operations | Yes |
| OQ-3 | Report template and disclaimer language | Legal / Research | Yes |
| OQ-4 | Variance Capital product scope | Principal | No |
| OQ-5 | Hosting environment (cloud vs. on-prem) | Operations | No (Phase 1 local OK) |

---

## Related Documents

- [SYSTEM_ARCHITECTURE.md](./SYSTEM_ARCHITECTURE.md)
- [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md)
- [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md)
- [FUTURE_EXPANSION_ROADMAP.md](./FUTURE_EXPANSION_ROADMAP.md)
