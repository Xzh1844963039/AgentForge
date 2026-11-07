"""SQLite persistence for conversations, messages, tasks, executions and memory."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from agentforge.models.execution import AgentExecution, ExecutionStatus
from agentforge.models.message import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
)
from agentforge.models.task import Task, TaskStatus
from agentforge.models.workflow import Workflow, WorkflowMetrics, WorkflowStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    conversation_type TEXT NOT NULL,
    title TEXT NOT NULL,
    participants TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    receiver TEXT,
    message_type TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    correlation_id TEXT,
    task_id TEXT,
    in_reply_to TEXT,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    parent_task_id TEXT,
    description TEXT NOT NULL,
    required_capabilities TEXT NOT NULL,
    assigned_agent TEXT,
    dependencies TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    depth INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_workflow ON tasks(workflow_id, created_at);

CREATE TABLE IF NOT EXISTS agent_executions (
    execution_id TEXT PRIMARY KEY,
    workflow_id TEXT,
    task_id TEXT,
    agent_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error TEXT,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    latency_ms REAL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_executions_workflow
    ON agent_executions(workflow_id, started_at);

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    final_result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    metrics TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_created ON workflows(created_at);

CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_agent
    ON memory_entries(agent_id, namespace, created_at);
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


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class SQLiteStore:
    """Thread-safe SQLite repository.

    The store is intentionally synchronous: local SQLite writes are fast and
    the async orchestrator calls them at natural task boundaries.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) "
                "VALUES('schema_version', '1')"
            )

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------------- conversations
    def save_conversation(self, conversation: Conversation) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO conversations(
                    conversation_id, conversation_type, title, participants,
                    created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    title=excluded.title,
                    participants=excluded.participants,
                    metadata=excluded.metadata
                """,
                (
                    conversation.conversation_id,
                    conversation.conversation_type.value,
                    conversation.title,
                    _json(conversation.participants),
                    conversation.created_at.isoformat(),
                    _json(conversation.metadata),
                ),
            )

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return Conversation(
            conversation_id=row["conversation_id"],
            conversation_type=ConversationType(row["conversation_type"]),
            title=row["title"],
            participants=_loads(row["participants"], []),
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=_loads(row["metadata"], {}),
        )

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            Conversation(
                conversation_id=row["conversation_id"],
                conversation_type=ConversationType(row["conversation_type"]),
                title=row["title"],
                participants=_loads(row["participants"], []),
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=_loads(row["metadata"], {}),
            )
            for row in rows
        ]

    # -------------------------------------------------------------------- messages
    def save_message(self, message: Message) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO messages(
                    message_id, conversation_id, sender, receiver, message_type,
                    content, timestamp, correlation_id, task_id, in_reply_to, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.conversation_id,
                    message.sender,
                    message.receiver,
                    message.message_type.value,
                    message.content,
                    message.timestamp.isoformat(),
                    message.correlation_id,
                    message.task_id,
                    message.in_reply_to,
                    _json(message.metadata),
                ),
            )

    def list_messages(
        self,
        conversation_id: str | None = None,
        *,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[Message]:
        query = "SELECT * FROM messages"
        params: list[Any] = []
        if conversation_id is not None:
            query += " WHERE conversation_id = ?"
            params.append(conversation_id)
        order = "DESC" if newest_first else "ASC"
        query += f" ORDER BY timestamp {order}, rowid {order}"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        messages = [self._row_to_message(row) for row in rows]
        if newest_first:
            messages.reverse()
        return messages

    def count_messages(self, conversation_id: str | None = None) -> int:
        query = "SELECT COUNT(*) AS n FROM messages"
        params: tuple[Any, ...] = ()
        if conversation_id is not None:
            query += " WHERE conversation_id = ?"
            params = (conversation_id,)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            sender=row["sender"],
            receiver=row["receiver"],
            message_type=MessageType(row["message_type"]),
            content=row["content"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            correlation_id=row["correlation_id"],
            task_id=row["task_id"],
            in_reply_to=row["in_reply_to"],
            metadata=_loads(row["metadata"], {}),
        )

    # ----------------------------------------------------------------------- tasks
    def save_task(self, task: Task) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tasks(
                    task_id, workflow_id, parent_task_id, description,
                    required_capabilities, assigned_agent, dependencies, status,
                    result, error, depth, created_at, started_at, finished_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    assigned_agent=excluded.assigned_agent,
                    dependencies=excluded.dependencies,
                    status=excluded.status,
                    result=excluded.result,
                    error=excluded.error,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    metadata=excluded.metadata
                """,
                (
                    task.task_id,
                    task.workflow_id,
                    task.parent_task_id,
                    task.description,
                    _json(task.required_capabilities),
                    task.assigned_agent,
                    _json(task.dependencies),
                    task.status.value,
                    task.result,
                    task.error,
                    task.depth,
                    task.created_at.isoformat(),
                    task.started_at.isoformat() if task.started_at else None,
                    task.finished_at.isoformat() if task.finished_at else None,
                    _json(task.metadata),
                ),
            )

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(
        self, workflow_id: str | None = None, *, limit: int | None = None
    ) -> list[Task]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        if workflow_id is not None:
            query += " WHERE workflow_id = ?"
            params.append(workflow_id)
        query += " ORDER BY created_at ASC, rowid ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            workflow_id=row["workflow_id"],
            parent_task_id=row["parent_task_id"],
            description=row["description"],
            required_capabilities=_loads(row["required_capabilities"], []),
            assigned_agent=row["assigned_agent"],
            dependencies=_loads(row["dependencies"], []),
            status=TaskStatus(row["status"]),
            result=row["result"],
            error=row["error"],
            depth=int(row["depth"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=_dt(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
            metadata=_loads(row["metadata"], {}),
        )

    # ------------------------------------------------------------------ executions
    def save_execution(self, execution: AgentExecution) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_executions(
                    execution_id, workflow_id, task_id, agent_id, purpose,
                    provider, model, started_at, finished_at, status, error,
                    prompt_tokens, completion_tokens, latency_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.execution_id,
                    execution.workflow_id,
                    execution.task_id,
                    execution.agent_id,
                    execution.purpose,
                    execution.provider,
                    execution.model,
                    execution.started_at.isoformat(),
                    execution.finished_at.isoformat()
                    if execution.finished_at
                    else None,
                    execution.status.value,
                    execution.error,
                    execution.prompt_tokens,
                    execution.completion_tokens,
                    execution.latency_ms,
                    _json(execution.metadata),
                ),
            )

    def list_executions(
        self, workflow_id: str | None = None, *, limit: int | None = None
    ) -> list[AgentExecution]:
        query = "SELECT * FROM agent_executions"
        params: list[Any] = []
        if workflow_id is not None:
            query += " WHERE workflow_id = ?"
            params.append(workflow_id)
        query += " ORDER BY started_at ASC, rowid ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_execution(row) for row in rows]

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> AgentExecution:
        return AgentExecution(
            execution_id=row["execution_id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            purpose=row["purpose"],
            provider=row["provider"],
            model=row["model"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
            status=ExecutionStatus(row["status"]),
            error=row["error"],
            prompt_tokens=int(row["prompt_tokens"]),
            completion_tokens=int(row["completion_tokens"]),
            latency_ms=row["latency_ms"],
            metadata=_loads(row["metadata"], {}),
        )

    # ------------------------------------------------------------------- workflows
    def save_workflow(self, workflow: Workflow) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO workflows(
                    workflow_id, goal, conversation_id, status, final_result,
                    error, created_at, finished_at, metrics, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    status=excluded.status,
                    final_result=excluded.final_result,
                    error=excluded.error,
                    finished_at=excluded.finished_at,
                    metrics=excluded.metrics,
                    metadata=excluded.metadata
                """,
                (
                    workflow.workflow_id,
                    workflow.goal,
                    workflow.conversation_id,
                    workflow.status.value,
                    workflow.final_result,
                    workflow.error,
                    workflow.created_at.isoformat(),
                    workflow.finished_at.isoformat()
                    if workflow.finished_at
                    else None,
                    _json(workflow.metrics.model_dump(mode="json")),
                    _json(workflow.metadata),
                ),
            )

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
        return self._row_to_workflow(row) if row else None

    def list_workflows(self, limit: int = 20) -> list[Workflow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflows ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_workflow(row) for row in rows]

    @staticmethod
    def _row_to_workflow(row: sqlite3.Row) -> Workflow:
        return Workflow(
            workflow_id=row["workflow_id"],
            goal=row["goal"],
            conversation_id=row["conversation_id"],
            status=WorkflowStatus(row["status"]),
            final_result=row["final_result"],
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            finished_at=_dt(row["finished_at"]),
            metrics=WorkflowMetrics(**_loads(row["metrics"], {})),
            metadata=_loads(row["metadata"], {}),
        )

    # ---------------------------------------------------------------------- memory
    def insert_memory(self, entry: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_entries(
                    memory_id, agent_id, namespace, kind, content, importance,
                    created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["memory_id"],
                    entry["agent_id"],
                    entry["namespace"],
                    entry["kind"],
                    entry["content"],
                    entry["importance"],
                    entry["created_at"].isoformat(),
                    _json(entry["metadata"]),
                ),
            )

    def list_memory_rows(
        self, agent_id: str, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_entries WHERE agent_id = ?"
        params: list[Any] = [agent_id]
        if namespace is not None:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "memory_id": row["memory_id"],
                "agent_id": row["agent_id"],
                "namespace": row["namespace"],
                "kind": row["kind"],
                "content": row["content"],
                "importance": float(row["importance"]),
                "created_at": datetime.fromisoformat(row["created_at"]),
                "metadata": _loads(row["metadata"], {}),
            }
            for row in rows
        ]

    def delete_memory(self, memory_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM memory_entries WHERE memory_id = ?", (memory_id,)
            )

    # ----------------------------------------------------------------------- misc
    def table_counts(self) -> dict[str, int]:
        tables: Iterable[str] = (
            "conversations",
            "messages",
            "tasks",
            "agent_executions",
            "workflows",
            "memory_entries",
        )
        counts: dict[str, int] = {}
        with self._lock:
            for table in tables:
                row = self._conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"  # table names are fixed above
                ).fetchone()
                counts[table] = int(row["n"]) if row else 0
        return counts
