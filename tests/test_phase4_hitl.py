"""Phase 4 human-in-the-loop, lifecycle and permission tests."""

from __future__ import annotations

import pytest

from agentforge.interaction.approval_service import ApprovalService
from agentforge.models.common import utc_now
from agentforge.models.task import TaskStatus
from agentforge.models.tool import (
    ApprovalRequest,
    ApprovalStatus,
    PermissionDecision,
    RiskLevel,
    ToolCall,
    ToolResultStatus,
)
from agentforge.models.workflow import WorkflowStatus
from agentforge.orchestration.state_machine import transition_task
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.tools.approval import ApprovalRequired, arguments_digest
from agentforge.tools.base import ToolExecutionContext, ToolRegistry
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.policy import ToolPermissionPolicy
from tests.phase4_support import (
    ApprovedEffectTool,
    EffectTool,
    FakeClock,
    Phase4Provider,
)


async def test_approval_request_is_persisted_bound_and_consumed_once(make_runtime):
    def coding(prompt: str):
        if "[TOOL_RESULTS]" in prompt:
            return {
                "action": "complete",
                "content": "Delete was approved and executed once.",
            }
        return {
            "action": "tool_call",
            "tool_calls": [
                {"name": "delete_file", "arguments": {"path": "stale.md"}}
            ],
        }

    provider = Phase4Provider(coding)
    runtime = make_runtime(provider=provider)
    workspace = runtime.workspaces.workspace_for("coding")
    (workspace / "stale.md").write_text("old", encoding="utf-8")

    workflow = await runtime.submit_goal("Delete the stale note safely")
    assert workflow.status == WorkflowStatus.PAUSED
    approvals = runtime.list_approvals(workflow.workflow_id)
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.status == ApprovalStatus.PENDING
    assert approval.tool_name == "delete_file"
    assert approval.arguments == {"path": "stale.md"}
    assert approval.argument_digest == arguments_digest({"path": "stale.md"})
    assert approval.risk_level in {RiskLevel.HIGH, RiskLevel.MEDIUM}
    assert approval.expires_at is not None
    assert (workspace / "stale.md").exists()

    task = next(
        task
        for task in runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("kind") == "coding"
    )
    assert task.status == TaskStatus.PENDING
    assert task.metadata["pending_approval_id"] == approval.approval_id

    approved = runtime.approve_tool(approval.approval_id, comment="safe to delete")
    assert approved.status == ApprovalStatus.APPROVED
    resumed = await runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert not (workspace / "stale.md").exists()

    records = runtime.list_tool_calls(workflow.workflow_id)
    assert [record.tool_name for record in records] == [
        "delete_file",
        "delete_file",
    ]
    assert records[0].status == ToolResultStatus.PENDING_APPROVAL
    assert records[0].approval_status == ApprovalStatus.PENDING
    assert records[1].status == ToolResultStatus.SUCCESS
    assert records[1].approval_status == ApprovalStatus.APPROVED

    consumed = runtime.get_approval(approval.approval_id)
    assert consumed is not None
    assert consumed.consumed_at is not None
    assert resumed.metrics.approval_requests == 1
    assert resumed.metrics.approval_approved == 1
    assert resumed.metrics.workflow_pauses == 1
    assert resumed.metrics.workflow_resumes == 1

    again = await runtime.resume_workflow(workflow.workflow_id)
    assert again.status == WorkflowStatus.COMPLETED
    assert len(runtime.list_tool_calls(workflow.workflow_id)) == 2


