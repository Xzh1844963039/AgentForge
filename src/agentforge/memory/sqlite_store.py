"""SQLite-backed durable memory store."""

from __future__ import annotations

from typing import Any

from agentforge.memory.base import MemoryEntry, MemoryStore, rank_memories
from agentforge.persistence.database import SQLiteStore


class SQLiteMemoryStore(MemoryStore):
    """Durable per-agent memory backed by the shared SQLite store."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def remember(
        self,
        agent_id: str,
        content: str,
        *,
        kind: str = "episodic",
        namespace: str = "default",
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            agent_id=agent_id,
            namespace=namespace,
            kind=kind,
            content=content,
            importance=importance,
            metadata=metadata or {},
        )
        self._store.insert_memory(
            {
                "memory_id": entry.memory_id,
                "agent_id": entry.agent_id,
                "namespace": entry.namespace,
                "kind": entry.kind,
                "content": entry.content,
                "importance": entry.importance,
                "created_at": entry.created_at,
                "metadata": entry.metadata,
            }
        )
        return entry

    def recall(
        self,
        agent_id: str,
        query: str,
        *,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        entries = self._entries(agent_id, namespace)
        return rank_memories(query, entries, limit)

    def recent(
        self,
        agent_id: str,
        *,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        return self._entries(agent_id, namespace)[: max(0, limit)]

    def count(self, agent_id: str, *, namespace: str | None = None) -> int:
        return len(self._entries(agent_id, namespace))

    def _entries(self, agent_id: str, namespace: str | None) -> list[MemoryEntry]:
        rows = self._store.list_memory_rows(agent_id, namespace)
        return [
            MemoryEntry(
                memory_id=row["memory_id"],
                agent_id=row["agent_id"],
                namespace=row["namespace"],
                kind=row["kind"],
                content=row["content"],
                importance=row["importance"],
                created_at=row["created_at"],
                metadata=row["metadata"],
            )
            for row in rows
        ]
