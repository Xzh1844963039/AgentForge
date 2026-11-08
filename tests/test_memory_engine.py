"""Evidence-first memory evolution engine tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agentforge.config.settings import Settings
from agentforge.memory.embeddings import HashingEmbeddingProvider
from agentforge.memory.engine import MemoryEvolutionEngine
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import (
    MemoryRetrievalConfig,
    MemoryType,
    PromotionState,
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


def make_engine(tmp_path, *, include_mock=False):
    audit = SQLiteStore(tmp_path / "audit.db")
    memory = EvolutionMemoryStore(
        tmp_path / "memory.db",
        embedding_provider=HashingEmbeddingProvider(128),
        retrieval_config=MemoryRetrievalConfig(
            include_mock=include_mock, include_unverified=False
        ),
    )
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        log_level="WARNING",
        memory_promotion_threshold=2,
        memory_promotion_confidence=0.8,
        memory_store_mock_procedures=True,
    )
    engine = MemoryEvolutionEngine(audit=audit, memory=memory, settings=settings)
    return audit, memory, engine


def seed_workflow(
    audit: SQLiteStore,
    workflow_id: str,
    task_id: str,
    *,
    path: str = "notes/verified.md",
    mock: bool = False,
    success: bool = True,
    provider: str = "mock",
    started_at=None,
    finished_at=None,
) -> None:
    now = utc_now()
    started_at = started_at or now - timedelta(seconds=1)
    finished_at = finished_at or now
    workflow = Workflow(
        workflow_id=workflow_id,
        goal="Create a note and verify it",
        conversation_id="conv",
        status=WorkflowStatus.COMPLETED,
        created_at=now - timedelta(seconds=2),
        finished_at=finished_at,
        metadata={"provider": provider},
    )
    audit.save_workflow(workflow)
    task = Task(
        task_id=task_id,
        workflow_id=workflow_id,
        description="Create a note and verify it",
        required_capabilities=["coding"],
        assigned_agent="coding",
        status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
        created_at=started_at,
        started_at=started_at,
        finished_at=finished_at,
        metadata={"kind": "coding"},
    )
    audit.save_task(task)

    def record(
        call_id: str,
        tool_name: str,
        arguments: dict,
        output: dict,
        *,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        record_status = (
            ToolResultStatus.SUCCESS if status == "success" else ToolResultStatus.ERROR
        )
        audit.save_tool_call(
            ToolCallRecord(
                tool_call_id=call_id,
                workflow_id=workflow_id,
                task_id=task_id,
                agent_id="coding",
                tool_name=tool_name,
                arguments=arguments,
                status=record_status,
                output=output,
                error=error,
                duration_seconds=0.01,
                permission_decision=PermissionDecision.ALLOWED,
                approval_status=ApprovalStatus.NOT_REQUIRED,
                permissions=["file_read"] if tool_name == "read_file" else ["file_write"],
                started_at=started_at + timedelta(milliseconds=10),
                finished_at=started_at + timedelta(milliseconds=20),
                metadata={"mock": True} if mock else {},
            )
        )

    record(
        f"{workflow_id}-write",
        "write_file",
        {"path": path, "content": "hello", "overwrite": True},
        {"path": path, "size_bytes": 5, "sha256": "hash"},
        status="success" if success else "error",
        error=None if success else "write failed",
    )
    record(
        f"{workflow_id}-read",
        "read_file",
        {"path": path},
        {
            "path": path,
            "content": "hello",
            "size_bytes": 5,
            "sha256": "hash",
            "truncated": False,
        },
        status="success" if success else "error",
        error=None if success else "read failed",
    )


def test_real_audit_promotes_after_two_workflows(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        seed_workflow(audit, "wf1", "t1")
        first = engine.process_workflow("wf1")
        assert first.verification_counts == {"REAL_EXECUTION": 1}
        assert first.candidate_ids
        assert first.promoted_memory_ids == []
        assert memory.list_candidates(agent_id="coding")[0].support_count == 1

        seed_workflow(audit, "wf2", "t2")
        second = engine.process_workflow("wf2")
        assert second.promoted_memory_ids
        assert len(second.promoted_memory_ids) == 1

        procedural = memory.list_memories(
            agent_id="coding",
            memory_type=MemoryType.PROCEDURAL,
            verification=VerificationLevel.REAL_EXECUTION,
        )
        assert len(procedural) == 1
        promoted = procedural[0]
        assert promoted.promotion_state == PromotionState.PROMOTED
        assert promoted.support_count == 2
        assert promoted.confidence >= 0.8
        assert "notes/verified.md" in promoted.content
        assert "REAL_EXECUTION" in promoted.content
        assert promoted.source is not None
        assert promoted.source.tool_execution == "real"
        assert promoted.source.llm_provider == "mock"
        assert promoted.source.tool_call_ids
        assert promoted.source.workflow_id == "wf2"

        candidate = memory.list_candidates(agent_id="coding")[0]
        assert candidate.support_count == 2
        assert candidate.status == PromotionState.PROMOTED
        assert candidate.promoted_memory_id == promoted.memory_id

        episodic = memory.list_memories(
            agent_id="coding", memory_type=MemoryType.EPISODIC
        )
        semantic = memory.list_memories(
            agent_id="coding", memory_type=MemoryType.SEMANTIC
        )
        working = memory.list_memories(
            agent_id="coding", memory_type=MemoryType.WORKING
        )
        assert len(episodic) == 2
        assert len(semantic) == 2
        assert len(working) == 2
        assert all(
            item.verification_level == VerificationLevel.REAL_EXECUTION
            for item in episodic + semantic + working
        )

        recalled = memory.recall("coding", "create and verify a note", limit=6)
        assert any(
            "[procedural|REAL_EXECUTION" in item.content
            and "notes/verified.md" in item.content
            for item in recalled
        )
        # Other agents cannot see coding's memory.
        assert memory.recall("research", "create and verify a note") == []
    finally:
        memory.close()
        audit.close()


def test_mock_audit_is_labelled_and_never_high_confidence(tmp_path):
    audit, memory, engine = make_engine(tmp_path, include_mock=True)
    try:
        seed_workflow(audit, "wfm", "tm", mock=True)
        result = engine.process_workflow("wfm")
        assert result.verification_counts == {"MOCK_SIMULATION": 1}
        assert result.promoted_memory_ids == []

        procedural = memory.list_memories(
            agent_id="coding", memory_type=MemoryType.PROCEDURAL
        )
        assert len(procedural) == 1
        assert procedural[0].verification_level == VerificationLevel.MOCK_SIMULATION
        assert procedural[0].promotion_state == PromotionState.PROVISIONAL
        assert procedural[0].confidence < 0.5
        assert "[MOCK SIMULATION - NOT REAL-WORLD VERIFIED]" in procedural[0].content
        candidate = memory.list_candidates(agent_id="coding")[0]
        assert candidate.status == PromotionState.PROVISIONAL
        assert candidate.promoted_memory_id == procedural[0].memory_id

        # Strict default recall excludes mock memories.
        strict = EvolutionMemoryStore(
            tmp_path / "memory.db",
            embedding_provider=HashingEmbeddingProvider(128),
            retrieval_config=MemoryRetrievalConfig(include_mock=False),
        )
        try:
            assert strict.recall("coding", "note") == []
        finally:
            strict.close()
    finally:
        memory.close()
        audit.close()


def test_failed_audit_is_unverified_and_creates_no_procedural(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        seed_workflow(audit, "wff", "tf", success=False)
        result = engine.process_workflow("wff")
        assert result.verification_counts == {"UNVERIFIED": 1}
        assert result.promoted_memory_ids == []
        assert memory.list_candidates(agent_id="coding") == []
        memories = memory.list_memories(agent_id="coding")
        assert memories
        assert all(
            item.verification_level == VerificationLevel.UNVERIFIED
            for item in memories
        )
    finally:
        memory.close()
        audit.close()


def test_no_tool_audit_is_skipped(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        audit.save_workflow(
            Workflow(
                workflow_id="wf-empty",
                goal="chat only",
                conversation_id="conv",
                status=WorkflowStatus.COMPLETED,
            )
        )
        result = engine.process_workflow("wf-empty")
        assert result.skipped_reason == "no_tool_audit"
        assert memory.total_count() == 0
    finally:
        memory.close()
        audit.close()


def test_feedback_links_retrieval_to_action_and_updates_confidence(tmp_path):
    audit, memory, engine = make_engine(tmp_path)
    try:
        seed_workflow(audit, "wf1", "t1")
        seed_workflow(audit, "wf2", "t2")
        engine.process_workflow("wf1")
        engine.process_workflow("wf2")
        promoted = memory.list_memories(
            agent_id="coding",
            memory_type=MemoryType.PROCEDURAL,
            verification=VerificationLevel.REAL_EXECUTION,
        )[0]
        before = promoted.confidence

        # Simulate retrieval during a third task, then process feedback only.
        memory.recall("coding", "create and verify a note", limit=6)
        seed_workflow(
            audit,
            "wf3",
            "t3",
            started_at=utc_now() - timedelta(seconds=5),
            finished_at=utc_now() + timedelta(seconds=5),
        )
        result = engine.process_workflow("wf3", learn=False)
        feedback = memory.list_feedback(workflow_id="wf3")
        assert result.feedback_ids
        assert len(feedback) == 1
        assert feedback[0].memory_id == promoted.memory_id
        assert feedback[0].used is True
        assert feedback[0].success is True
        assert feedback[0].confidence_delta == pytest.approx(0.03)
        updated = memory.get_memory(promoted.memory_id)
        assert updated is not None
        assert updated.confidence == pytest.approx(before + 0.03, abs=1e-6)
    finally:
        memory.close()
        audit.close()