async def test_overwrite_requires_approval_and_rejection_preserves_content(
    make_runtime,
):
    def coding(prompt: str):
        if "[TOOL_RESULTS]" in prompt:
            return {
                "action": "complete",
                "content": "The overwrite was denied; original content preserved.",
            }
        return {
            "action": "tool_call",
            "tool_calls": [
                {
                    "name": "update_file",
                    "arguments": {
                        "path": "existing.md",
                        "content": "replacement",
                        "mode": "replace",
                    },
                }
            ],
        }

    runtime = make_runtime(provider=Phase4Provider(coding))
    workspace = runtime.workspaces.workspace_for("coding")
    (workspace / "existing.md").write_text("original", encoding="utf-8")

    workflow = await runtime.submit_goal("Overwrite the existing note if approved")
    assert workflow.status == WorkflowStatus.PAUSED
    approval = runtime.list_approvals(workflow.workflow_id)[0]
    assert approval.tool_name == "update_file"
    runtime.reject_tool(approval.approval_id, comment="keep original")
    resumed = await runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert (workspace / "existing.md").read_text(encoding="utf-8") == "original"
    records = runtime.list_tool_calls(workflow.workflow_id)
    assert records[-1].tool_name == "update_file"
    assert records[-1].status == ToolResultStatus.DENIED


async def test_write_file_overwrite_requires_approval(make_runtime):
    def coding(prompt: str):
        if "[TOOL_RESULTS]" in prompt:
            return {
                "action": "complete",
                "content": "The overwrite was denied; original content preserved.",
            }
        return {
            "action": "tool_call",
            "tool_calls": [
                {
                    "name": "write_file",
                    "arguments": {
                        "path": "existing.md",
                        "content": "replacement",
                        "overwrite": True,
                    },
                }
            ],
        }

    runtime = make_runtime(provider=Phase4Provider(coding))
    workspace = runtime.workspaces.workspace_for("coding")
    (workspace / "existing.md").write_text("original", encoding="utf-8")
    workflow = await runtime.submit_goal("Overwrite the existing note if approved")
    assert workflow.status == WorkflowStatus.PAUSED
    approval = runtime.list_approvals(workflow.workflow_id)[0]
    assert approval.risk_level.value == "high"
    runtime.reject_tool(approval.approval_id, comment="keep original")
    resumed = await runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert (workspace / "existing.md").read_text(encoding="utf-8") == "original"


async def test_rejected_approval_never_executes_the_side_effect(make_runtime):
    def coding(prompt: str):
        if "[TOOL_RESULTS]" in prompt:
            return {
                "action": "complete",
                "content": "The deletion was denied; the file was preserved.",
            }
        return {
            "action": "tool_call",
            "tool_calls": [
                {"name": "delete_file", "arguments": {"path": "keep.md"}}
            ],
        }

    runtime = make_runtime(provider=Phase4Provider(coding))
    workspace = runtime.workspaces.workspace_for("coding")
    (workspace / "keep.md").write_text("must stay", encoding="utf-8")

    workflow = await runtime.submit_goal("Delete the note only if approved")
    approval = runtime.list_approvals(workflow.workflow_id)[0]
    runtime.reject_tool(approval.approval_id, comment="do not delete")
    resumed = await runtime.resume_workflow(workflow.workflow_id)

    assert resumed.status == WorkflowStatus.COMPLETED
    assert (workspace / "keep.md").read_text(encoding="utf-8") == "must stay"
    records = runtime.list_tool_calls(workflow.workflow_id)
    assert records[-1].status == ToolResultStatus.DENIED
    assert records[-1].approval_status == ApprovalStatus.REJECTED
    assert runtime.get_approval(approval.approval_id).status == ApprovalStatus.REJECTED
    assert resumed.metrics.approval_rejected == 1


