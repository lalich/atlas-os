# Atlas OS — Repository Structure

**Version:** 1.0  
**Status:** Draft

---

## 1. Top-Level Layout

```
atlas-os/
├── docs/                    # Planning, architecture, runbooks
├── core/                    # Atlas Core — orchestration, approval, shared infra
├── agents/                  # Agent definitions (prompts, schemas, tools)
├── greenrock/               # Division: GreenRock Analysts
├── batsignal/               # Division: The Bat Signal
├── insurance/               # Division: GreenRock Insurance
├── variance/                # Division: Variance Capital (future — scaffold only)
├── archive/                 # Retired code and deprecated workflows
├── scripts/                 # Dev utilities, setup, one-off migrations
├── tests/                   # Cross-cutting integration tests
├── .github/                 # CI workflows (future)
├── pyproject.toml           # Project metadata and dependencies
├── .env.example             # Environment variable template
└── README.md                # Project entry point
```

---

## 2. Design Rationale

| Decision | Rationale |
|----------|-----------|
| Monorepo | Single source of truth; Core changes propagate atomically |
| `core/` separate from `agents/` | Core is runtime infrastructure; agents are declarative config |
| Division per top-level directory | Hard boundary prevents cross-division imports |
| `docs/` at root | Planning artifacts versioned alongside code |
| `archive/` for retired code | Preserve history without polluting active modules |
| Shared `tests/` at root | Integration tests span Core + divisions |

---

## 3. Core (`core/`)

```
core/
├── __init__.py
├── orchestrator/
│   ├── __init__.py
│   ├── engine.py              # Workflow execution engine
│   ├── scheduler.py           # Cron / manual trigger handling
│   ├── state.py               # Run state machine
│   └── workflow_loader.py     # Load YAML workflow definitions
├── approval/
│   ├── __init__.py
│   ├── gate.py                # Approval queue logic
│   ├── models.py              # Approval record types
│   └── store.py               # Persistence for approval records
├── agents/
│   ├── __init__.py
│   ├── registry.py            # Agent lookup by ID
│   ├── runner.py              # Execute agent with context
│   └── validator.py           # Output schema validation
├── llm/
│   ├── __init__.py
│   ├── gateway.py             # Unified LLM client
│   └── token_tracker.py       # Per-run token accounting
├── storage/
│   ├── __init__.py
│   ├── artifacts.py           # Read/write artifact store
│   └── database.py            # DB connection and migrations
├── events/
│   ├── __init__.py
│   └── bus.py                 # Internal event dispatch
├── api/
│   ├── __init__.py
│   ├── routes.py              # REST endpoints
│   └── schemas.py             # Request/response models
├── cli/
│   ├── __init__.py
│   └── main.py                # `atlas` CLI entry point
└── config/
    ├── __init__.py
    └── settings.py            # Environment-based settings
```

**Ownership:** Platform team. No division-specific logic.

---

## 4. Agents (`agents/`)

Agent definitions are declarative — prompts, tool bindings, and output schemas. Runtime execution lives in `core/agents/`.

```
agents/
├── README.md                  # Agent authoring guide
├── registry.yaml              # Master agent index
├── screener/
│   ├── agent.yaml             # Agent metadata (id, model, division)
│   ├── system_prompt.md       # System prompt template
│   ├── output_schema.json     # JSON Schema for validated output
│   └── tools.yaml             # Tool bindings (if any)
├── analyst/
│   ├── agent.yaml
│   ├── system_prompt.md
│   └── output_schema.json
├── publisher/
│   ├── agent.yaml
│   ├── system_prompt.md
│   └── output_schema.json
├── batsignal/                 # Future: division-scoped agents
│   ├── modeler/
│   └── risk/
└── insurance/                 # Future: division-scoped agents
    ├── crm/
    └── comms/
```

### Agent Definition Example (`agents/analyst/agent.yaml`)

```yaml
id: analyst
division: greenrock
description: Drafts per-stock research commentary from screening results
model: claude-sonnet-4-20250514
temperature: 0.3
max_tokens: 4096
prompt: system_prompt.md
output_schema: output_schema.json
tools: []
```

---

## 5. GreenRock Analysts (`greenrock/`)

First implementation target.

```
greenrock/
├── README.md
├── workflows/
│   └── monthly_report.yaml    # Workflow DAG definition
├── steps/
│   ├── __init__.py
│   ├── fetch_universe.py      # Deterministic: pull equity universe
│   ├── apply_screening.py     # Deterministic: run screening rules
│   └── rank_and_select.py     # Deterministic: select top 11 per bucket
├── screening/
│   ├── __init__.py
│   ├── criteria/
│   │   └── v1.0.yaml          # Versioned screening criteria
│   ├── filters.py             # Hard filter implementations
│   ├── signals.py             # Scoring signal implementations
│   └── ranker.py              # Weighted ranking logic
├── data/
│   ├── __init__.py
│   ├── client.py              # Abstract market data interface
│   └── providers/
│       └── polygon.py         # Concrete provider (example)
├── models/
│   ├── __init__.py
│   ├── universe.py            # Stock, Universe types
│   ├── screening.py           # ScreeningResult, Score types
│   └── report.py              # Report, Section types
├── templates/
│   └── monthly_report.md.j2   # Jinja2 report template
└── config/
    └── greenrock.yaml         # Division-level settings
```

