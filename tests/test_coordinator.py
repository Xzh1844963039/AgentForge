"""Coordinator planning tests (valid, repair, failure)."""

from __future__ import annotations

import json

import pytest

from agentforge.agents import build_default_agents
from agentforge.llm.mock import MockProvider
from agentforge.memory import InMemoryMemoryStore
from agentforge.models.agent import AgentRole
from agentforge.orchestration.errors import PlanningError
from agentforge.tools import ToolRegistry


def build_coordinator(provider: MockProvider | None = None):
    provider = provider or MockProvider()
    memory = InMemoryMemoryStore()
    agents = build_default_agents(
        provider=provider, memory=memory, tools=ToolRegistry()
    )
    coordinator = next(a for a in agents if a.role == AgentRole.COORDINATOR)
    return coordinator, agents, provider


def valid_plan_payload() -> dict:
    return {
        "goal_summary": "test",
        "subtasks": [
            {
                "key": "research",
                "description": "research it",
                "required_capabilities": ["research"],
                "depends_on": [],
                "kind": "research",
            },
            {
                "key": "coding",
                "description": "plan it",
                "required_capabilities": ["coding"],
                "depends_on": ["research"],
                "kind": "coding",
            },
        ],
    }


def cyclic_plan_payload() -> dict:
    return {
        "goal_summary": "cycle",
        "subtasks": [
            {
                "key": "a",
                "description": "a",
                "required_capabilities": ["research"],
                "depends_on": ["b"],
            },
            {
                "key": "b",
                "description": "b",
                "required_capabilities": ["coding"],
                "depends_on": ["a"],
            },
        ],
    }


async def test_mock_plan_is_valid():
    coordinator, agents, provider = build_coordinator()
    result = await coordinator.plan_goal(
        "Research and plan", agent_cards=[a.card for a in agents], max_subtasks=8
    )
    assert len(result.attempts) == 1
    assert result.attempts[0].error is None
    assert [s.key for s in result.ordered_subtasks] == [
        "benefits_research",
        "python_implementation_plan",
    ]
    assert provider.call_count == 1
    assert provider.calls[0]["purpose"] == "plan"


async def test_plan_repair_retry():
    provider = MockProvider(
        scripted_responses=["this is not json", json.dumps(valid_plan_payload())]
    )
    coordinator, agents, _ = build_coordinator(provider)
    result = await coordinator.plan_goal(
        "goal", agent_cards=[a.card for a in agents], max_subtasks=8
    )
    assert len(result.attempts) == 2
    assert result.attempts[0].error is not None
    assert result.attempts[1].error is None
    assert [s.key for s in result.ordered_subtasks] == ["research", "coding"]


async def test_plan_failure_after_two_attempts():
    provider = MockProvider(scripted_responses=["bad", "still bad"])
    coordinator, agents, _ = build_coordinator(provider)
    with pytest.raises(PlanningError) as exc_info:
        await coordinator.plan_goal(
            "goal", agent_cards=[a.card for a in agents], max_subtasks=8
        )
    assert len(exc_info.value.attempts) == 2
    assert all(attempt.error for attempt in exc_info.value.attempts)


async def test_cycle_fails_planning():
    payload = json.dumps(cyclic_plan_payload())
    provider = MockProvider(scripted_responses=[payload, payload])
    coordinator, agents, _ = build_coordinator(provider)
    with pytest.raises(PlanningError) as exc_info:
        await coordinator.plan_goal(
            "goal", agent_cards=[a.card for a in agents], max_subtasks=8
        )
    assert "cycle" in str(exc_info.value).lower()
    assert len(exc_info.value.attempts) == 2


async def test_no_agents_rejected():
    coordinator, _, _ = build_coordinator()
    with pytest.raises(PlanningError, match="no agents"):
        await coordinator.plan_goal("goal", agent_cards=[], max_subtasks=8)