async def test_expired_approval_cannot_execute_and_reports_expired(
    tmp_path,
):
    clock = FakeClock(utc_now())
    store = SQLiteStore(tmp_path / "audit.db")
    events = EventLogger(tmp_path / "events.jsonl")
    try:
        service = ApprovalService(store, events, ttl_seconds=60, clock=clock)
        effects: list[str] = []
        registry = ToolRegistry()
        registry.register(EffectTool(effects))
        registry.register(ApprovedEffectTool(effects))
        executor = ToolExecutor(
            registry,
            store,
            events,
            ToolPermissionPolicy(),
            service,
            raise_on_pending=True,
            approval_ttl_seconds=60,
        )
        context = ToolExecutionContext(
            agent_id="coding",
            workflow_id="wf-expire",
            task_id="task-expire",
        )
        call = ToolCall(name="effect_approved", arguments={"label": "expired"})
        with pytest.raises(ApprovalRequired) as raised:
            await executor.execute(call, context)
        approval_id = raised.value.request.approval_id
        assert effects == []

        clock.advance(hours=2)
        result = await executor.execute(call, context, approval_id=approval_id)
        assert result.status == ToolResultStatus.DENIED
        assert "expired" in (result.error or "").lower()
        assert effects == []
        stored = service.get(approval_id)
        assert stored is not None
        assert stored.status == ApprovalStatus.EXPIRED
        assert stored.consumed_at is None
    finally:
        events.close()
        store.close()


async def test_approval_token_rejects_wrong_digest_scope_and_reuse(tmp_path):
    events = EventLogger(tmp_path / "events.jsonl")
    store = SQLiteStore(tmp_path / "audit.db")
    try:
        service = ApprovalService(store, events)
        request = ApprovalRequest(
            agent_id="coding",
            workflow_id="wf-bind",
            task_id="task-bind",
            tool_name="delete_file",
            action="delete_file",
            summary="delete one file",
            arguments={"path": "a.md"},
            argument_digest=arguments_digest({"path": "a.md"}),
        )
        await service.request_approval(request)
        service.approve(request.approval_id)

        wrong_digest = await service.consume_approval(
            request.approval_id,
            tool_name="delete_file",
            argument_digest=arguments_digest({"path": "b.md"}),
            workflow_id="wf-bind",
            task_id="task-bind",
            agent_id="coding",
        )
        assert wrong_digest.status == ApprovalStatus.REJECTED
        wrong_agent = await service.consume_approval(
            request.approval_id,
            tool_name="delete_file",
            argument_digest=request.argument_digest,
            workflow_id="wf-bind",
            task_id="task-bind",
            agent_id="coordinator",
        )
        assert wrong_agent.status == ApprovalStatus.REJECTED
        stored = service.get(request.approval_id)
        assert stored is not None and stored.consumed_at is None

        accepted = await service.consume_approval(
            request.approval_id,
            tool_name="delete_file",
            argument_digest=request.argument_digest,
            workflow_id="wf-bind",
            task_id="task-bind",
            agent_id="coding",
        )
        assert accepted.status == ApprovalStatus.APPROVED
        reused = await service.consume_approval(
            request.approval_id,
            tool_name="delete_file",
            argument_digest=request.argument_digest,
            workflow_id="wf-bind",
            task_id="task-bind",
            agent_id="coding",
        )
        assert reused.status == ApprovalStatus.REJECTED
        assert "consumed" in reused.reason
    finally:
        events.close()
        store.close()


async def test_clarification_survives_restart_and_answer_resumes(make_runtime):
    def clarify(prompt: str):
        if "[CLARIFICATION_ANSWER]" in prompt:
            return {
                "action": "complete",
                "content": "Retention set to 90 days per the human answer.",
            }
        return {
            "action": "clarify",
            "question": "Should retention be 30 or 90 days?",
            "clarification_context": "The goal omits the retention period.",
            "possible_interpretations": ["30 days", "90 days"],
        }

    first_runtime = make_runtime(provider=Phase4Provider(clarify))
    workflow = await first_runtime.submit_goal("Write the data retention policy")
    assert workflow.status == WorkflowStatus.PAUSED
    requests = first_runtime.list_clarifications(workflow.workflow_id)
    assert len(requests) == 1
    request = requests[0]
    assert request.question == "Should retention be 30 or 90 days?"
    assert request.possible_interpretations == ["30 days", "90 days"]
    assert request.workflow_id == workflow.workflow_id
    await first_runtime.aclose()

    second_runtime = make_runtime(provider=Phase4Provider(clarify))
    persisted = second_runtime.get_clarification(request.clarification_id)
    assert persisted is not None and persisted.status.value == "open"
    answered = second_runtime.answer_clarification(
        request.clarification_id, "90 days"
    )
    assert answered.status.value == "answered"
    resumed = await second_runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert resumed.metrics.clarifications == 1
    assert resumed.metrics.clarification_answered == 1
    assert resumed.metrics.workflow_pauses == 1
    assert resumed.metrics.workflow_resumes == 1


