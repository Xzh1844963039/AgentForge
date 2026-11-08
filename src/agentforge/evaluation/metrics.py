"""Workflow metrics derived from durable task and execution records."""

from __future__ import annotations

from datetime import datetime

from agentforge.models.clarification import ClarificationRequest, ClarificationStatus
from agentforge.models.execution import AgentExecution
from agentforge.models.task import Task, TaskStatus
from agentforge.models.tool import ApprovalRequest, ApprovalStatus, ToolCallRecord, ToolResultStatus
from agentforge.models.workflow import WorkflowEvent, WorkflowMetrics


def compute_workflow_metrics(
    tasks: list[Task],
    executions: list[AgentExecution],
    *,
    started_at: datetime,
    finished_at: datetime,
    tool_calls: list[ToolCallRecord] | None = None,
    approvals: list[ApprovalRequest] | None = None,
    clarifications: list[ClarificationRequest] | None = None,
    workflow_events: list[WorkflowEvent] | None = None,
) -> WorkflowMetrics:
    """Compute completion rate, timing, delegation and token metrics.

    The synthetic root task is excluded so the rate reflects executed work.
    """
    substantive = [task for task in tasks if task.metadata.get("role") != "root"]
    completed = sum(1 for task in substantive if task.status == TaskStatus.COMPLETED)
    failed = sum(1 for task in substantive if task.status == TaskStatus.FAILED)
    blocked = sum(1 for task in substantive if task.status == TaskStatus.BLOCKED)
    total = len(substantive)

    delegation_count = sum(
        1 for task in substantive if task.metadata.get("origin") == "delegation"
    )
    max_depth = max((task.depth for task in substantive), default=0)

    llm_calls = len(executions)
    prompt_tokens = sum(execution.prompt_tokens for execution in executions)
    completion_tokens = sum(execution.completion_tokens for execution in executions)

    task_seconds = 0.0
    for task in substantive:
        duration = task.duration_seconds()
        if duration is not None:
            task_seconds += duration

    total_seconds = max(0.0, (finished_at - started_at).total_seconds())
    tool_calls = tool_calls or []
    approvals = approvals or []
    clarifications = clarifications or []
    workflow_events = workflow_events or []

    decided_approvals = [
        item
        for item in approvals
        if item.decided_at is not None
        and item.status
        in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
        }
    ]
    approval_waits = [
        (item.decided_at - item.created_at).total_seconds()
        for item in decided_approvals
        if item.decided_at is not None
    ]
    approved = sum(
        1 for item in approvals if item.status == ApprovalStatus.APPROVED
    )
    rejected = sum(
        1 for item in approvals if item.status == ApprovalStatus.REJECTED
    )
    expired = sum(
        1 for item in approvals if item.status == ApprovalStatus.EXPIRED
    )
    answered_clarifications = [
        item
        for item in clarifications
        if item.answered_at is not None
        and item.status == ClarificationStatus.ANSWERED
    ]
    clarification_waits = [
        (item.answered_at - item.created_at).total_seconds()
        for item in answered_clarifications
        if item.answered_at is not None
    ]
    event_types = [item.event_type for item in workflow_events]
    delegation_tasks = [
        task
        for task in substantive
        if task.metadata.get("origin") == "delegation"
    ]
    delegation_completed = sum(
        1 for task in delegation_tasks if task.status == TaskStatus.COMPLETED
    )
    reassignments = sum(
        1 for task in substantive if task.metadata.get("reassigned_from")
    )

    return WorkflowMetrics(
        total_tasks=total,
        completed_tasks=completed,
        failed_tasks=failed,
        blocked_tasks=blocked,
        task_completion_rate=(completed / total) if total else 0.0,
        delegation_count=delegation_count,
        max_depth=max_depth,
        llm_calls=llm_calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_execution_seconds=total_seconds,
        total_task_seconds=task_seconds,
        tool_calls=len(tool_calls),
        tool_failures=sum(
            1 for call in tool_calls if call.status != ToolResultStatus.SUCCESS
        ),
        tool_seconds=sum(call.duration_seconds for call in tool_calls),
        approval_requests=len(approvals),
        approval_approved=approved,
        approval_rejected=rejected,
        approval_expired=expired,
        approval_rate=(
            approved / len(decided_approvals) if decided_approvals else 0.0
        ),
        approval_rejection_rate=(
            rejected / len(decided_approvals) if decided_approvals else 0.0
        ),
        average_approval_wait_seconds=(
            sum(approval_waits) / len(approval_waits) if approval_waits else 0.0
        ),
        clarifications=len(clarifications),
        clarification_answered=len(answered_clarifications),
        average_clarification_wait_seconds=(
            sum(clarification_waits) / len(clarification_waits)
            if clarification_waits
            else 0.0
        ),
        workflow_pauses=event_types.count("workflow_paused"),
        workflow_resumes=event_types.count("workflow_resumed"),
        workflow_cancellations=event_types.count("workflow_cancelled"),
        delegation_tasks=len(delegation_tasks),
        delegation_completed=delegation_completed,
        delegation_success_rate=(
            delegation_completed / len(delegation_tasks)
            if delegation_tasks
            else 0.0
        ),
        reassignments=reassignments,
    )
