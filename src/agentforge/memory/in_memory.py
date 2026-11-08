"""In-process memory store used by unit tests and lightweight embeddings."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from agentforge.memory.base import MemoryEntry, MemoryStore, rank_memories


class InMemoryMemoryStore(MemoryStore):
    """Simple in-memory implementation (not durable across processes)."""

    def __init__(self) -> None:
        self._entries: dict[str, list[MemoryEntry]] = defaultdict(list)

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
        self._entries[agent_id].append(entry)
        return entry

    def recall(
        self,
        agent_id: str,
        query: str,
        *,
        namespace: str | None = None,
        limit: int = 5,
        workflow_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
    ) -> list[MemoryEntry]:
        entries = self._scoped(agent_id, namespace)
        return rank_memories(query, entries, limit)

    def recent(
        self,
        agent_id: str,
        *,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        entries = sorted(
            self._scoped(agent_id, namespace),
            key=lambda entry: entry.created_at,
            reverse=True,
        )
        return entries[: max(0, limit)]

    def count(self, agent_id: str, *, namespace: str | None = None) -> int:
        return len(self._scoped(agent_id, namespace))

    def _scoped(self, agent_id: str, namespace: str | None) -> list[MemoryEntry]:
        entries = list(self._entries.get(agent_id, []))
        if namespace is not None:
            entries = [entry for entry in entries if entry.namespace == namespace]
        return entries
