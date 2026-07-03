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
)


def list_agents() -> tuple[Agent, ...]:
    return DEFAULT_AGENTS


def get_agent(key: str) -> Agent:
    for agent in DEFAULT_AGENTS:
        if agent.agent_id == key:
            return agent
    raise KeyError(f"Unknown agent: {key}")
