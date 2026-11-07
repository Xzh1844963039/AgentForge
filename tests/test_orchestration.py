"""End-to-end orchestration tests with the deterministic mock provider."""

from __future__ import annotations

import json

import pytest

from agentforge.agents.registry import AgentNotFoundError
from agentforge.llm.base import LLMProviderError, LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider
from agentforge.models.message import MessageType
from agentforge.models.task import TaskStatus
from agentforge.models.workflow import WorkflowStatus

DEMO_GOAL = (
    "Research the benefits of multi-agent systems, propose a Python "
    "implementation plan, and review the plan for potential issues."
)


class DelegatingCodingProvider(MockProvider):
    """Delegates the first coding task, then completes normally on resume."""

    def __init__(self, *, always: bool = False, latency_ms: int = 0) -> None:
        super().__init__(latency_ms=latency_ms)
        self.always = always
        self.delegated = False

    async def complete(
        self,
        messages,
        *,
        purpose=None,
        temperature=None,
        max_tokens=None,
    ):
        should_delegate = (
            purpose is not None
            and purpose.startswith("execute:coding:")
            and (self.always or not self.delegated)
        )
        if should_delegate:
            self.delegated = True
            payload = {
                "action": "delegate",
                "content": "The plan needs a focused validation step.",
                "delegation": {
                    "reason": "Validate implementation assumptions",
                    "subtasks": [
                        {
                            "description": (
                                "Validate the Python plan assumptions about async "
                                "orchestration and persistence."
                            ),
                            "required_capabilities": ["research"],
                            "kind": "delegated",
                        }
                    ],
                },
                "reasoning": "Test delegation path.",
            }
            return LLMResponse(
                content=json.dumps(payload),
                provider=self.name,
                model=self.model,
                usage=TokenUsage(
                    prompt_tokens=10, completion_tokens=10, total_tokens=20
                ),
                latency_ms=0.0,
            )
        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class FailingResearchProvider(MockProvider):
    async def complete(
        self,
        messages,
        *,
        purpose=None,
        temperature=None,
        max_tokens=None,
    ):
        if purpose is not None and purpose.startswith("execute:research:"):
            raise LLMProviderError("simulated provider failure", provider="mock")
        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class CapabilityMismatchProvider(MockProvider):
    async def complete(
        self,
        messages,
        *,
        purpose=None,
        temperature=None,
        max_tokens=None,
    ):
        if purpose == "plan":
            payload = {
                "goal_summary": "impossible assignment",
                "subtasks": [
                    {
                        "key": "fusion",
                        "description": "Do quantum fusion research",
                        "required_capabilities": ["quantum_fusion"],
                        "depends_on": [],
                        "kind": "research",
                    }
                ],
            }
            return LLMResponse(
                content=json.dumps(payload),
                provider=self.name,
                model=self.model,
                usage=TokenUsage(
                    prompt_tokens=5, completion_tokens=5, total_tokens=10
                ),
                latency_ms=0.0,
            )
        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def work_tasks(runtime, workflow_id):
    return [
        task
        for task in runtime.list_tasks(workflow_id)
        if task.metadata.get("role") != "root"
    ]


def by_kind(tasks):
    return {str(task.metadata.get("kind")): task for task in tasks}


