"""Durable Phase 3 memory store with provenance, retrieval and feedback."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from agentforge.memory.base import MemoryEntry, MemoryStore, tokenize
from agentforge.memory.embeddings import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    cosine_similarity,
)
from agentforge.memory.models import (
    EvolvedMemory,
    MemoryCandidate,
    MemoryFeedback,
    MemoryRetrievalConfig,
    MemorySource,
    MemoryStats,
    MemoryType,
    PromotionState,
    RetrievalEvent,
    VERIFICATION_RANK,
    VerificationLevel,
)
from agentforge.models.common import utc_now

logger = logging.getLogger("agentforge.memory.evolution")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolved_memories (
    memory_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL,
    confidence REAL NOT NULL,
    support_count INTEGER NOT NULL,
    source_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    access_count INTEGER NOT NULL,
    last_accessed_at TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    promotion_state TEXT NOT NULL,
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_evolved_agent
    ON evolved_memories(agent_id, namespace, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evolved_dedupe
    ON evolved_memories(agent_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    signature TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    content TEXT NOT NULL,
    procedure_json TEXT NOT NULL,
    support_count INTEGER NOT NULL,
    evidence_workflow_ids_json TEXT NOT NULL,
    evidence_tool_call_ids_json TEXT NOT NULL,
    evidence_task_ids_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    promoted_memory_id TEXT,
    status TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_signature
    ON memory_candidates(agent_id, signature);

CREATE TABLE IF NOT EXISTS retrieval_events (
    retrieval_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT,
    query TEXT NOT NULL,
    memory_ids_json TEXT NOT NULL,
    workflow_id TEXT,
    task_id TEXT,
    execution_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retrieval_agent_time
    ON retrieval_events(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_retrieval_scope
    ON retrieval_events(workflow_id, task_id, execution_id);

CREATE TABLE IF NOT EXISTS memory_feedback (
    feedback_id TEXT PRIMARY KEY,
    retrieval_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    workflow_id TEXT,
    task_id TEXT,
    execution_id TEXT,
    agent_id TEXT NOT NULL,
    used INTEGER NOT NULL,
    success INTEGER NOT NULL,
    confidence_delta REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(retrieval_id, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_workflow
    ON memory_feedback(workflow_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_scope
    ON memory_feedback(workflow_id, task_id, execution_id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class EvolutionMemoryStore(MemoryStore):
    """SQLite memory store implementing Phase 3 taxonomy and retrieval.

    Storage is separate from the Phase 2 audit database. All retrieval is scoped
    by ``agent_id``; namespace is an additional isolation dimension.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        retrieval_config: MemoryRetrievalConfig | None = None,
        retrieval_enabled: bool = True,
        clock: Callable[[], datetime] = utc_now,
        decay_half_life_days: float = 30.0,
        forget_threshold: float = 0.05,
        working_ttl_seconds: int = 3600,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self.retrieval_config = retrieval_config or MemoryRetrievalConfig()
        self.retrieval_enabled = retrieval_enabled
        self.clock = clock
        self.decay_half_life_days = max(0.1, decay_half_life_days)
        self.forget_threshold = max(0.0, forget_threshold)
        self.working_ttl_seconds = max(1, working_ttl_seconds)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate_scope_columns()
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', '2') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )

    def _migrate_scope_columns(self) -> None:
        """Add Phase 4 task-scope columns to pre-existing memory databases."""
        for table in ("retrieval_events", "memory_feedback"):
            existing = {
                str(row["name"])
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for column in ("workflow_id", "task_id", "execution_id"):
                if column not in existing:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} TEXT"
                    )

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------------- MemoryStore
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
        metadata = dict(metadata or {})
        memory_type = _memory_type_from_kind(kind, metadata)
        verification = _verification_from_value(
            metadata.get("verification_level", VerificationLevel.UNVERIFIED)
        )
        source_data = metadata.get("source")
        source = (
            MemorySource.model_validate(source_data)
            if isinstance(source_data, dict)
            else MemorySource(
                origin="agent_memory",
                agent_id=agent_id,
                workflow_id=metadata.get("workflow_id"),
                task_id=metadata.get("task_id"),
                llm_provider=metadata.get("llm_provider"),
                llm_is_mock=bool(metadata.get("llm_is_mock", False)),
                tool_execution="none",
            )
        )
        memory = EvolvedMemory(
            agent_id=agent_id,
            namespace=namespace,
            memory_type=memory_type,
            verification_level=verification,
            content=content,
            importance=max(0.0, min(1.0, importance)),
            confidence=float(metadata.get("confidence", _default_confidence(verification))),
            source=source,
            metadata=metadata,
            expires_at=metadata.get("expires_at"),
            dedupe_key=metadata.get("dedupe_key"),
        )
        if memory.memory_type == MemoryType.WORKING and memory.expires_at is None:
            memory.expires_at = self.clock() + timedelta(
                seconds=self.working_ttl_seconds
            )
        memory.embedding = self.embedding_provider.embed(content)
        stored, _created, _updated = self.upsert_memory(memory)
        return _to_memory_entry(stored, prefix=False)

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
        if not self.retrieval_enabled or not self.retrieval_config.enabled:
            return []
        now = self.clock()
        rows = self._active_rows(agent_id, namespace)
        rows = [
            row
            for row in rows
            if self._verification_allowed(row.verification_level)
        ]
        ranked = self._rank(query, rows, now)
        selected = ranked[: min(limit, self.retrieval_config.max_items)]
        if not selected:
            return []

        entries: list[MemoryEntry] = []
        used_chars = 0
        for memory, _score in selected:
            prefixed = _prefixed_content(memory)
            remaining = self.retrieval_config.max_chars - used_chars
            if remaining <= 0:
                break
            if len(prefixed) > remaining:
                suffix = "...[memory truncated]"
                keep = max(0, remaining - len(suffix))
                prefixed = prefixed[:keep] + suffix
            used_chars += len(prefixed)
            entries.append(_to_memory_entry(memory, content=prefixed, prefix=False))

        ids = [entry.metadata["memory_id"] for entry in entries]
        self._mark_accessed(ids, now)
        self.record_retrieval(
            agent_id=agent_id,
            query=query,
            memory_ids=ids,
            namespace=namespace,
            workflow_id=workflow_id,
            task_id=task_id,
            execution_id=execution_id,
            created_at=now,
        )
        return entries

    def recent(
        self,
        agent_id: str,
        *,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        rows = self._active_rows(agent_id, namespace)
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return [_to_memory_entry(memory, prefix=False) for memory in rows[:limit]]

    def count(self, agent_id: str, *, namespace: str | None = None) -> int:
        return len(self._active_rows(agent_id, namespace))

    # --------------------------------------------------------------- direct access
    def upsert_memory(
        self, memory: EvolvedMemory
    ) -> tuple[EvolvedMemory, bool, bool]:
        """Insert or merge a memory by (agent_id, dedupe_key)."""
        existing = (
            self.find_by_dedupe(memory.agent_id, memory.dedupe_key)
            if memory.dedupe_key
            else None
        )
        if existing is None:
            self._insert_memory(memory)
            return memory, True, False

        workflow_ids = _merge_lists(
            existing.metadata.get("workflow_ids", []),
            memory.metadata.get("workflow_ids", []),
        )
        support = max(
            existing.support_count,
            memory.support_count,
            len(workflow_ids) if workflow_ids else 1,
        )
        use_new_content = memory.support_count >= existing.support_count
        merged = existing.model_copy(
            update={
                "content": memory.content if use_new_content else existing.content,
                "importance": max(existing.importance, memory.importance),
                "confidence": max(existing.confidence, memory.confidence),
                "support_count": support,
                "source": _merge_sources(existing.source, memory.source),
                "metadata": {
                    **existing.metadata,
                    **memory.metadata,
                    "workflow_ids": workflow_ids,
                },
                "embedding": (
                    memory.embedding
                    if use_new_content and memory.embedding
                    else existing.embedding
                ),
                "promotion_state": (
                    memory.promotion_state
                    if _promotion_rank(memory.promotion_state)
                    > _promotion_rank(existing.promotion_state)
                    else existing.promotion_state
                ),
                "last_accessed_at": existing.last_accessed_at,
                "access_count": existing.access_count,
            }
        )
        self._update_memory(merged)
        return merged, False, True

    def find_by_dedupe(
        self, agent_id: str, dedupe_key: str | None
    ) -> EvolvedMemory | None:
        if not dedupe_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evolved_memories WHERE agent_id = ? AND dedupe_key = ?",
                (agent_id, dedupe_key),
            ).fetchone()
        return _row_to_memory(row) if row else None

    def get_memory(self, memory_id: str) -> EvolvedMemory | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evolved_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return _row_to_memory(row) if row else None

    def list_memories(
        self,
        *,
        agent_id: str | None = None,
        namespace: str | None = None,
        memory_type: MemoryType | None = None,
        verification: VerificationLevel | None = None,
        include_expired: bool = False,
        limit: int | None = None,
    ) -> list[EvolvedMemory]:
        query = "SELECT * FROM evolved_memories WHERE 1=1"
        params: list[Any] = []
        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if namespace is not None:
            query += " AND namespace = ?"
            params.append(namespace)
        if memory_type is not None:
            query += " AND memory_type = ?"
            params.append(memory_type.value)
        if verification is not None:
            query += " AND verification_level = ?"
            params.append(verification.value)
        if not include_expired:
            query += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(self.clock().isoformat())
        query += " ORDER BY created_at DESC, rowid DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_memory(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        agent_id: str | None = None,
        namespace: str | None = None,
        include_mock: bool = False,
        include_unverified: bool = False,
        limit: int = 20,
    ) -> list[EvolvedMemory]:
        now = self.clock()
        rows = self.list_memories(
            agent_id=agent_id, namespace=namespace, include_expired=False
        )
        allowed = {VerificationLevel.REAL_EXECUTION}
        if include_mock:
            allowed.add(VerificationLevel.MOCK_SIMULATION)
        if include_unverified:
            allowed = set(VerificationLevel)
        rows = [row for row in rows if row.verification_level in allowed]
        ranked = self._rank(query, rows, now)
        return [memory for memory, _score in ranked[: max(0, limit)]]

    def total_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM evolved_memories"
            ).fetchone()
        return int(row["n"]) if row else 0

    def update_confidence(
        self, memory_id: str, delta: float, *, now: datetime | None = None
    ) -> float | None:
        memory = self.get_memory(memory_id)
        if memory is None:
            return None
        updated = memory.model_copy(
            update={
                "confidence": max(0.05, min(0.99, memory.confidence + delta)),
                "metadata": {
                    **memory.metadata,
                    "feedback_updates": int(
                        memory.metadata.get("feedback_updates", 0)
                    )
                    + 1,
                },
            }
        )
        self._update_memory(updated)
        return updated.confidence

    # ---------------------------------------------------------------- candidates
    def upsert_candidate(
        self, candidate: MemoryCandidate
    ) -> tuple[MemoryCandidate, bool, bool]:
        existing = self.find_candidate(candidate.agent_id, candidate.signature)
        if existing is None:
            self._insert_candidate(candidate)
            return candidate, True, True

        workflow_ids = _merge_lists(
            existing.evidence_workflow_ids, candidate.evidence_workflow_ids
        )
        tool_ids = _merge_lists(
            existing.evidence_tool_call_ids, candidate.evidence_tool_call_ids
        )
        task_ids = _merge_lists(
            existing.evidence_task_ids, candidate.evidence_task_ids
        )
        new_workflow = len(workflow_ids) > len(existing.evidence_workflow_ids)
        verification = (
            candidate.verification_level
            if VERIFICATION_RANK[candidate.verification_level]
            <= VERIFICATION_RANK[existing.verification_level]
            else existing.verification_level
        )
        merged = existing.model_copy(
            update={
                "verification_level": verification,
                "content": candidate.content,
                "procedure": candidate.procedure,
                "support_count": max(len(workflow_ids), existing.support_count),
                "evidence_workflow_ids": workflow_ids,
                "evidence_tool_call_ids": tool_ids,
                "evidence_task_ids": task_ids,
                "last_seen": self.clock(),
                "promoted_memory_id": (
                    existing.promoted_memory_id or candidate.promoted_memory_id
                ),
                "status": (
                    existing.status
                    if existing.status == PromotionState.PROMOTED
                    else candidate.status
                ),
            }
        )
        self._update_candidate(merged)
        return merged, False, new_workflow

    def find_candidate(
        self, agent_id: str, signature: str
    ) -> MemoryCandidate | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_candidates WHERE agent_id = ? AND signature = ?",
                (agent_id, signature),
            ).fetchone()
        return _row_to_candidate(row) if row else None

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return _row_to_candidate(row) if row else None

    def list_candidates(
        self,
        *,
        agent_id: str | None = None,
        status: PromotionState | None = None,
        limit: int = 100,
    ) -> list[MemoryCandidate]:
        query = "SELECT * FROM memory_candidates WHERE 1=1"
        params: list[Any] = []
        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def mark_candidate_promoted(
        self, candidate_id: str, memory_id: str
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memory_candidates SET status = ?, promoted_memory_id = ? "
                "WHERE candidate_id = ?",
                (PromotionState.PROMOTED.value, memory_id, candidate_id),
            )

    # ----------------------------------------------------------- retrieval/feedback
    def record_retrieval(
        self,
        *,
        agent_id: str,
        query: str,
        memory_ids: list[str],
        namespace: str | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RetrievalEvent:
        event = RetrievalEvent(
            agent_id=agent_id,
            namespace=namespace,
            query=query,
            memory_ids=list(memory_ids),
            workflow_id=workflow_id,
            task_id=task_id,
            execution_id=execution_id,
            created_at=created_at or self.clock(),
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO retrieval_events("
                "retrieval_id, agent_id, namespace, query, memory_ids_json, "
                "workflow_id, task_id, execution_id, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.retrieval_id,
                    event.agent_id,
                    event.namespace,
                    event.query,
                    _json(event.memory_ids),
                    event.workflow_id,
                    event.task_id,
                    event.execution_id,
                    event.created_at.isoformat(),
                ),
            )
        return event

    def list_retrieval_events(
        self,
        *,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
    ) -> list[RetrievalEvent]:
        query = "SELECT * FROM retrieval_events WHERE 1=1"
        params: list[Any] = []
        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if workflow_id is not None:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(task_id)
        if execution_id is not None:
            query += " AND execution_id = ?"
            params.append(execution_id)
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since.isoformat())
        if until is not None:
            query += " AND created_at <= ?"
            params.append(until.isoformat())
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_retrieval(row) for row in rows]

    def record_feedback(self, feedback: MemoryFeedback) -> bool:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO memory_feedback("
                    "feedback_id, retrieval_id, memory_id, workflow_id, task_id, "
                    "execution_id, agent_id, used, success, confidence_delta, "
                    "evidence_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        feedback.feedback_id,
                        feedback.retrieval_id,
                        feedback.memory_id,
                        feedback.workflow_id,
                        feedback.task_id,
                        feedback.execution_id,
                        feedback.agent_id,
                        int(feedback.used),
                        int(feedback.success),
                        feedback.confidence_delta,
                        _json(feedback.evidence),
                        feedback.created_at.isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def list_feedback(
        self,
        *,
        workflow_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 200,
    ) -> list[MemoryFeedback]:
        query = "SELECT * FROM memory_feedback WHERE 1=1"
        params: list[Any] = []
        if workflow_id is not None:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(task_id)
        if execution_id is not None:
            query += " AND execution_id = ?"
            params.append(execution_id)
        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(agent_id)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_feedback(row) for row in rows]

    # -------------------------------------------------------------------- hygiene
    def prune(
        self, *, agent_id: str | None = None, now: datetime | None = None
    ) -> list[str]:
        now = now or self.clock()
        deleted: list[str] = []
        memories = self.list_memories(agent_id=agent_id, include_expired=True)
        for memory in memories:
            if memory.expires_at is not None and memory.expires_at <= now:
                deleted.append(memory.memory_id)
                continue
            if memory.promotion_state == PromotionState.PROMOTED:
                continue
            if memory.verification_level == VerificationLevel.REAL_EXECUTION:
                continue
            age_days = max(
                0.0, (now - memory.created_at).total_seconds() / 86400.0
            )
            if age_days < min(1.0, self.decay_half_life_days / 2):
                continue
            if self.effective_importance(memory, now) < self.forget_threshold:
                deleted.append(memory.memory_id)
        for memory_id in deleted:
            self._delete_memory(memory_id)
        return deleted

    def effective_importance(
        self, memory: EvolvedMemory, now: datetime | None = None
    ) -> float:
        now = now or self.clock()
        age_days = max(0.0, (now - memory.created_at).total_seconds() / 86400.0)
        decay = math.pow(0.5, age_days / self.decay_half_life_days)
        weight = {
            VerificationLevel.REAL_EXECUTION: 1.0,
            VerificationLevel.MOCK_SIMULATION: 0.55,
            VerificationLevel.UNVERIFIED: 0.25,
        }[memory.verification_level]
        access_boost = 1.0 + min(0.25, 0.02 * memory.access_count)
        return max(0.0, min(1.0, memory.importance * decay * weight * access_boost))

    def stats(self, *, agent_id: str | None = None) -> MemoryStats:
        memories = self.list_memories(agent_id=agent_id, include_expired=True)
        active = self.list_memories(agent_id=agent_id, include_expired=False)
        candidates = self.list_candidates(agent_id=agent_id)
        feedback = self.list_feedback(agent_id=agent_id, limit=10_000)
        by_agent: dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_verification: dict[str, int] = {}
        for memory in memories:
            by_agent[memory.agent_id] = by_agent.get(memory.agent_id, 0) + 1
            by_type[memory.memory_type.value] = (
                by_type.get(memory.memory_type.value, 0) + 1
            )
            by_verification[memory.verification_level.value] = (
                by_verification.get(memory.verification_level.value, 0) + 1
            )
        return MemoryStats(
            total=len(memories),
            active=len(active),
            candidates=len(candidates),
            promoted=sum(
                1 for memory in memories if memory.promotion_state == PromotionState.PROMOTED
            ),
            feedback_used=sum(1 for item in feedback if item.used),
            feedback_success=sum(1 for item in feedback if item.used and item.success),
            by_agent=by_agent,
            by_type=by_type,
            by_verification=by_verification,
        )

    def clear(
        self,
        *,
        agent_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock, self._conn:
            for table in (
                "evolved_memories",
                "memory_candidates",
                "retrieval_events",
            ):
                conditions = []
                params: list[Any] = []
                if agent_id is not None:
                    conditions.append("agent_id = ?")
                    params.append(agent_id)
                if namespace is not None:
                    conditions.append("namespace = ?")
                    params.append(namespace)
                where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
                cursor = self._conn.execute(f"DELETE FROM {table}{where}", params)
                counts[table] = cursor.rowcount

            conditions = []
            feedback_params: list[Any] = []
            if agent_id is not None:
                conditions.append("agent_id = ?")
                feedback_params.append(agent_id)
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = self._conn.execute(f"DELETE FROM memory_feedback{where}", feedback_params)
            counts["memory_feedback"] = cursor.rowcount
        return counts

    # ------------------------------------------------------------------- internal
    def _insert_memory(self, memory: EvolvedMemory) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO evolved_memories("
                "memory_id, agent_id, namespace, memory_type, verification_level, "
                "content, importance, confidence, support_count, source_json, "
                "metadata_json, embedding_json, access_count, last_accessed_at, "
                "created_at, expires_at, promotion_state, dedupe_key"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _memory_values(memory),
            )

    def _update_memory(self, memory: EvolvedMemory) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE evolved_memories SET "
                "content = ?, importance = ?, confidence = ?, support_count = ?, "
                "source_json = ?, metadata_json = ?, embedding_json = ?, "
                "access_count = ?, last_accessed_at = ?, expires_at = ?, "
                "promotion_state = ?, dedupe_key = ? "
                "WHERE memory_id = ?",
                (
                    memory.content,
                    memory.importance,
                    memory.confidence,
                    memory.support_count,
                    _json(memory.source.model_dump(mode="json") if memory.source else {}),
                    _json(memory.metadata),
                    _json(memory.embedding),
                    memory.access_count,
                    memory.last_accessed_at.isoformat()
                    if memory.last_accessed_at
                    else None,
                    memory.expires_at.isoformat() if memory.expires_at else None,
                    memory.promotion_state.value,
                    memory.dedupe_key,
                    memory.memory_id,
                ),
            )

    def _delete_memory(self, memory_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM evolved_memories WHERE memory_id = ?", (memory_id,)
            )

    def _mark_accessed(self, memory_ids: list[str], now: datetime) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE evolved_memories SET access_count = access_count + 1, "
                f"last_accessed_at = ? WHERE memory_id IN ({placeholders})",
                [now.isoformat(), *memory_ids],
            )

    def _active_rows(
        self, agent_id: str, namespace: str | None
    ) -> list[EvolvedMemory]:
        query = (
            "SELECT * FROM evolved_memories WHERE agent_id = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: list[Any] = [agent_id, self.clock().isoformat()]
        if namespace is not None:
            query += " AND namespace = ?"
            params.append(namespace)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_memory(row) for row in rows]

    def _verification_allowed(self, level: VerificationLevel) -> bool:
        if self.retrieval_config.include_unverified:
            return True
        if self.retrieval_config.include_mock:
            return level in {
                VerificationLevel.REAL_EXECUTION,
                VerificationLevel.MOCK_SIMULATION,
            }
        return level == VerificationLevel.REAL_EXECUTION

    def _rank(
        self, query: str, memories: list[EvolvedMemory], now: datetime
    ) -> list[tuple[EvolvedMemory, float]]:
        query_tokens = tokenize(query)
        query_embedding = self.embedding_provider.embed(query)
        ranked: list[tuple[EvolvedMemory, float]] = []
        for memory in memories:
            semantic = cosine_similarity(query_embedding, memory.embedding)
            memory_tokens = tokenize(memory.content)
            union = query_tokens | memory_tokens
            keyword = (len(query_tokens & memory_tokens) / len(union)) if union else 0.0
            importance = self.effective_importance(memory, now)
            verification = {
                VerificationLevel.REAL_EXECUTION: 1.0,
                VerificationLevel.MOCK_SIMULATION: 0.45,
                VerificationLevel.UNVERIFIED: 0.15,
            }[memory.verification_level]
            type_boost = {
                MemoryType.PROCEDURAL: 0.1,
                MemoryType.SEMANTIC: 0.04,
                MemoryType.EPISODIC: 0.0,
                MemoryType.WORKING: -0.05,
            }[memory.memory_type]
            score = (
                0.45 * max(0.0, semantic)
                + 0.25 * keyword
                + 0.20 * importance
                + 0.10 * verification
                + type_boost
            )
            ranked.append((memory, score))
        ranked.sort(
            key=lambda item: (
                item[1],
                item[0].confidence,
                item[0].created_at,
            ),
            reverse=True,
        )
        return ranked

    def _insert_candidate(self, candidate: MemoryCandidate) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO memory_candidates("
                "candidate_id, agent_id, namespace, signature, verification_level, "
                "content, procedure_json, support_count, "
                "evidence_workflow_ids_json, evidence_tool_call_ids_json, "
                "evidence_task_ids_json, first_seen, last_seen, "
                "promoted_memory_id, status"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _candidate_values(candidate),
            )

    def _update_candidate(self, candidate: MemoryCandidate) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memory_candidates SET verification_level = ?, content = ?, "
                "procedure_json = ?, support_count = ?, "
                "evidence_workflow_ids_json = ?, evidence_tool_call_ids_json = ?, "
                "evidence_task_ids_json = ?, last_seen = ?, "
                "promoted_memory_id = ?, status = ? WHERE candidate_id = ?",
                (
                    candidate.verification_level.value,
                    candidate.content,
                    _json(candidate.procedure),
                    candidate.support_count,
                    _json(candidate.evidence_workflow_ids),
                    _json(candidate.evidence_tool_call_ids),
                    _json(candidate.evidence_task_ids),
                    candidate.last_seen.isoformat(),
                    candidate.promoted_memory_id,
                    candidate.status.value,
                    candidate.candidate_id,
                ),
            )