async def test_human_pause_interrupts_execution_and_resume_continues(make_runtime):
    state = {"paused": False}

    async def coding(prompt: str):
        runtime = state["runtime"]
        if not state["paused"]:
            state["paused"] = True
            latest = runtime.list_workflows(limit=1)[0]
            runtime.pause_workflow(latest.workflow_id)
            return {"action": "complete", "content": "This response is pre-pause."}
        return {"action": "complete", "content": "Completed after human resume."}

    runtime = make_runtime(provider=Phase4Provider(coding))
    state["runtime"] = runtime
    workflow = await runtime.submit_goal("Complete a pausable coding task")
    assert workflow.status == WorkflowStatus.PAUSED
    task = next(
        task
        for task in runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("kind") == "coding"
    )
    assert task.status == TaskStatus.PENDING

    resumed = await runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    coding_result = next(
        task.result
        for task in runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("kind") == "coding"
    )
    assert "after human resume" in (coding_result or "")
    assert resumed.metrics.workflow_pauses == 1
    assert resumed.metrics.workflow_resumes == 1


async def test_human_cancel_marks_tasks_and_supports_idempotent_status(make_runtime):
    state = {"cancelled": False}

    async def coding(prompt: str):
        runtime = state["runtime"]
        if not state["cancelled"]:
            state["cancelled"] = True
            latest = runtime.list_workflows(limit=1)[0]
            await runtime.cancel_workflow(latest.workflow_id, reason="operator stop")
        return {"action": "complete", "content": "Should not be recorded."}

    runtime = make_runtime(provider=Phase4Provider(coding))
    state["runtime"] = runtime
    workflow = await runtime.submit_goal("Run a cancellable coding task")
    assert workflow.status == WorkflowStatus.CANCELLED
    assert workflow.final_result is None
    for task in runtime.list_tasks(workflow.workflow_id):
        assert task.status == TaskStatus.CANCELLED
    assert runtime.list_approvals(workflow.workflow_id) == []
    assert runtime.list_clarifications(workflow.workflow_id) == []
    assert workflow.metrics.workflow_cancellations == 1
    again = await runtime.resume_workflow(workflow.workflow_id)
    assert again.status == WorkflowStatus.CANCELLED


async def test_restart_recovers_running_task_without_duplicate_work(make_runtime):
    state = {"paused": False}

    def pause_once(prompt: str):
        runtime = state["runtime"]
        if not state["paused"]:
            state["paused"] = True
            latest = runtime.list_workflows(limit=1)[0]
            runtime.pause_workflow(latest.workflow_id)
        return {"action": "complete", "content": "Pre-pause completion."}

    first_runtime = make_runtime(provider=Phase4Provider(pause_once))
    state["runtime"] = first_runtime
    workflow = await first_runtime.submit_goal("Recover a task after a crash")
    assert workflow.status == WorkflowStatus.PAUSED
    task = next(
        task
        for task in first_runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("kind") == "coding"
    )
    assert task.status == TaskStatus.PENDING
    # Simulate a process dying after the task was marked RUNNING.
    transition_task(task, TaskStatus.RUNNING)
    first_runtime.store.save_task(task)
    await first_runtime.aclose()

    def resume_complete(prompt: str):
        return {
            "action": "complete",
            "content": "Completed after restart recovery.",
        }

    second_runtime = make_runtime(provider=Phase4Provider(resume_complete))
    resumed = await second_runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    recovered = next(
        task
        for task in second_runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("kind") == "coding"
    )
    assert recovered.status == TaskStatus.COMPLETED
    assert recovered.metadata.get("recovered_from_running") is True
    assert len(recovered.metadata.get("execution_ids", [])) == 2
    persisted = second_runtime.get_workflow(workflow.workflow_id)
    assert persisted is not None
    assert persisted.status == WorkflowStatus.COMPLETED


