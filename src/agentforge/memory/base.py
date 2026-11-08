"""Memory interfaces with per-agent namespaces.

Phase 1 stores episodic task outcomes and supports keyword/recency recall. The
interface is intentionally small so later phases can add importance scoring,
reflection, forgetting, and embedding-based semantic retrieval without changing
agents or orchestration.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentforge.models.common import new_id, utc_now

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


class MemoryEntry(BaseModel):
    memory_id: str = Field(default_factory=lambda: new_id("mem"))
    agent_id: str
    namespace: str = "default"
    kind: str = "episodic"
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryStore(ABC):
    """Per-agent memory interface."""

    @abstractmethod
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
        """Store a memory in the agent's namespace."""

    @abstractmethod
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
        """Return the most relevant memories for a query.

        Retrieval implementations that audit usage should persist the task scope
        so feedback can be attributed to the exact workflow/task/execution.
        """

    @abstractmethod
    def recent(
        self,
        agent_id: str,
        *,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Return the newest memories for an agent."""

    @abstractmethod
    def count(self, agent_id: str, *, namespace: str | None = None) -> int:
        """Count stored memories for an agent."""


def rank_memories(
    query: str, entries: list[MemoryEntry], limit: int
) -> list[MemoryEntry]:
    """Rank memories by token overlap, then recency, then importance."""
    query_tokens = tokenize(query)
    scored: list[tuple[float, MemoryEntry]] = []
    for entry in entries:
        overlap = len(query_tokens & tokenize(entry.content))
        score = overlap + (0.1 * entry.importance)
        scored.append((score, entry))

    scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
    relevant = [entry for score, entry in scored if score > 0.1]
    if not relevant:
        relevant = [entry for _, entry in scored]
    return relevant[: max(0, limit)]