---

## 6. The Bat Signal (`batsignal/`)

Scaffold for Phase 2. Directory structure defined now to avoid rework.

```
batsignal/
├── README.md
├── workflows/
│   └── daily_intelligence.yaml
├── steps/
├── models/
├── data/
│   └── providers/
├── analysis/
│   ├── reversion.py
│   ├── hr_probability.py
│   └── hrr_probability.py
├── risk/
│   └── bankroll.py
├── templates/
│   └── daily_brief.md.j2
└── config/
    └── batsignal.yaml
```

---

## 7. GreenRock Insurance (`insurance/`)

Scaffold for Phase 3.

```
insurance/
├── README.md
├── workflows/
│   ├── renewal_reminders.yaml
│   └── carrier_followup.yaml
├── steps/
├── models/
│   ├── prospect.py
│   └── policy.py
├── crm/
│   └── store.py
├── templates/
│   └── followup_email.md.j2
└── config/
    └── insurance.yaml
```

---

## 8. Variance Capital (`variance/`)

Future division. Placeholder only.

```
variance/
├── README.md                  # Scope TBD
└── .gitkeep
```

---

## 9. Documentation (`docs/`)

```
docs/
├── README.md                          # Documentation index
├── PRD.md                             # Product requirements
├── SYSTEM_ARCHITECTURE.md             # System architecture
├── REPOSITORY_STRUCTURE.md            # This document
├── AGENT_ARCHITECTURE.md              # Agent design
├── IMPLEMENTATION_ROADMAP.md          # Build phases
├── FUTURE_EXPANSION_ROADMAP.md        # Long-term vision
├── ATLAS_OS_MASTER_PLAN.md            # Executive summary (legacy)
├── adr/                               # Architecture Decision Records
│   └── 001-monorepo.md
├── runbooks/                          # Operational guides (future)
│   ├── monthly-report-runbook.md
│   └── approval-workflow.md
└── divisions/
    ├── greenrock/
    │   └── screening-criteria.md      # Canonical criteria doc
    ├── batsignal/
    ├── insurance/
    └── variance/
```

---

## 10. Scripts & Tests

```
scripts/
├── setup_dev.sh               # Local dev environment bootstrap
├── run_monthly_report.sh      # Convenience wrapper
└── migrate_db.py              # Database migrations

tests/
├── conftest.py                # Shared fixtures
├── core/
│   ├── test_orchestrator.py
│   ├── test_approval_gate.py
│   └── test_agent_runner.py
├── greenrock/
│   ├── test_screening.py
│   ├── test_ranker.py
│   └── test_monthly_report_workflow.py
└── integration/
    └── test_end_to_end_report.py
```

---

## 11. Configuration & Secrets

### Environment Variables (`.env.example`)

```bash
# Database
ATLAS_DATABASE_URL=sqlite:///./data/atlas.db

# Artifacts
ATLAS_ARTIFACT_PATH=./data/artifacts

# LLM
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Market Data
POLYGON_API_KEY=

# Approvers
ATLAS_APPROVERS=analyst@greenrockam.com

# Logging
ATLAS_LOG_LEVEL=INFO
```

### Config Hierarchy

```
Environment variables  (secrets, URLs)
    ↓ overrides
Division config        (greenrock/config/greenrock.yaml)
    ↓ overrides
Workflow config        (greenrock/workflows/monthly_report.yaml)
    ↓ overrides
Screening criteria     (greenrock/screening/criteria/v1.0.yaml)
```

---

## 12. Import Rules

Enforced by convention and linting (future):

| From | May Import | Must Not Import |
|------|------------|-----------------|
| `core/` | `core/*`, stdlib, third-party | `greenrock/*`, `batsignal/*`, `insurance/*` |
| `agents/` | N/A (declarative only) | Code imports |
| `greenrock/` | `core/*` (public API), `greenrock/*` | `batsignal/*`, `insurance/*`, `variance/*` |
| `batsignal/` | `core/*`, `batsignal/*` | Other divisions |
| `insurance/` | `core/*`, `insurance/*` | Other divisions |
| `tests/` | All packages | — |

---

## 13. Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Workflow IDs | `{division}.{name}` | `greenrock.monthly_report` |
| Agent IDs | `{role}` or `{division}.{role}` | `analyst`, `batsignal.modeler` |
| Run artifacts | `{run_id}/{step_id}/{filename}` | `abc123/draft_analysis/output.json` |
| Config versions | Semantic versioning in filename | `v1.0.yaml` |
| Python modules | snake_case | `fetch_universe.py` |
| CLI commands | kebab-case | `atlas run greenrock.monthly-report` |

---

## Related Documents

- [SYSTEM_ARCHITECTURE.md](./SYSTEM_ARCHITECTURE.md)
- [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md)
- [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md)