def _memory_values(memory: EvolvedMemory) -> tuple:
    return (
        memory.memory_id,
        memory.agent_id,
        memory.namespace,
        memory.memory_type.value,
        memory.verification_level.value,
        memory.content,
        memory.importance,
        memory.confidence,
        memory.support_count,
        _json(memory.source.model_dump(mode="json") if memory.source else {}),
        _json(memory.metadata),
        _json(memory.embedding),
        memory.access_count,
        memory.last_accessed_at.isoformat() if memory.last_accessed_at else None,
        memory.created_at.isoformat(),
        memory.expires_at.isoformat() if memory.expires_at else None,
        memory.promotion_state.value,
        memory.dedupe_key,
    )


def _candidate_values(candidate: MemoryCandidate) -> tuple:
    return (
        candidate.candidate_id,
        candidate.agent_id,
        candidate.namespace,
        candidate.signature,
        candidate.verification_level.value,
        candidate.content,
        _json(candidate.procedure),
        candidate.support_count,
        _json(candidate.evidence_workflow_ids),
        _json(candidate.evidence_tool_call_ids),
        _json(candidate.evidence_task_ids),
        candidate.first_seen.isoformat(),
        candidate.last_seen.isoformat(),
        candidate.promoted_memory_id,
        candidate.status.value,
    )


