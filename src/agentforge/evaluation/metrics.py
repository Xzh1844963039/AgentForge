"""Workflow metrics derived from durable task and execution records."""

from __future__ import annotations

from datetime import datetime

from agentforge.models.execution import AgentExecution
from agentforge.models.task import Task, TaskStatus
from agentforge.models.tool import ToolCallRecord, ToolResultStatus
from agentforge.models.workflow import WorkflowMetrics


def compute_workflow_metrics(
    tasks: list[Task],
    executions: list[AgentExecution],
    *,
    started_at: datetime,
    finished_at: datetime,
    tool_calls: list[ToolCallRecord] | None = None,
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
    )
