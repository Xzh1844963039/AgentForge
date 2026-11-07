"""Domain model tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.models.agent import AgentProfile, AgentRole
from agentforge.models.message import Message, MessageType
from agentforge.models.task import (
    AgentAction,
    AgentActionType,
    DelegatedSubtask,
    DelegationRequest,
    ExecutionPlan,
    SubtaskSpec,
    Task,
    TaskStatus,
)


def test_message_ids_are_unique_and_content_required():
    first = Message(
        conversation_id="conv",
        sender="human",
        receiver="research",
        message_type=MessageType.DIRECT,
        content="hello",
    )
    second = Message(
        conversation_id="conv",
        sender="human",
        receiver="research",
        message_type=MessageType.DIRECT,
        content="hello again",
    )
    assert first.message_id != second.message_id
    with pytest.raises(ValidationError):
        Message(
            conversation_id="conv",
            sender="human",
            message_type=MessageType.DIRECT,
            content="   ",
        )


def test_task_terminal_and_duration():
    task = Task(workflow_id="wf", description="do work")
    assert task.status is TaskStatus.PENDING
    assert not task.is_terminal
    assert task.duration_seconds() is None

    task.status = TaskStatus.RUNNING
    from datetime import timedelta

    from agentforge.models.common import utc_now

    task.started_at = utc_now() - timedelta(seconds=2)
    task.finished_at = utc_now()
    assert task.duration_seconds() >= 1.9
    task.status = TaskStatus.COMPLETED
    assert task.is_terminal


def test_agent_action_validation():
    complete = AgentAction(action=AgentActionType.COMPLETE, content="done")
    assert complete.content == "done"

    with pytest.raises(ValidationError):
        AgentAction(action=AgentActionType.COMPLETE, content=" ")

    with pytest.raises(ValidationError):
        AgentAction(action=AgentActionType.DELEGATE)

    with pytest.raises(ValidationError):
        AgentAction(action=AgentActionType.FAIL)

    delegation = DelegationRequest(
        subtasks=[
            DelegatedSubtask(
                description="check assumptions",
                required_capabilities=["research"],
            )
        ]
    )
    delegated = AgentAction(
        action=AgentActionType.DELEGATE,
        content="need help",
        delegation=delegation,
    )
    assert delegated.delegation is not None


def test_execution_plan_rejects_duplicate_keys():
    with pytest.raises(ValidationError):
        ExecutionPlan(
            subtasks=[
                SubtaskSpec(
                    key="same", description="a", required_capabilities=["research"]
                ),
                SubtaskSpec(
                    key="same", description="b", required_capabilities=["coding"]
                ),
            ]
        )


def test_subtask_requires_capability():
    with pytest.raises(ValidationError):
        SubtaskSpec(key="x", description="a", required_capabilities=[])


def test_agent_profile_normalizes_capabilities():
    profile = AgentProfile(
        agent_id="test-agent",
        name="Test",
        role=AgentRole.RESEARCH,
        capabilities=["Information Retrieval", "analysis", "Analysis"],
    )
    assert profile.capabilities == ["information_retrieval", "analysis"]
