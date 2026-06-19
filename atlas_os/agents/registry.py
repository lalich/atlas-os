"""Local agent registry scaffold."""

from __future__ import annotations

from atlas_os.agents.base import Agent


DEFAULT_AGENTS: tuple[Agent, ...] = (
    Agent(
        key="greenrock_screener",
        name="GreenRock Screener",
        division="greenrock",
        description="Placeholder agent for future screening workflow.",
    ),
    Agent(
        key="greenrock_reporter",
        name="GreenRock Reporter",
        division="greenrock",
        description="Placeholder agent for future monthly report drafting.",
    ),
)


def list_agents() -> tuple[Agent, ...]:
    return DEFAULT_AGENTS


def get_agent(key: str) -> Agent:
    for agent in DEFAULT_AGENTS:
        if agent.key == key:
            return agent
    raise KeyError(f"Unknown agent: {key}")