async def test_full_mock_workflow_meets_phase1_demo(make_runtime):
    provider = MockProvider(latency_ms=2)
    runtime = make_runtime(provider=provider)

    workflow = await runtime.submit_goal(DEMO_GOAL)

    assert workflow.status is WorkflowStatus.COMPLETED
    assert workflow.error is None
    assert workflow.final_result is not None
    assert "Final Report" in workflow.final_result
    assert DEMO_GOAL[:40] in workflow.final_result

    tasks = work_tasks(runtime, workflow.workflow_id)
    assert len(tasks) == 5
    kinds = by_kind(tasks)
    assert set(kinds) == {"research", "coding", "synthesis", "review", "finalize"}
    assert all(task.status is TaskStatus.COMPLETED for task in tasks)
    assert all(task.started_at and task.finished_at for task in tasks)

    assert kinds["research"].assigned_agent == "research"
    assert kinds["coding"].assigned_agent == "coding"
    assert kinds["synthesis"].assigned_agent == "coordinator"
    assert kinds["review"].assigned_agent == "reviewer"
    assert kinds["finalize"].assigned_agent == "coordinator"
    assert kinds["coding"].dependencies == [kinds["research"].task_id]
    assert kinds["synthesis"].dependencies == [
        kinds["research"].task_id,
        kinds["coding"].task_id,
    ]
    assert kinds["review"].dependencies == [
        kinds["synthesis"].task_id,
        kinds["research"].task_id,
        kinds["coding"].task_id,
    ]
    assert set(kinds["finalize"].dependencies) == {
        kinds["synthesis"].task_id,
        kinds["review"].task_id,
    }

    metrics = workflow.metrics
    assert metrics.total_tasks == 5
    assert metrics.completed_tasks == 5
    assert metrics.failed_tasks == 0
    assert metrics.task_completion_rate == 1.0
    assert metrics.llm_calls == 6  # 1 planning + 5 task executions
    assert metrics.prompt_tokens > 0
    assert metrics.completion_tokens > 0
    assert metrics.total_task_seconds > 0
    assert workflow.duration_seconds() is not None
    assert workflow.duration_seconds() > 0

    executions = runtime.store.list_executions(workflow.workflow_id)
    assert len(executions) == 6
    assert [execution.purpose for execution in executions] == [
        "plan",
        "execute:research:research",
        "execute:coding:coding",
        "execute:synthesis:coordinator",
        "execute:review:reviewer",
        "execute:finalize:coordinator",
    ]
    assert all(execution.finished_at is not None for execution in executions)
    assert all(execution.latency_ms is not None for execution in executions)

    # Group conversation: human goal, plan, status notices, final report.
    group_messages = runtime.replay(workflow.conversation_id)
    assert group_messages[0].message_type is MessageType.GROUP
    assert group_messages[0].sender == "human"
    assert group_messages[0].content == DEMO_GOAL
    assert any(message.message_type is MessageType.PLAN for message in group_messages)
    final_messages = [
        message
        for message in group_messages
        if message.message_type is MessageType.TASK_RESULT
        and message.metadata.get("final")
    ]
    assert len(final_messages) == 1
    assert final_messages[0].sender == "coordinator"
    assert final_messages[0].content == workflow.final_result
    assert group_messages[0].receiver == workflow.conversation_id

    # Direct agent-to-agent task request and result.
    all_messages = runtime.store.list_messages()
    assert any(
        message.message_type is MessageType.TASK_REQUEST
        and message.sender == "coordinator"
        and message.receiver == "research"
        for message in all_messages
    )
    assert any(
        message.message_type is MessageType.TASK_RESULT
        and message.sender == "research"
        and message.receiver == "coordinator"
        for message in all_messages
    )
    assert any(
        message.message_type is MessageType.TASK_RESULT
        and message.sender == "reviewer"
        and message.receiver == "coordinator"
        for message in all_messages
    )

    # Independent review content is persisted on the review task.
    review = kinds["review"].result or ""
    assert "Verdict:" in review

    # Per-agent memory namespaces.
    assert runtime.memory.count("research", namespace="experience") == 1
    assert runtime.memory.count("coding", namespace="experience") == 1
    assert runtime.memory.count("reviewer", namespace="experience") == 1
    assert runtime.memory.count("coordinator", namespace="experience") == 2


async def test_group_conversation_is_created_with_all_agents(make_runtime):
    runtime = make_runtime(provider=MockProvider())
    workflow = await runtime.submit_goal("Simple goal")
    conversation = runtime.store.get_conversation(workflow.conversation_id)
    assert conversation is not None
    assert conversation.conversation_type.value == "group"
    assert set(conversation.participants) == {
        "human",
        "coordinator",
        "research",
        "coding",
        "reviewer",
    }


async def test_private_chat_is_persisted_with_execution_and_memory(make_runtime):
    provider = MockProvider()
    runtime = make_runtime(provider=provider)

    exchange = await runtime.chat_with_agent("research", "What are you good at?")
    assert exchange.outgoing.sender == "research"
    assert exchange.outgoing.receiver == "human"
    assert "ResearchAgent here" in exchange.outgoing.content
    assert exchange.incoming.sender == "human"

    transcript = runtime.replay(exchange.conversation.conversation_id)
    assert [message.content for message in transcript] == [
        "What are you good at?",
        exchange.outgoing.content,
    ]

    executions = runtime.store.list_executions()
    assert len(executions) == 1
    assert executions[0].purpose == "chat:research"
    assert executions[0].status.value == "completed"

    assert runtime.memory.count("research", namespace="conversation") == 1

    await runtime.chat_with_agent("research", "And what about review?")
    prompt = provider.calls[-1]["messages"][1]["content"]
    assert "What are you good at?" in prompt  # history is supplied to the agent

    with pytest.raises(AgentNotFoundError):
        await runtime.chat_with_agent("missing", "hello")


async def test_agent_initiated_delegation_resumes_parent(make_runtime):
    provider = DelegatingCodingProvider()
    runtime = make_runtime(provider=provider)

    workflow = await runtime.submit_goal(DEMO_GOAL)
    assert workflow.status is WorkflowStatus.COMPLETED, workflow.error

    tasks = work_tasks(runtime, workflow.workflow_id)
    coding = next(task for task in tasks if task.metadata.get("kind") == "coding")
    delegated = [
        task for task in tasks if task.metadata.get("origin") == "delegation"
    ]
    assert len(delegated) == 1
    child = delegated[0]
    assert child.parent_task_id == coding.task_id
    assert child.depth == 2
    assert child.assigned_agent == "research"
    assert child.status is TaskStatus.COMPLETED
    assert coding.status is TaskStatus.COMPLETED
    assert coding.metadata["delegation_rounds"] == 1

    assert workflow.metrics.delegation_count == 1
    assert workflow.metrics.max_depth == 2
    assert len(tasks) == 6
    assert workflow.metrics.completed_tasks == 6

    group_messages = runtime.replay(workflow.conversation_id)
    delegation_messages = [
        message
        for message in group_messages
        if message.message_type is MessageType.DELEGATION
    ]
    assert len(delegation_messages) == 1
    assert delegation_messages[0].sender == "coding"


