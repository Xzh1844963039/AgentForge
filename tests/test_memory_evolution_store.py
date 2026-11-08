"""Phase 3 memory store, retrieval, decay and isolation tests."""

from __future__ import annotations

from datetime import timedelta


from agentforge.memory.embeddings import HashingEmbeddingProvider
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import (
    EvolvedMemory,
    MemoryCandidate,
    MemoryRetrievalConfig,
    MemoryType,
    PromotionState,
    VerificationLevel,
)
from agentforge.models.common import utc_now


class FakeClock:
    def __init__(self) -> None:
        self.now = utc_now()

    def __call__(self):
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def store(tmp_path, clock=None, **kwargs) -> EvolutionMemoryStore:
    return EvolutionMemoryStore(
        tmp_path / "memory.db",
        embedding_provider=HashingEmbeddingProvider(128),
        clock=clock or FakeClock(),
        **kwargs,
    )


def memory(
    *,
    agent_id: str = "coding",
    namespace: str = "experience",
    memory_type: MemoryType = MemoryType.EPISODIC,
    verification: VerificationLevel = VerificationLevel.REAL_EXECUTION,
    content: str = "real execution fact",
    importance: float = 0.6,
    confidence: float = 0.8,
    support: int = 1,
    dedupe_key: str | None = None,
    workflow_ids: list[str] | None = None,
    created_at=None,
    expires_at=None,
    promotion=PromotionState.NONE,
) -> EvolvedMemory:
    return EvolvedMemory(
        agent_id=agent_id,
        namespace=namespace,
        memory_type=memory_type,
        verification_level=verification,
        content=content,
        importance=importance,
        confidence=confidence,
        support_count=support,
        metadata={"workflow_ids": workflow_ids or []},
        created_at=created_at or utc_now(),
        expires_at=expires_at,
        promotion_state=promotion,
        dedupe_key=dedupe_key,
    )


def test_agent_and_namespace_isolation(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock)
    try:
        store_obj.upsert_memory(
            memory(agent_id="coding", content="coding secret procedure")
        )
        store_obj.upsert_memory(
            memory(agent_id="research", content="research secret procedure")
        )
        coding = store_obj.recall("coding", "procedure", limit=10)
        research = store_obj.recall("research", "procedure", limit=10)
        assert len(coding) == 1
        assert "coding secret" in coding[0].content
        assert len(research) == 1
        assert "research secret" in research[0].content
        assert store_obj.count("coding", namespace="experience") == 1
        assert store_obj.count("coding", namespace="other") == 0
    finally:
        store_obj.close()


def test_verification_filter_and_prefix(tmp_path):
    clock = FakeClock()
    strict = store(tmp_path, clock)
    try:
        strict.upsert_memory(
            memory(
                content="real note procedure",
                verification=VerificationLevel.REAL_EXECUTION,
            )
        )
        strict.upsert_memory(
            memory(
                content="mock note procedure",
                verification=VerificationLevel.MOCK_SIMULATION,
            )
        )
        strict.upsert_memory(
            memory(
                content="unverified note procedure",
                verification=VerificationLevel.UNVERIFIED,
            )
        )
        results = strict.recall("coding", "note procedure", limit=10)
        assert len(results) == 1
        assert results[0].content.startswith("[episodic|REAL_EXECUTION")
    finally:
        strict.close()

    include_mock = store(
        tmp_path / "mock",
        clock,
        retrieval_config=MemoryRetrievalConfig(
            include_mock=True, include_unverified=False
        ),
    )
    try:
        include_mock.upsert_memory(
            memory(
                content="mock note procedure",
                verification=VerificationLevel.MOCK_SIMULATION,
            )
        )
        result = include_mock.recall("coding", "note", limit=10)[0]
        assert "[MOCK SIMULATION - NOT REAL-WORLD VERIFIED]" in result.content
    finally:
        include_mock.close()


def test_embedding_ranking_prefers_relevant_procedure(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock)
    try:
        store_obj.upsert_memory(
            memory(
                content="unrelated accounting invoice procedure",
                memory_type=MemoryType.EPISODIC,
            )
        )
        store_obj.upsert_memory(
            memory(
                content=(
                    "Create and verify a note: use write_file then read_file "
                    "with path notes/verified.md"
                ),
                memory_type=MemoryType.PROCEDURAL,
                promotion=PromotionState.PROMOTED,
                importance=0.9,
                confidence=0.9,
            )
        )
        results = store_obj.recall(
            "coding", "create and verify a note", limit=2
        )
        assert results
        assert "notes/verified.md" in results[0].content
        assert results[0].metadata["memory_type"] == "procedural"
        assert results[0].metadata["verification_level"] == "REAL_EXECUTION"
    finally:
        store_obj.close()


