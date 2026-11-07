"""Memory store and namespace isolation tests."""

from __future__ import annotations

from agentforge.memory import InMemoryMemoryStore, SQLiteMemoryStore
from agentforge.persistence import SQLiteStore


def test_in_memory_namespace_isolation():
    memory = InMemoryMemoryStore()
    memory.remember("research", "multi-agent research notes about orchestration")
    memory.remember("coding", "python implementation notes about orchestration")

    research = memory.recall("research", "orchestration", limit=5)
    coding = memory.recall("coding", "orchestration", limit=5)
    assert len(research) == 1
    assert "multi-agent" in research[0].content
    assert len(coding) == 1
    assert "python" in coding[0].content
    assert memory.count("research") == 1
    assert memory.count("coding") == 1


def test_memory_recall_ranks_relevant_first():
    memory = InMemoryMemoryStore()
    memory.remember("a", "completely unrelated grocery list")
    memory.remember("a", "multi-agent orchestration and task delegation")
    results = memory.recall("a", "delegation orchestration", limit=2)
    assert "multi-agent" in results[0].content


def test_sqlite_memory_survives_reopen(tmp_path):
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    memory = SQLiteMemoryStore(store)
    memory.remember(
        "research",
        "task outcome: research multi-agent benefits",
        kind="task_outcome",
        namespace="experience",
        metadata={"task_id": "t1"},
    )
    memory.remember(
        "coding",
        "task outcome: propose python plan",
        kind="task_outcome",
        namespace="experience",
    )
    store.close()

    reopened = SQLiteStore(path)
    try:
        reopened_memory = SQLiteMemoryStore(reopened)
        research = reopened_memory.recall(
            "research", "multi-agent benefits", namespace="experience"
        )
        assert len(research) == 1
        assert research[0].metadata["task_id"] == "t1"
        assert reopened_memory.count("coding", namespace="experience") == 1
        assert reopened_memory.count("research", namespace="conversation") == 0
    finally:
        reopened.close()