async def test_max_delegation_depth_fails_task(make_runtime):
    provider = DelegatingCodingProvider(always=True)
    runtime = make_runtime(provider=provider, max_delegation_depth=1)

    workflow = await runtime.submit_goal(DEMO_GOAL)

    assert workflow.status is WorkflowStatus.FAILED
    assert workflow.error is not None
    assert "maximum delegation depth" in workflow.error.lower()
    tasks = work_tasks(runtime, workflow.workflow_id)
    assert not [
        task for task in tasks if task.metadata.get("origin") == "delegation"
    ]
    coding = next(task for task in tasks if task.metadata.get("kind") == "coding")
    assert coding.status is TaskStatus.FAILED
    assert "maximum delegation depth" in (coding.error or "").lower()


async def test_max_execution_steps_aborts_workflow(make_runtime):
    runtime = make_runtime(provider=MockProvider(), max_execution_steps=2)

    workflow = await runtime.submit_goal(DEMO_GOAL)

    assert workflow.status is WorkflowStatus.FAILED
    assert workflow.error is not None
    assert "max_execution_steps" in workflow.error
    tasks = work_tasks(runtime, workflow.workflow_id)
    completed = [task for task in tasks if task.status is TaskStatus.COMPLETED]
    failed = [task for task in tasks if task.status is TaskStatus.FAILED]
    assert len(completed) == 2
    assert len(failed) == 3
    assert workflow.final_result is None


async def test_provider_failure_propagates_to_dependents(make_runtime):
    runtime = make_runtime(provider=FailingResearchProvider())

    workflow = await runtime.submit_goal(DEMO_GOAL)

    assert workflow.status is WorkflowStatus.FAILED
    assert workflow.error is not None
    assert "simulated provider failure" in workflow.error
    tasks = work_tasks(runtime, workflow.workflow_id)
    research = next(task for task in tasks if task.metadata.get("kind") == "research")
    assert research.status is TaskStatus.FAILED
    assert "simulated provider failure" in (research.error or "")
    assert workflow.metrics.completed_tasks == 0
    assert workflow.metrics.failed_tasks == 5

    executions = runtime.store.list_executions(workflow.workflow_id)
    failed_executions = [
        execution for execution in executions if execution.status.value == "failed"
    ]
    assert len(failed_executions) == 1
    assert "simulated provider failure" in (failed_executions[0].error or "")


async def test_unmatched_capability_fails_workflow(make_runtime):
    runtime = make_runtime(provider=CapabilityMismatchProvider())

    workflow = await runtime.submit_goal("Use a capability nobody has")

    assert workflow.status is WorkflowStatus.FAILED
    assert workflow.error is not None
    assert "no registered agent covers" in workflow.error
    assert work_tasks(runtime, workflow.workflow_id) == []
    # The planning call succeeded and remains observable.
    executions = runtime.store.list_executions(workflow.workflow_id)
    assert len(executions) == 1
    assert executions[0].purpose == "plan"


async def test_final_review_can_be_disabled(make_runtime):
    runtime = make_runtime(provider=MockProvider(), enable_final_review=False)

    workflow = await runtime.submit_goal(DEMO_GOAL)
    assert workflow.status is WorkflowStatus.COMPLETED
    tasks = work_tasks(runtime, workflow.workflow_id)
    kinds = by_kind(tasks)
    assert set(kinds) == {"research", "coding", "synthesis", "finalize"}
    assert "review" not in kinds
    assert kinds["finalize"].dependencies == [kinds["synthesis"].task_id]


async def test_workflow_survives_runtime_restart(make_runtime):
    from agentforge.llm.mock import MockProvider

    first = make_runtime(provider=MockProvider())
    workflow = await first.submit_goal(DEMO_GOAL)
    assert workflow.status is WorkflowStatus.COMPLETED
    await first.aclose()

    second = make_runtime(provider=MockProvider())
    loaded = second.get_workflow(workflow.workflow_id)
    assert loaded is not None
    assert loaded.status is WorkflowStatus.COMPLETED
    assert loaded.final_result == workflow.final_result
    assert second.count_messages(workflow.conversation_id) > 0
    tasks = second.list_tasks(workflow.workflow_id)
    assert all(task.status is TaskStatus.COMPLETED for task in tasks)
    transcript = second.replay(workflow.conversation_id)
    assert transcript[-1].content == workflow.final_result