def _row_to_memory(row: sqlite3.Row) -> EvolvedMemory:
    source_data = _loads(row["source_json"], {})
    return EvolvedMemory(
        memory_id=row["memory_id"],
        agent_id=row["agent_id"],
        namespace=row["namespace"],
        memory_type=MemoryType(row["memory_type"]),
        verification_level=VerificationLevel(row["verification_level"]),
        content=row["content"],
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        support_count=int(row["support_count"]),
        source=MemorySource.model_validate(source_data) if source_data else None,
        metadata=_loads(row["metadata_json"], {}),
        embedding=_loads(row["embedding_json"], []),
        access_count=int(row["access_count"]),
        last_accessed_at=(
            datetime.fromisoformat(row["last_accessed_at"])
            if row["last_accessed_at"]
            else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=(
            datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
        ),
        promotion_state=PromotionState(row["promotion_state"]),
        dedupe_key=row["dedupe_key"],
    )


def _row_to_candidate(row: sqlite3.Row) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=row["candidate_id"],
        agent_id=row["agent_id"],
        namespace=row["namespace"],
        signature=row["signature"],
        verification_level=VerificationLevel(row["verification_level"]),
        content=row["content"],
        procedure=_loads(row["procedure_json"], {}),
        support_count=int(row["support_count"]),
        evidence_workflow_ids=_loads(row["evidence_workflow_ids_json"], []),
        evidence_tool_call_ids=_loads(row["evidence_tool_call_ids_json"], []),
        evidence_task_ids=_loads(row["evidence_task_ids_json"], []),
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
        promoted_memory_id=row["promoted_memory_id"],
        status=PromotionState(row["status"]),
    )


