"""Strict task state transitions.

Terminal states never transition again. ``BLOCKED`` is used both for
dependency waits and for a parent task waiting on delegated children.
"""

from __future__ import annotations


from agentforge.models.common import utc_now
from agentforge.models.task import Task, TaskStatus
from agentforge.orchestration.errors import OrchestrationError


class InvalidTaskTransition(OrchestrationError):
    pass


ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.FAILED}
    ),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED}
    ),
    TaskStatus.BLOCKED: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


def transition_task(
    task: Task,
    new_status: TaskStatus,
    *,
    result: str | None = None,
    error: str | None = None,
) -> Task:
    """Validate and apply a status transition, updating timestamps."""
    if new_status not in ALLOWED_TRANSITIONS[task.status]:
        raise InvalidTaskTransition(
            f"invalid task transition {task.status.value} -> {new_status.value} "
            f"for task {task.task_id}"
        )

    now = utc_now()
    if new_status == TaskStatus.RUNNING:
        if task.started_at is None:
            task.started_at = now
        task.finished_at = None
        task.error = None
    elif new_status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        task.finished_at = now
    elif new_status == TaskStatus.BLOCKED:
        task.finished_at = None

    task.status = new_status
    if result is not None:
        task.result = result
    if error is not None:
        task.error = error
    if new_status == TaskStatus.COMPLETED and error is None:
        task.error = None
    return task