async def test_tool_permissions_are_enforced_before_approval(make_runtime):
    runtime = make_runtime(provider=Phase4Provider())
    executor = runtime.tool_executor
    assert executor is not None
    denied_calls = [
        ("coordinator", "delete_file", {"path": "a.md"}),
        ("research", "delete_file", {"path": "a.md"}),
        ("reviewer", "write_file", {"path": "a.md", "content": "x"}),
        ("reviewer", "delete_file", {"path": "a.md"}),
    ]
    for agent_id, tool_name, arguments in denied_calls:
        result = await executor.execute(
            ToolCall(name=tool_name, arguments=arguments),
            ToolExecutionContext(
                agent_id=agent_id,
                workflow_id="wf-permissions",
                task_id=f"task-{agent_id}",
            ),
        )
        assert result.status == ToolResultStatus.DENIED
        assert "permission denied" in (result.error or "").lower()

    coding_call = ToolCall(name="delete_file", arguments={"path": "a.md"})
    coding_context = ToolExecutionContext(
        agent_id="coding",
        workflow_id="wf-permissions",
        task_id="task-coding",
    )
    with pytest.raises(ApprovalRequired) as raised:
        await executor.execute(coding_call, coding_context)
    approval = raised.value.request
    assert approval.agent_id == "coding"

    forged = await runtime.approval_service.consume_approval(
        approval.approval_id,
        tool_name="delete_file",
        argument_digest=approval.argument_digest,
        workflow_id="wf-permissions",
        task_id="task-coding",
        agent_id="coordinator",
    )
    assert forged.status == ApprovalStatus.REJECTED
    coordinator = await executor.execute(
        coding_call,
        ToolExecutionContext(
            agent_id="coordinator",
            workflow_id="wf-permissions",
            task_id="task-coding",
        ),
        approval_id=approval.approval_id,
    )
    assert coordinator.status == ToolResultStatus.DENIED

    records = runtime.list_tool_calls("wf-permissions")
    denied_records = [
        record
        for record in records
        if record.permission_decision == PermissionDecision.DENIED
    ]
    assert len(denied_records) >= 4
    assert all(
        record.status == ToolResultStatus.DENIED for record in denied_records
    )


async def test_duplicate_side_effects_are_prevented_by_replay_cache(make_runtime):
    effects: list[str] = []

    def coding(prompt: str):
        if "[TOOL_RESULTS]" in prompt:
            return {"action": "complete", "content": "Both effects completed once."}
        return {
            "action": "tool_call",
            "tool_calls": [
                {"name": "effect_write", "arguments": {"label": "plain"}},
                {"name": "effect_approved", "arguments": {"label": "approved"}},
            ],
        }

    runtime = make_runtime(provider=Phase4Provider(coding))
    runtime.tools.register(EffectTool(effects))
    runtime.tools.register(ApprovedEffectTool(effects))
    workflow = await runtime.submit_goal("Execute two effects exactly once")
    assert workflow.status == WorkflowStatus.PAUSED
    assert effects == ["plain"]
    approval = runtime.list_approvals(workflow.workflow_id)[0]
    runtime.approve_tool(approval.approval_id)
    resumed = await runtime.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert effects == ["plain", "approved"]

    records = runtime.list_tool_calls(workflow.workflow_id)
    replayed = [
        record for record in records if record.metadata.get("replayed") is True
    ]
    assert len(replayed) == 1
    assert replayed[0].tool_name == "effect_write"
    executed = [
        record
        for record in records
        if record.tool_name == "effect_approved"
        and record.status == ToolResultStatus.SUCCESS
    ]
    assert len(executed) == 1

    await runtime.resume_workflow(workflow.workflow_id)
    assert effects == ["plain", "approved"]
