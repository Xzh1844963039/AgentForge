"""Phase 4 task-scoped retrieval and feedback isolation tests."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentforge.config.settings import Settings
from agentforge.memory.embeddings import HashingEmbeddingProvider
from agentforge.memory.engine import MemoryEvolutionEngine
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import (
    MemoryFeedback,
    MemoryRetrievalConfig,
    MemoryType,
    VerificationLevel,
)
from agentforge.models.common import utc_now
from agentforge.models.task import Task, TaskStatus
from agentforge.models.tool import (
    ApprovalStatus,
    PermissionDecision,
    ToolCallRecord,
    ToolResultStatus,
)
from agentforge.models.workflow import Workflow, WorkflowStatus
from agentforge.persistence import SQLiteStore


def make_engine(tmp_path):
    audit = SQLiteStore(tmp_path / "audit.db")
    memory = EvolutionMemoryStore(
        tmp_path / "memory.db",
        embedding_provider=HashingEmbeddingProvider(128),
        retrieval_config=MemoryRetrievalConfig(include_unverified=False),
    )
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        log_level="WARNING",
        memory_promotion_threshold=2,
        memory_promotion_confidence=0.8,
    )
    return audit, memory, MemoryEvolutionEngine(
        audit=audit, memory=memory, settings=settings
    )


def seed_workflow(
    audit: SQLiteStore,
    workflow_id: str,
    task_id: str,
    *,
    execution_id: str | None = None,
    status: WorkflowStatus = WorkflowStatus.COMPLETED,
    started_at=None,
    finished_at=None,
) -> Task:
    now = utc_now()
    started_at = started_at or now - timedelta(seconds=1)
    finished_at = finished_at or now
    workflow = Workflow(
        workflow_id=workflow_id,
        goal="Create a note and verify it",
        conversation_id="conv",
        status=status,
        created_at=now - timedelta(seconds=2),
        finished_at=finished_at,
        metadata={"provider": "mock"},
    )
    audit.save_workflow(workflow)
    metadata = {"kind": "coding"}
    if execution_id:
        metadata["current_execution_id"] = execution_id
        metadata["execution_ids"] = [execution_id]
    task = Task(
        task_id=task_id,
        workflow_id=workflow_id,
        description="Create a note and verify it",
        required_capabilities=["coding"],
        assigned_agent="coding",
        status=TaskStatus.COMPLETED,
        created_at=started_at,
        started_at=started_at,
        finished_at=finished_at,
        metadata=metadata,
    )
    audit.save_task(task)

    def record(
        call_id: str,
        tool_name: str,
        arguments: dict,
        output: dict,
    ) -> None:
        audit.save_tool_call(
            ToolCallRecord(
                tool_call_id=call_id,
                workflow_id=workflow_id,
                task_id=task_id,
                agent_id="coding",
                tool_name=tool_name,
                arguments=arguments,
                status=ToolResultStatus.SUCCESS,
                output=output,
                duration_seconds=0.01,
                permission_decision=PermissionDecision.ALLOWED,
                approval_status=ApprovalStatus.NOT_REQUIRED,
                permissions=["file_write"],
                started_at=started_at + timedelta(milliseconds=10),
                finished_at=started_at + timedelta(milliseconds=20),
                metadata={},
            )
        )

    record(
        f"{workflow_id}-write",
        "write_file",
        {"path": "notes/verified.md", "content": "hello", "overwrite": True},
        {"path": "notes/verified.md", "size_bytes": 5, "sha256": "hash"},
    )
    record(
        f"{workflow_id}-read",
        "read_file",
        {"path": "notes/verified.md"},
        {
            "path": "notes/verified.md",
            "content": "hello",
            "size_bytes": 5,
            "sha256": "hash",
            "truncated": False,
        },
    )
    return task


def promoted_procedural(memory):
    return memory.list_memories(
        agent_id="coding",
        memory_type=MemoryType.PROCEDURAL,
        verification=VerificationLevel.REAL_EXECUTION,
    )[0]


def test_retrieval_and_feedback_rows_persist_exact_scope(tmp_path):
    audit, memory, _engine = make_engine(tmp_path)
    try:
        memory.record_retrieval(
            agent_id="coding",
            query="alpha",
            memory_ids=["mem-1"],
            workflow_id="wf-a",
            task_id="task-a",
            execution_id="exec-a",
        )
        memory.record_retrieval(
            agent_id="coding",
            query="beta",
            memory_ids=["mem-2"],
            workflow_id="wf-b",
            task_id="task-b",
            execution_id="exec-b",
        )
        exact = memory.list_retrieval_events(
            agent_id="coding",
            workflow_id="wf-a",
            task_id="task-a",
            execution_id="exec-a",
        )
        assert len(exact) == 1
        assert exact[0].query == "alpha"
        assert exact[0].workflow_id == "wf-a"
        assert exact[0].task_id == "task-a"
        assert exact[0].execution_id == "exec-a"

        feedback = MemoryFeedback(
            retrieval_id=exact[0].retrieval_id,
            memory_id="mem-1",
            workflow_id="wf-a",
            task_id="task-a",
            execution_id="exec-a",
            agent_id="coding",
            used=True,
            success=True,
            confidence_delta=0.03,
        )
        assert memory.record_feedback(feedback) is True
        scoped = memory.list_feedback(
            workflow_id="wf-a", task_id="task-a", execution_id="exec-a"
        )
        assert len(scoped) == 1
        assert scoped[0].execution_id == "exec-a"
        assert memory.list_feedback(workflow_id="wf-b") == []
    finally:
        memory.close()
        audit.close()


def test_feedback_is_task_scoped_not_time_window_scoped(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        seed_workflow(audit, "wf1", "t1")
        seed_workflow(audit, "wf2", "t2")
        engine.process_workflow("wf1")
        engine.process_workflow("wf2")
        promoted = promoted_procedural(memory)
        before = promoted.confidence

        window_start = utc_now() - timedelta(seconds=30)
        window_end = utc_now() + timedelta(seconds=30)
        seed_workflow(
            audit,
            "wf3",
            "t3",
            execution_id="exec-target",
            started_at=window_start,
            finished_at=window_end,
        )
        seed_workflow(
            audit,
            "wf4",
            "t4",
            execution_id="exec-concurrent",
            started_at=window_start,
            finished_at=window_end,
        )
        memory.record_retrieval(
            agent_id="coding",
            query="create and verify a note",
            memory_ids=[promoted.memory_id],
            workflow_id="wf3",
            task_id="t3",
            execution_id="exec-target",
        )
        memory.record_retrieval(
            agent_id="coding",
            query="create and verify a note",
            memory_ids=[promoted.memory_id],
            workflow_id="wf4",
            task_id="t4",
            execution_id="exec-concurrent",
        )

        result = engine.process_workflow("wf3", learn=False)
        feedback = memory.list_feedback(workflow_id="wf3")
        assert len(feedback) == 1
        assert feedback[0].task_id == "t3"
        assert feedback[0].execution_id == "exec-target"
        assert feedback[0].used is True
        assert result.feedback_ids == [feedback[0].feedback_id]
        assert memory.list_feedback(workflow_id="wf4") == []
        updated = memory.get_memory(promoted.memory_id)
        assert updated is not None
        assert updated.confidence == pytest.approx(before + 0.03, abs=1e-6)
    finally:
        memory.close()
        audit.close()


def test_cancelled_workflow_records_no_success_feedback(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        seed_workflow(audit, "wf1", "t1")
        seed_workflow(audit, "wf2", "t2")
        engine.process_workflow("wf1")
        engine.process_workflow("wf2")
        promoted = promoted_procedural(memory)
        seed_workflow(
            audit,
            "wf-cancelled",
            "t-cancelled",
            execution_id="exec-cancelled",
            status=WorkflowStatus.CANCELLED,
        )
        memory.record_retrieval(
            agent_id="coding",
            query="create and verify a note",
            memory_ids=[promoted.memory_id],
            workflow_id="wf-cancelled",
            task_id="t-cancelled",
            execution_id="exec-cancelled",
        )
        result = engine.process_workflow("wf-cancelled", learn=False)
        assert result.feedback_ids == []
        assert memory.list_feedback(workflow_id="wf-cancelled") == []
    finally:
        memory.close()
        audit.close()


def test_concurrent_workflow_feedback_does_not_cross_contaminate(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        seed_workflow(audit, "wf1", "t1")
        seed_workflow(audit, "wf2", "t2")
        engine.process_workflow("wf1")
        engine.process_workflow("wf2")
        promoted = promoted_procedural(memory)

        for suffix in ("a", "b"):
            seed_workflow(
                audit,
                f"wf-{suffix}",
                f"task-{suffix}",
                execution_id=f"exec-{suffix}",
            )
            memory.record_retrieval(
                agent_id="coding",
                query="create and verify a note",
                memory_ids=[promoted.memory_id],
                workflow_id=f"wf-{suffix}",
                task_id=f"task-{suffix}",
                execution_id=f"exec-{suffix}",
            )

        async def run_both():
            return await asyncio.gather(
                asyncio.to_thread(engine.process_workflow, "wf-a", learn=False),
                asyncio.to_thread(engine.process_workflow, "wf-b", learn=False),
            )

        result_a, result_b = asyncio.run(run_both())
        assert len(result_a.feedback_ids) == 1
        assert len(result_b.feedback_ids) == 1
        assert result_a.feedback_ids != result_b.feedback_ids
        feedback_a = memory.list_feedback(workflow_id="wf-a")
        feedback_b = memory.list_feedback(workflow_id="wf-b")
        assert len(feedback_a) == 1 and len(feedback_b) == 1
        assert feedback_a[0].execution_id == "exec-a"
        assert feedback_b[0].execution_id == "exec-b"
        assert feedback_a[0].memory_id == feedback_b[0].memory_id
    finally:
        memory.close()
        audit.close()
