"""Agent construction, registry, execution and chat tests."""

from __future__ import annotations

import pytest

from agentforge.agents import AgentRegistry, build_default_agents
from agentforge.agents.registry import AgentNotFoundError
from agentforge.llm.mock import MockProvider
from agentforge.memory import InMemoryMemoryStore
from agentforge.models.agent import AgentRole
from agentforge.models.message import Message, MessageType
from agentforge.models.task import Task
from agentforge.tools import ToolRegistry


def build_agents():
    provider = MockProvider()
    memory = InMemoryMemoryStore()
    agents = build_default_agents(
        provider=provider,
        memory=memory,
        tools=ToolRegistry(),
    )
    return provider, memory, agents


def test_registry_register_and_lookup():
    _, _, agents = build_agents()
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)

    assert len(registry) == 4
    assert {card.agent_id for card in registry.cards()} == {
        "coordinator",
        "research",
        "coding",
        "reviewer",
    }
    assert registry.require("research").name == "ResearchAgent"
    assert registry.require_role(AgentRole.REVIEWER).agent_id == "reviewer"
    assert "coding" in registry

    with pytest.raises(ValueError, match="already registered"):
        registry.register(agents[0])
    with pytest.raises(AgentNotFoundError):
        registry.require("missing")


def test_registry_rejects_duplicate_agent_id():
    _, _, agents = build_agents()
    registry = AgentRegistry()
    registry.register(agents[0])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(agents[0])


async def test_agent_executes_task_and_uses_memory():
    provider, memory, agents = build_agents()
    research = next(agent for agent in agents if agent.agent_id == "research")
    memory.remember(
        "research",
        "Prior experience: multi-agent systems benefit from independent review.",
        namespace="experience",
    )

    task = Task(
        workflow_id="wf",
        description="Research multi-agent systems and their benefits",
        required_capabilities=["research"],
        assigned_agent="research",
        metadata={"kind": "research"},
    )
    result = await research.execute_task(task, goal="study multi-agent systems")

    assert result.action.action.value == "complete"
    assert "Benefits of Multi-Agent Systems" in result.action.content
    assert result.purpose == "execute:research:research"
    assert result.parse_warning is None
    assert provider.calls[-1]["purpose"] == "execute:research:research"
    prompt = provider.calls[-1]["messages"][1]["content"]
    assert "independent review" in prompt

    research.remember_task_outcome(task, result)
    assert memory.count("research", namespace="experience") == 2
    assert memory.count("coding", namespace="experience") == 0


async def test_agent_private_chat_reply():
    provider, memory, agents = build_agents()
    research = next(agent for agent in agents if agent.agent_id == "research")
    result = await research.respond_to_chat("What are you good at?")
    assert "ResearchAgent here" in result.response.content
    assert provider.calls[-1]["purpose"] == "chat:research"
    assert research.agent_id == "research"


def test_agent_inbox_delivery_and_filtering():
    _, _, agents = build_agents()
    research = next(agent for agent in agents if agent.agent_id == "research")
    first = Message(
        conversation_id="c",
        sender="coordinator",
        receiver="research",
        message_type=MessageType.TASK_REQUEST,
        content="task one",
        task_id="task_1",
    )
    second = Message(
        conversation_id="c",
        sender="coordinator",
        receiver="research",
        message_type=MessageType.TASK_REQUEST,
        content="task two",
        task_id="task_2",
    )
    research.deliver(first)
    research.deliver(second)
    research.deliver(first)
    assert research.inbox_size == 2

    assert research.take_message("task_2") == second
    assert research.take_message("task_2") is None
    assert research.take_message() == first
    assert research.inbox_size == 0
