"""Phase 4 delegation acceptance, rejection, reassignment and depth tests."""

from __future__ import annotations

from agentforge.agents.research import ResearchAgent
from agentforge.models.agent import AgentProfile, AgentRole
from agentforge.models.message import MessageType
from agentforge.models.task import TaskStatus
from agentforge.models.workflow import WorkflowStatus
from agentforge.orchestration.assignment import CapabilityAssigner
from tests.phase4_support import Phase4Provider


def _delegate(capability: str, description: str) -> dict:
    return {
        "action": "delegate",
        "reasoning": "The parent task needs specialised work.",
        "delegation": {
            "reason": "delegate to a specialised agent",
            "subtasks": [
                {
                    "description": description,
                    "required_capabilities": [capability],
                    "kind": "delegated",
                }
            ],
        },
    }


def _add_analyst(runtime) -> ResearchAgent:
    profile = AgentProfile(
        agent_id="analyst",
        name="AnalystAgent",
        role=AgentRole.RESEARCH,
        capabilities=["research", "analysis"],
        description="Second research-capable agent used to prove reassignment.",
    )
    agent = ResearchAgent(
        profile=profile,
        provider=runtime.provider,
        memory=runtime.memory,
        tools=runtime.tools,
        memory_recall_limit=runtime.settings.memory_recall_limit,
        tool_executor=runtime.tool_executor,
        max_tool_calls=runtime.settings.max_tool_calls_per_task,
    )
    runtime.agents.register(agent)
    runtime.bus.register_agent(agent)
    runtime.orchestrator.assigner = CapabilityAssigner(runtime.agents.cards())
    return agent


async def test_delegation_rejection_reassigns_to_another_capable_agent(make_runtime):
    def coding(prompt: str):
        if "Replacement analysis complete" in prompt:
            return {
                "action": "complete",
                "content": "Parent work completed with the replacement analysis.",
            }
        return _delegate("analysis", "Assess implementation options")

    delegated_calls = {"count": 0}

    def delegated(prompt: str, role: str):
        delegated_calls["count"] += 1
        if delegated_calls["count"] == 1:
            assert role == "research"
            return {
                "action": "reject_assignment",
                "reason": "This is outside my verified analysis scope.",
            }
        return {
            "action": "complete",
            "content": "Replacement analysis complete.",
        }

    provider = Phase4Provider(
        coding, handlers={"delegated": delegated}
    )
    runtime = make_runtime(provider=provider)
    _add_analyst(runtime)

    workflow = await runtime.submit_goal("Assess implementation options safely")
    assert workflow.status == WorkflowStatus.COMPLETED
    tasks = runtime.list_tasks(workflow.workflow_id)
    delegated_tasks = [
        task for task in tasks if task.metadata.get("origin") == "delegation"
    ]
    assert len(delegated_tasks) == 2
    rejected = next(
        task
        for task in delegated_tasks
        if task.status == TaskStatus.CANCELLED
    )
    replacement = next(
        task
        for task in delegated_tasks
        if task.metadata.get("reassigned_from") == rejected.task_id
    )
    assert rejected.assigned_agent == "analyst"
    assert rejected.metadata.get("reassigned_to") == "research"
    assert replacement.assigned_agent == "research"
    assert replacement.status == TaskStatus.COMPLETED
    assert workflow.metrics.reassignments == 1
    assert workflow.metrics.delegation_tasks == 2
    assert workflow.metrics.delegation_completed == 1

    events = runtime.list_workflow_events(workflow.workflow_id)
    assert any(event.event_type == "task_reassigned" for event in events)
    reassigned_event = next(
        event for event in events if event.event_type == "task_reassigned"
    )
    assert reassigned_event.metadata["from_agent"] == "analyst"
    assert reassigned_event.metadata["to_agent"] == "research"


async def test_two_level_agent_to_agent_delegation_end_to_end(make_runtime):
    def coding(prompt: str):
        if "Research child complete" in prompt:
            return {
                "action": "complete",
                "content": "Parent completed after two delegation levels.",
            }
        return _delegate("research", "Research the implementation constraints")

    def delegated(prompt: str, role: str):
        if role == "research":
            if "Review child complete" in prompt:
                return {
                    "action": "complete",
                    "content": "Research child complete after review.",
                }
            return _delegate("review", "Review the proposed constraints")
        if role == "reviewer":
            return {
                "action": "complete",
                "content": "Review child complete.",
            }
        return {"action": "complete", "content": f"Completed for {role}."}

    provider = Phase4Provider(
        coding, handlers={"delegated": delegated}
    )
    runtime = make_runtime(provider=provider)
    workflow = await runtime.submit_goal("Run a two-level delegation workflow")
    assert workflow.status == WorkflowStatus.COMPLETED, workflow.error

    tasks = runtime.list_tasks(workflow.workflow_id)
    delegated = [
        task for task in tasks if task.metadata.get("origin") == "delegation"
    ]
    assert len(delegated) == 2
    parent_child = min(delegated, key=lambda task: task.depth)
    grandchild = max(delegated, key=lambda task: task.depth)
    assert grandchild.depth == parent_child.depth + 1
    assert parent_child.assigned_agent == "research"
    assert grandchild.assigned_agent == "reviewer"
    assert grandchild.metadata.get("delegated_by") == "research"
    assert grandchild.parent_task_id == parent_child.task_id
    assert all(task.status == TaskStatus.COMPLETED for task in delegated)
    assert workflow.metrics.delegation_count == 2
    assert workflow.metrics.max_depth == grandchild.depth
    assert workflow.metrics.delegation_success_rate == 1.0

    delegation_messages = [
        message
        for message in runtime.store.list_messages(workflow.conversation_id)
        if message.message_type == MessageType.DELEGATION
    ]
    senders = {message.sender for message in delegation_messages}
    assert {"coding", "research"} <= senders
