"""Default agent profiles and construction."""

from __future__ import annotations

from agentforge.agents.base import BaseAgent
from agentforge.agents.coding import CodingAgent
from agentforge.agents.coordinator import CoordinatorAgent
from agentforge.agents.research import ResearchAgent
from agentforge.agents.reviewer import ReviewerAgent
from agentforge.llm.base import BaseLLMProvider
from agentforge.memory.base import MemoryStore
from agentforge.models.agent import AgentProfile, AgentRole
from agentforge.tools.base import ToolRegistry
from agentforge.tools.executor import ToolExecutor

DEFAULT_PROFILES: list[AgentProfile] = [
    AgentProfile(
        agent_id="coordinator",
        name="CoordinatorAgent",
        role=AgentRole.COORDINATOR,
        capabilities=[
            "planning",
            "coordination",
            "task_decomposition",
            "delegation",
            "synthesis",
            "finalization",
        ],
        description=(
            "Plans complex goals, decomposes them into a task DAG, consolidates "
            "agent results, and produces the final deliverable."
        ),
    ),
    AgentProfile(
        agent_id="research",
        name="ResearchAgent",
        role=AgentRole.RESEARCH,
        capabilities=[
            "research",
            "information_retrieval",
            "analysis",
            "summarization",
        ],
        description=(
            "Researches topics, compares alternatives, organises evidence, and "
            "produces structured briefs with explicit assumptions."
        ),
    ),
    AgentProfile(
        agent_id="coding",
        name="CodingAgent",
        role=AgentRole.CODING,
        capabilities=[
            "coding",
            "python",
            "software_architecture",
            "implementation_planning",
            "testing",
        ],
        description=(
            "Designs Python architectures and implementation plans, and owns "
            "code-related analysis and test strategy."
        ),
    ),
    AgentProfile(
        agent_id="reviewer",
        name="ReviewerAgent",
        role=AgentRole.REVIEWER,
        capabilities=[
            "review",
            "quality_assurance",
            "critique",
            "risk_analysis",
            "verification",
        ],
        description=(
            "Independently audits drafts and plans for correctness, completeness, "
            "consistency, feasibility, and risk."
        ),
    ),
]

_AGENT_CLASSES = {
    AgentRole.COORDINATOR: CoordinatorAgent,
    AgentRole.RESEARCH: ResearchAgent,
    AgentRole.CODING: CodingAgent,
    AgentRole.REVIEWER: ReviewerAgent,
}


def build_default_agents(
    *,
    provider: BaseLLMProvider,
    memory: MemoryStore,
    tools: ToolRegistry,
    memory_recall_limit: int = 4,
    tool_executor: ToolExecutor | None = None,
    max_tool_calls: int = 6,
    max_output_repairs: int = 1,
    require_structured_actions: bool = True,
    max_task_tokens: int = 120_000,
    max_repeated_tool_actions: int = 3,
    planner_protocol: str = "full",
    agent_protocol: str = "full",
) -> list[BaseAgent]:
    agents: list[BaseAgent] = []
    for profile in DEFAULT_PROFILES:
        agent_class = _AGENT_CLASSES[profile.role]
        agents.append(
            agent_class(
                profile=profile,
                provider=provider,
                memory=memory,
                tools=tools,
                memory_recall_limit=memory_recall_limit,
                tool_executor=tool_executor,
                max_tool_calls=max_tool_calls,
                max_output_repairs=max_output_repairs,
                require_structured_actions=require_structured_actions,
                max_task_tokens=max_task_tokens,
                max_repeated_tool_actions=max_repeated_tool_actions,
                planner_protocol=planner_protocol,
                agent_protocol=agent_protocol,
            )
        )
    return agents