def test_dedupe_consolidation_support(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock)
    try:
        first = memory(
            content="same procedure",
            dedupe_key="procedural:same",
            support=1,
            workflow_ids=["wf1"],
            promotion=PromotionState.CANDIDATE,
        )
        stored, created, updated = store_obj.upsert_memory(first)
        assert created and not updated
        second = memory(
            content="same procedure",
            dedupe_key="procedural:same",
            support=2,
            workflow_ids=["wf1", "wf2"],
            promotion=PromotionState.PROMOTED,
        )
        stored, created, updated = store_obj.upsert_memory(second)
        assert not created and updated
        assert stored.support_count == 2
        assert stored.promotion_state == PromotionState.PROMOTED
        assert store_obj.count("coding") == 1
    finally:
        store_obj.close()


def test_candidate_support_distinct_workflows(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock)
    try:
        candidate = MemoryCandidate(
            agent_id="coding",
            signature="sig",
            verification_level=VerificationLevel.REAL_EXECUTION,
            content="procedure",
            procedure={"tool_sequence": ["write_file", "read_file"]},
            support_count=1,
            evidence_workflow_ids=["wf1"],
            evidence_tool_call_ids=["tc1"],
            evidence_task_ids=["t1"],
        )
        merged, created, new_workflow = store_obj.upsert_candidate(candidate)
        assert created and new_workflow and merged.support_count == 1
        same = candidate.model_copy(
            update={
                "evidence_workflow_ids": ["wf1"],
                "evidence_tool_call_ids": ["tc1", "tc2"],
            }
        )
        merged, created, new_workflow = store_obj.upsert_candidate(same)
        assert not created and not new_workflow and merged.support_count == 1
        second = candidate.model_copy(
            update={
                "candidate_id": "cand2",
                "evidence_workflow_ids": ["wf2"],
                "evidence_tool_call_ids": ["tc3"],
                "evidence_task_ids": ["t2"],
            }
        )
        merged, created, new_workflow = store_obj.upsert_candidate(second)
        assert not created and new_workflow and merged.support_count == 2
    finally:
        store_obj.close()


def test_working_memory_expires_and_is_pruned(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock, working_ttl_seconds=60)
    try:
        store_obj.upsert_memory(
            memory(
                content="working context",
                memory_type=MemoryType.WORKING,
                importance=0.2,
                expires_at=clock.now + timedelta(seconds=60),
            )
        )
        assert store_obj.count("coding") == 1
        clock.advance(seconds=61)
        assert store_obj.recall("coding", "working", limit=5) == []
        assert store_obj.count("coding") == 0
        deleted = store_obj.prune(agent_id="coding")
        assert len(deleted) == 1
    finally:
        store_obj.close()


def test_decay_and_prune_unverified(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock, decay_half_life_days=1.0)
    try:
        old = clock.now - timedelta(days=30)
        unverified = memory(
            content="old unverified chat memory",
            verification=VerificationLevel.UNVERIFIED,
            importance=0.2,
            created_at=old,
        )
        real = memory(
            content="old real execution evidence",
            verification=VerificationLevel.REAL_EXECUTION,
            importance=0.8,
            created_at=old,
        )
        store_obj.upsert_memory(unverified)
        store_obj.upsert_memory(real)
        assert store_obj.effective_importance(unverified) < store_obj.effective_importance(real)
        deleted = store_obj.prune(agent_id="coding")
        assert unverified.memory_id in deleted
        assert store_obj.get_memory(real.memory_id) is not None
    finally:
        store_obj.close()


def test_stats_and_clear(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock)
    try:
        store_obj.upsert_memory(memory(content="real"))
        store_obj.upsert_memory(
            memory(
                content="mock",
                verification=VerificationLevel.MOCK_SIMULATION,
                agent_id="research",
            )
        )
        stats = store_obj.stats()
        assert stats.total == 2
        assert stats.by_agent["coding"] == 1
        counts = store_obj.clear(agent_id="coding", namespace="experience")
        assert counts["evolved_memories"] == 1
        assert store_obj.count("coding") == 0
        assert store_obj.count("research") == 1
    finally:
        store_obj.close()


def test_retrieval_event_is_recorded(tmp_path):
    clock = FakeClock()
    store_obj = store(tmp_path, clock)
    try:
        stored, _, _ = store_obj.upsert_memory(memory(content="note procedure"))
        store_obj.recall("coding", "note", limit=5)
        events = store_obj.list_retrieval_events(agent_id="coding")
        assert len(events) == 1
        assert stored.memory_id in events[0].memory_ids
    finally:
        store_obj.close()


def test_prompt_budget_limits_recalled_content(tmp_path):
    clock = FakeClock()
    store_obj = store(
        tmp_path,
        clock,
        retrieval_config=MemoryRetrievalConfig(
            max_items=5, max_chars=180
        ),
    )
    try:
        for index in range(5):
            store_obj.upsert_memory(
                memory(
                    content="note procedure " + ("x" * 300) + f" item {index}",
                    importance=0.9,
                )
            )
        results = store_obj.recall("coding", "note procedure", limit=5)
        assert results
        total = sum(len(item.content) for item in results)
        assert total <= 180
        assert results[-1].content.endswith("[memory truncated]") or total <= 180
    finally:
        store_obj.close()
