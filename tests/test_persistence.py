"""Persistence round-trip and event-log tests."""

from __future__ import annotations

import json
from datetime import timedelta

from agentforge.models.common import utc_now
from agentforge.models.execution import AgentExecution, ExecutionStatus
from agentforge.models.message import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
)
from agentforge.models.task import Task, TaskStatus
from agentforge.models.workflow import Workflow, WorkflowMetrics, WorkflowStatus
from agentforge.persistence import EventLogger, SQLiteStore


def build_records():
    now = utc_now()
    conversation = Conversation(
        conversation_id="conv_test",
        conversation_type=ConversationType.GROUP,
        title="Test group",
        participants=["human", "research"],
    )
    message = Message(
        message_id="msg_test",
        conversation_id="conv_test",
        sender="human",
        receiver="conv_test",
        message_type=MessageType.GROUP,
        content="hello group",
        timestamp=now,
        correlation_id="wf_test",
        metadata={"k": "v"},
    )
    task = Task(
        task_id="task_test",
        workflow_id="wf_test",
        description="research task",
        required_capabilities=["research"],
        assigned_agent="research",
        status=TaskStatus.RUNNING,
        depth=1,
        started_at=now,
        metadata={"kind": "research"},
    )
    execution = AgentExecution(
        execution_id="exec_test",
        workflow_id="wf_test",
        task_id="task_test",
        agent_id="research",
        purpose="execute:research:research",
        provider="mock",
        model="mock-deterministic-v1",
        started_at=now,
        finished_at=now + timedelta(milliseconds=10),
        status=ExecutionStatus.COMPLETED,
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=10.0,
        metadata={"action": "complete"},
    )
    workflow = Workflow(
        workflow_id="wf_test",
        goal="test goal",
        conversation_id="conv_test",
        status=WorkflowStatus.RUNNING,
        created_at=now,
        metrics=WorkflowMetrics(total_tasks=1, completed_tasks=1),
    )
    return conversation, message, task, execution, workflow


def test_roundtrip_across_store_reopen(tmp_path):
    path = tmp_path / "agentforge.db"
    conversation, message, task, execution, workflow = build_records()

    store = SQLiteStore(path)
    store.save_conversation(conversation)
    store.save_message(message)
    store.save_task(task)
    store.save_execution(execution)
    store.save_workflow(workflow)
    store.insert_memory(
        {
            "memory_id": "mem_test",
            "agent_id": "research",
            "namespace": "experience",
            "kind": "task_outcome",
            "content": "learned something",
            "importance": 0.7,
            "created_at": utc_now(),
            "metadata": {"task_id": "task_test"},
        }
    )
    counts = store.table_counts()
    store.close()

    reopened = SQLiteStore(path)
    try:
        loaded_conversation = reopened.get_conversation("conv_test")
        assert loaded_conversation is not None
        assert loaded_conversation.participants == ["human", "research"]

        loaded_messages = reopened.list_messages("conv_test")
        assert len(loaded_messages) == 1
        assert loaded_messages[0].content == "hello group"
        assert loaded_messages[0].metadata == {"k": "v"}

        loaded_task = reopened.get_task("task_test")
        assert loaded_task is not None
        assert loaded_task.required_capabilities == ["research"]
        assert loaded_task.metadata["kind"] == "research"

        loaded_execution = reopened.list_executions("wf_test")[0]
        assert loaded_execution.execution_id == "exec_test"
        assert loaded_execution.prompt_tokens == 10

        loaded_workflow = reopened.get_workflow("wf_test")
        assert loaded_workflow is not None
        assert loaded_workflow.metrics.total_tasks == 1

        rows = reopened.list_memory_rows("research")
        assert len(rows) == 1
        assert rows[0]["content"] == "learned something"
        assert counts["messages"] == 1
    finally:
        reopened.close()


def test_task_upsert_updates_status(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite")
    task = Task(task_id="t1", workflow_id="wf", description="work")
    store.save_task(task)
    task.status = TaskStatus.COMPLETED
    task.result = "done"
    store.save_task(task)
    loaded = store.get_task("t1")
    assert loaded is not None
    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.result == "done"
    store.close()


def test_workflow_upsert_and_listing(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite")
    workflow = Workflow(workflow_id="wf1", goal="a", conversation_id="c")
    store.save_workflow(workflow)
    workflow.status = WorkflowStatus.COMPLETED
    workflow.final_result = "report"
    store.save_workflow(workflow)
    loaded = store.get_workflow("wf1")
    assert loaded is not None
    assert loaded.final_result == "report"
    assert store.list_workflows()[0].workflow_id == "wf1"
    store.close()


def test_event_logger_writes_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    logger = EventLogger(path)
    logger.event("workflow_started", workflow_id="wf1", goal="g")
    logger.event("task_completed", task_id="t1", duration=0.1)
    logger.close()

    lines = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [line["event"] for line in lines] == [
        "workflow_started",
        "task_completed",
    ]
    assert lines[0]["workflow_id"] == "wf1"
    assert "timestamp" in lines[0]