def _row_to_retrieval(row: sqlite3.Row) -> RetrievalEvent:
    return RetrievalEvent(
        retrieval_id=row["retrieval_id"],
        agent_id=row["agent_id"],
        namespace=row["namespace"],
        query=row["query"],
        memory_ids=_loads(row["memory_ids_json"], []),
        workflow_id=row["workflow_id"],
        task_id=row["task_id"],
        execution_id=row["execution_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_feedback(row: sqlite3.Row) -> MemoryFeedback:
    return MemoryFeedback(
        feedback_id=row["feedback_id"],
        retrieval_id=row["retrieval_id"],
        memory_id=row["memory_id"],
        workflow_id=row["workflow_id"],
        task_id=row["task_id"],
        execution_id=row["execution_id"],
        agent_id=row["agent_id"],
        used=bool(row["used"]),
        success=bool(row["success"]),
        confidence_delta=float(row["confidence_delta"]),
        evidence=_loads(row["evidence_json"], {}),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _to_memory_entry(
    memory: EvolvedMemory,
    *,
    content: str | None = None,
    prefix: bool = True,
) -> MemoryEntry:
    return MemoryEntry(
        memory_id=memory.memory_id,
        agent_id=memory.agent_id,
        namespace=memory.namespace,
        kind=memory.memory_type.value,
        content=content if content is not None else (
            _prefixed_content(memory) if prefix else memory.content
        ),
        importance=memory.importance,
        created_at=memory.created_at,
        metadata={
            "memory_id": memory.memory_id,
            "memory_type": memory.memory_type.value,
            "verification_level": memory.verification_level.value,
            "confidence": memory.confidence,
            "support_count": memory.support_count,
            "promotion_state": memory.promotion_state.value,
            "source": memory.source.model_dump(mode="json") if memory.source else None,
            "procedure": memory.metadata.get("procedure"),
        },
    )


def _prefixed_content(memory: EvolvedMemory) -> str:
    label = (
        f"[{memory.memory_type.value}|{memory.verification_level.value}"
        f"|confidence={memory.confidence:.2f}|support={memory.support_count}]"
    )
    if memory.verification_level == VerificationLevel.MOCK_SIMULATION:
        label += "[MOCK SIMULATION - NOT REAL-WORLD VERIFIED]"
    return f"{label} {memory.content}"


def _memory_type_from_kind(
    kind: str, metadata: dict[str, Any]
) -> MemoryType:
    explicit = metadata.get("memory_type")
    if explicit:
        try:
            return MemoryType(str(explicit))
        except ValueError:
            pass
    normalized = kind.lower()
    if normalized in {"procedural", "procedure"}:
        return MemoryType.PROCEDURAL
    if normalized in {"semantic", "fact"}:
        return MemoryType.SEMANTIC
    if normalized in {"working", "working_memory"}:
        return MemoryType.WORKING
    return MemoryType.EPISODIC


def _verification_from_value(value: Any) -> VerificationLevel:
    if isinstance(value, VerificationLevel):
        return value
    try:
        return VerificationLevel(str(value))
    except ValueError:
        return VerificationLevel.UNVERIFIED


def _default_confidence(verification: VerificationLevel) -> float:
    return {
        VerificationLevel.REAL_EXECUTION: 0.75,
        VerificationLevel.MOCK_SIMULATION: 0.3,
        VerificationLevel.UNVERIFIED: 0.15,
    }[verification]


def _merge_lists(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys([*first, *second]))


def _merge_sources(
    first: MemorySource | None, second: MemorySource | None
) -> MemorySource | None:
    if first is None:
        return second
    if second is None:
        return first
    return first.model_copy(
        update={
            "tool_call_ids": _merge_lists(
                first.tool_call_ids, second.tool_call_ids
            ),
            "tool_names": _merge_lists(first.tool_names, second.tool_names),
        }
    )


def _promotion_rank(state: PromotionState) -> int:
    return {
        PromotionState.NONE: 0,
        PromotionState.CANDIDATE: 1,
        PromotionState.PROVISIONAL: 2,
        PromotionState.PROMOTED: 3,
    }[state]
