"""Offline tests for the Compact Planner protocol and one-request probe."""

from __future__ import annotations

import json

import pytest

from agentforge.agents import build_default_agents
from agentforge.agents.coordinator import (
    COMPACT_PLANNER_PROTOCOL,
    build_planner_messages,
    resolve_planner_protocol,
)
from agentforge.benchmark.hkgai_compact_planner import (
    compact_planner_messages,
    validate_compact_plan,
)
from agentforge.llm.mock import MockProvider
from agentforge.memory import InMemoryMemoryStore
from agentforge.orchestration.assignment import CapabilityAssigner
from agentforge.orchestration.errors import PlanningError, TaskAssignmentError
from agentforge.tools import ToolRegistry


def _valid_plan_payload() -> dict:
    return {
        "goal_summary": "Create a simple note.",
        "subtasks": [
            {
                "key": "write",
                "description": "Create notes/simple.md containing hello.",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            }
        ],
    }


def _multi_task_payload() -> dict:
    return {
        "goal_summary": "Research and write.",
        "subtasks": [
            {
                "key": "research",
                "description": "Research the topic.",
                "required_capabilities": ["research"],
                "depends_on": [],
                "kind": "research",
            },
            {
                "key": "write",
                "description": "Write the final note.",
                "required_capabilities": ["coding"],
                "depends_on": ["research"],
                "kind": "coding",
            },
        ],
    }


def _coordinator(planner_protocol: str, provider: MockProvider | None = None):
    provider = provider or MockProvider()
    agents = build_default_agents(
        provider=provider,
        memory=InMemoryMemoryStore(),
        tools=ToolRegistry(),
        planner_protocol=planner_protocol,
    )
    return next(a for a in agents if a.agent_id == "coordinator"), agents, provider


def test_compact_prompt_is_significantly_shorter_than_full():
    _, agents, _ = _coordinator("full")
    full = build_planner_messages(
        "Create notes/simple.md containing hello.",
        [a.card for a in agents],
        3,
        protocol="full",
    )
    compact = compact_planner_messages()
    full_chars = sum(len(message.content) for message in full)
    compact_chars = sum(len(message.content) for message in compact)
    assert compact_chars < 1100
    assert full_chars > 2000
    assert compact_chars < full_chars * 0.5
    assert COMPACT_PLANNER_PROTOCOL in [message.content for message in compact]
    assert "[AGENTS]" in compact[1].content


def test_compact_planning_validates_one_task():
    content = json.dumps(_valid_plan_payload())
    assert validate_compact_plan(content) == (True, True, True, 1)


def test_compact_planning_supports_dynamic_dag():
    content = json.dumps(_multi_task_payload())
    valid_json, plan_valid, capability_valid, count = validate_compact_plan(
        content
    )
    assert valid_json is True
    assert plan_valid is False  # probe requires exactly one minimal subtask
    assert capability_valid is True
    assert count == 2
    # The general Planner validator still supports multi-task DAGs.
    from agentforge.orchestration.planner import validate_plan
    from agentforge.models.task import ExecutionPlan

    ordered = validate_plan(ExecutionPlan.model_validate(_multi_task_payload()), max_subtasks=8)
    assert [item.key for item in ordered] == ["research", "write"]


def test_compact_planning_rejects_invalid_dependencies_and_cycles():
    invalid_dep = {
        "goal_summary": "bad",
        "subtasks": [
            {
                "key": "one",
                "description": "one",
                "required_capabilities": ["coding"],
                "depends_on": ["missing"],
                "kind": "coding",
            }
        ],
    }
    cycle = {
        "goal_summary": "cycle",
        "subtasks": [
            {
                "key": "a",
                "description": "a",
                "required_capabilities": ["research"],
                "depends_on": ["b"],
                "kind": "research",
            },
            {
                "key": "b",
                "description": "b",
                "required_capabilities": ["coding"],
                "depends_on": ["a"],
                "kind": "coding",
            },
        ],
    }
    assert validate_compact_plan(json.dumps(invalid_dep))[1] is False
    assert validate_compact_plan(json.dumps(cycle))[1] is False


def test_compact_plan_rejects_malformed_json():
    assert validate_compact_plan("this is not a JSON plan") == (
        False,
        False,
        False,
        0,
    )


def test_compact_plan_respects_max_subtasks():
    payload = {
        "goal_summary": "too many independent tasks",
        "subtasks": [
            {
                "key": f"task_{index}",
                "description": f"independent task {index}",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            }
            for index in range(4)
        ],
    }
    valid_json, plan_valid, capability_valid, count = validate_compact_plan(
        json.dumps(payload)
    )
    assert valid_json is True
    assert plan_valid is False
    assert capability_valid is False
    assert count == 4  # exceeds the probe's MAX_SUBTASKS=3


def test_compact_plan_unknown_capability_fails_dispatch():
    payload = {
        "goal_summary": "unsupported capability",
        "subtasks": [
            {
                "key": "k1",
                "description": "do unsupported work",
                "required_capabilities": ["quantum_physics"],
                "depends_on": [],
                "kind": "generic",
            }
        ],
    }
    valid_json, plan_valid, capability_valid, count = validate_compact_plan(
        json.dumps(payload)
    )
    assert (valid_json, plan_valid, capability_valid, count) == (
        True,
        True,
        False,
        1,
    )
    _, agents, _ = _coordinator("compact")
    with pytest.raises(TaskAssignmentError, match="no registered agent"):
        CapabilityAssigner([agent.card for agent in agents]).assign(
            ["quantum_physics"]
        )


async def test_compact_coordinator_uses_compact_prompt_and_repairs():
    provider = MockProvider(
        scripted_responses=[
            "not json",
            json.dumps(_valid_plan_payload()),
        ]
    )
    coordinator, agents, provider = _coordinator("compact", provider)
    result = await coordinator.plan_goal(
        "Create notes/simple.md containing hello.",
        agent_cards=[a.card for a in agents],
        max_subtasks=3,
    )
    assert len(result.attempts) == 2
    assert result.attempts[1].error is None
    system_prompt = provider.calls[0]["messages"][0]["content"]
    user_prompt = provider.calls[0]["messages"][1]["content"]
    assert system_prompt == COMPACT_PLANNER_PROTOCOL
    assert "[AGENTS]" in user_prompt
    assert len(system_prompt) + len(user_prompt) < 1100


async def test_compact_coordinator_rejects_cycle():
    cyclic = json.dumps(
        {
            "goal_summary": "cycle",
            "subtasks": [
                {
                    "key": "a",
                    "description": "a",
                    "required_capabilities": ["research"],
                    "depends_on": ["b"],
                    "kind": "research",
                },
                {
                    "key": "b",
                    "description": "b",
                    "required_capabilities": ["coding"],
                    "depends_on": ["a"],
                    "kind": "coding",
                },
            ],
        }
    )
    provider = MockProvider(scripted_responses=[cyclic, cyclic])
    coordinator, agents, _ = _coordinator("compact", provider)
    with pytest.raises(PlanningError, match="cycle"):
        await coordinator.plan_goal(
            "cycle goal",
            agent_cards=[a.card for a in agents],
            max_subtasks=3,
        )


def test_auto_protocol_uses_compact_only_for_hkgai():
    assert resolve_planner_protocol("auto", "hkgai") == "compact"
    assert resolve_planner_protocol("auto", "mock") == "full"
    assert resolve_planner_protocol("full", "hkgai") == "full"
    assert resolve_planner_protocol("compact", "mock") == "compact"
