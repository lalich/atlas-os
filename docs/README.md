# Atlas OS — Documentation Index

Atlas OS is a multi-division AI operating system supporting GreenRock Asset Management, Variance Capital, The Bat Signal, and GreenRock Insurance.

**First implementation target:** GreenRock Analysts Monthly Report.

---

## Planning Documents

| Document | Description |
|----------|-------------|
| [PRD.md](./PRD.md) | Product Requirements Document — goals, functional requirements, user flows, success metrics |
| [SYSTEM_ARCHITECTURE.md](./SYSTEM_ARCHITECTURE.md) | System architecture — layers, workflow model, approval gate, deployment, security |
| [REPOSITORY_STRUCTURE.md](./REPOSITORY_STRUCTURE.md) | Repository layout — directory tree, module ownership, import rules, naming conventions |
| [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md) | Agent design — lifecycle, GreenRock MVP roster, prompt standards, coordination patterns |
| [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) | Phased build plan — Phase 0 through Phase 4 with milestones and decision gates |
| [FUTURE_EXPANSION_ROADMAP.md](./FUTURE_EXPANSION_ROADMAP.md) | Long-term vision — division expansion, platform features, 24-month targets |

## Summary

| Document | Description |
|----------|-------------|
| [ATLAS_OS_MASTER_PLAN.md](./ATLAS_OS_MASTER_PLAN.md) | Executive summary and high-level division overview |

---

## Divisions

| Division | Directory | Status |
|----------|-----------|--------|
| GreenRock Analysts | `greenrock/` | **Phase 1 — MVP target** |
| The Bat Signal | `batsignal/` | Phase 3 |
| GreenRock Insurance | `insurance/` | Phase 4 |
| Variance Capital | `variance/` (planned) | Future — scope TBD |

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
