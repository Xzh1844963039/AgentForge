"""Task state-machine tests."""

from __future__ import annotations

import pytest

from agentforge.models.task import Task, TaskStatus
from agentforge.orchestration.state_machine import (
    InvalidTaskTransition,
    transition_task,
)


def make_task() -> Task:
    return Task(workflow_id="wf", description="work")


def test_pending_to_running_to_completed():
    task = make_task()
    transition_task(task, TaskStatus.RUNNING)
    assert task.started_at is not None
    transition_task(task, TaskStatus.COMPLETED, result="ok")
    assert task.status is TaskStatus.COMPLETED
    assert task.result == "ok"
    assert task.finished_at is not None
    assert task.is_terminal


def test_blocked_can_resume():
    task = make_task()
    transition_task(task, TaskStatus.RUNNING)
    transition_task(task, TaskStatus.BLOCKED)
    assert task.status is TaskStatus.BLOCKED
    transition_task(task, TaskStatus.RUNNING)
    transition_task(task, TaskStatus.COMPLETED, result="resumed")
    assert task.result == "resumed"


def test_terminal_state_is_final():
    task = make_task()
    transition_task(task, TaskStatus.RUNNING)
    transition_task(task, TaskStatus.COMPLETED, result="done")
    with pytest.raises(InvalidTaskTransition):
        transition_task(task, TaskStatus.RUNNING)


def test_cannot_skip_running():
    task = make_task()
    with pytest.raises(InvalidTaskTransition):
        transition_task(task, TaskStatus.COMPLETED, result="no")


def test_failure_records_error():
    task = make_task()
    transition_task(task, TaskStatus.RUNNING)
    transition_task(task, TaskStatus.FAILED, error="boom")
    assert task.status is TaskStatus.FAILED
    assert task.error == "boom"
    assert task.finished_at is not None


def test_pending_can_be_blocked_or_failed():
    blocked = make_task()
    transition_task(blocked, TaskStatus.BLOCKED)
    assert blocked.status is TaskStatus.BLOCKED

    failed = make_task()
    transition_task(failed, TaskStatus.FAILED, error="dependency failed")
    assert failed.status is TaskStatus.FAILED
