# Atlas OS — Documentation Index

Atlas OS is a governed, local-first research operating system. GreenRock is the active implemented division; Variance Capital, The Bat Signal, and GreenRock Insurance remain future expansion areas.

**Current release:** v0.9.1 — Executive Workflow hardening

**Next release:** v1.0 — GreenRock Operating System

---

## Planning Documents

| Document | Description |
|----------|-------------|
| [PRD.md](./PRD.md) | Product Requirements Document — goals, functional requirements, user flows, success metrics |
| [SYSTEM_ARCHITECTURE.md](./SYSTEM_ARCHITECTURE.md) | System architecture — layers, workflow model, approval gate, deployment, security |
| [REPOSITORY_STRUCTURE.md](./REPOSITORY_STRUCTURE.md) | Repository layout — directory tree, module ownership, import rules, naming conventions |
| [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md) | Agent design — lifecycle, GreenRock MVP roster, prompt standards, coordination patterns |
| [ATLAS_AGENTS.md](./ATLAS_AGENTS.md) | Phase 8A local agent orchestration — responsibilities, safe mode, Atlas Inbox, run records |
| [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) | Historical implementation baseline; superseded for current sequencing by the root roadmap |
| [FUTURE_EXPANSION_ROADMAP.md](./FUTURE_EXPANSION_ROADMAP.md) | Noncommittal long-range expansion ideas; not an active release plan |

## Summary

| Document | Description |
|----------|-------------|
| [ATLAS_OS_MASTER_PLAN.md](./ATLAS_OS_MASTER_PLAN.md) | Executive summary and high-level division overview |

---

## Divisions

| Division | Directory | Status |
|----------|-----------|--------|
| GreenRock | `atlas_os/greenrock/` | **Active — v0.9.1** |
| The Bat Signal | Not implemented | Future consideration |
| GreenRock Insurance | Not implemented | Future consideration |
| Variance Capital | Not implemented | Future consideration |

---

## Core Principles

1. **Human oversight first** — All client-facing communication requires approval.
2. **Division isolation** — Domain logic stays in division packages; shared infra in `core/`.
3. **Agent orchestration** — Specialized agents execute workflow steps; Core routes and monitors.
4. **Auditability** — Every action logged with run ID, inputs, outputs, and approval status.

---

## Recommended Reading Order

1. [PRD.md](./PRD.md) — What we're building and why
2. [SYSTEM_ARCHITECTURE.md](./SYSTEM_ARCHITECTURE.md) — How it fits together
3. [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md) — How agents work
4. [REPOSITORY_STRUCTURE.md](./REPOSITORY_STRUCTURE.md) — Where code lives
5. [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) — When we build it
6. [FUTURE_EXPANSION_ROADMAP.md](./FUTURE_EXPANSION_ROADMAP.md) — Where it goes next
