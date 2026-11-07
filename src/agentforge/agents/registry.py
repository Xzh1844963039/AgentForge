"""Agent registry."""

from __future__ import annotations

from agentforge.agents.base import BaseAgent
from agentforge.models.agent import AgentCard, AgentRole


class AgentNotFoundError(LookupError):
    pass


class AgentRegistry:
    """In-memory registry of instantiated agents."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"agent {agent.agent_id!r} is already registered")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> BaseAgent | None:
        return self._agents.get(agent_id)

    def require(self, agent_id: str) -> BaseAgent:
        agent = self.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"unknown agent {agent_id!r}")
        return agent

    def list(self) -> list[BaseAgent]:
        return list(self._agents.values())

    def cards(self) -> list[AgentCard]:
        return [agent.card for agent in self._agents.values()]

    def by_role(self, role: AgentRole) -> BaseAgent | None:
        for agent in self._agents.values():
            if agent.role == role:
                return agent
        return None

    def require_role(self, role: AgentRole) -> BaseAgent:
        agent = self.by_role(role)
        if agent is None:
            raise AgentNotFoundError(f"no agent registered for role {role.value!r}")
        return agent

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def __len__(self) -> int:
        return len(self._agents)
