"""Local Atlas OS agent registry."""

from __future__ import annotations

from atlas_os.agents.base import Agent


DEFAULT_AGENTS: tuple[Agent, ...] = (
    Agent(
        agent_id="market",
        name="Market Agent",
        division="greenrock",
        responsibility="Check provider status, reference the latest Market Pulse scan, and summarize universe coverage.",
    ),
    Agent(
        agent_id="evidence",
        name="Evidence Agent",
        division="greenrock",
        responsibility="Summarize latest Market Pulse movement, including score, confidence, and evidence improvers.",
    ),
    Agent(
        agent_id="fundamental",
        name="Fundamental Agent",
        division="greenrock",
        responsibility="Review Fundamental Guardrails for confidence support, red flags, and missing data without running valuation models.",
    ),
    Agent(
        agent_id="memory",
        name="Memory Agent",
        division="greenrock",
        responsibility="Verify Atlas Memory state and identify changes since the prior scan.",
    ),
    Agent(
        agent_id="report",
        name="Report Agent",
        division="greenrock",
        responsibility="Check Analyst Slate and staging readiness, then recommend whether a draft can be generated.",
    ),
    Agent(
        agent_id="qa",
        name="QA Agent",
        division="atlas-core",
        responsibility="Flag provider failures, analytics gaps, staging bucket issues, pending approvals, and missing PDFs.",
    ),
    Agent(
        agent_id="inbox",
        name="Inbox Agent",
        division="atlas-core",
        responsibility="Create local Atlas Inbox items from safe agent findings.",
    ),
    Agent(
        agent_id="market_scout",
        name="Market Scout",
        division="greenrock",
        responsibility="Collect local market scan context for GreenRock report-agent dry runs.",
    ),
    Agent(
        agent_id="derivative_analyst",
        name="Derivative Analyst",
        division="greenrock",
        responsibility="Summarize read-only Derivative Workbench Top Research, exclusions, strategy intent, and guardrails.",
    ),
    Agent(
        agent_id="portfolio_analyst",
        name="Portfolio Analyst",
        division="greenrock",
        responsibility="Review local read-only position and staged-candidate context without broker access.",
    ),
    Agent(
        agent_id="risk_officer",
        name="Risk Officer",
        division="greenrock",
        responsibility="Propagate research risks, missing data, provider issues, and review-required flags.",
    ),
    Agent(
        agent_id="compliance_reviewer",
        name="Compliance Reviewer",
        division="greenrock",
        responsibility="Confirm draft-only boundaries and human-review requirements for report-agent workflows.",
    ),
    Agent(
        agent_id="report_writer",
        name="Report Writer",
        division="greenrock",
        responsibility="Assemble the local GreenRock report dry-run draft for review only.",
    ),
    Agent(
        agent_id="atlas_chief_of_staff",
        name="Atlas Chief of Staff",
        division="atlas-core",
        responsibility="Summarize report-agent handoffs, conflicts, flags, draft location, and approval state.",
    ),
    Agent(
        agent_id="distribution_agent",
        name="Distribution Agent",
        division="greenrock",
        responsibility="Registered for future distribution checks only; disabled and non-runnable in v0.8.1.",
        status="blocked",
        health="disabled",
        last_message="Distribution is fail-closed and unavailable in v0.8.1.",
    ),
)


def list_agents() -> tuple[Agent, ...]:
    return DEFAULT_AGENTS


def get_agent(key: str) -> Agent:
    for agent in DEFAULT_AGENTS:
        if agent.agent_id == key:
            return agent
    raise KeyError(f"Unknown agent: {key}")
