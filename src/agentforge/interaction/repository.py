"""Persistence repository for approvals, clarifications and workflow events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from agentforge.models.clarification import ClarificationRequest, ClarificationStatus
from agentforge.models.tool import ApprovalRequest, ApprovalStatus, RiskLevel
from agentforge.models.workflow import WorkflowEvent
from agentforge.persistence.database import SQLiteStore


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
    return datetime.fromisoformat(value) if value else None


class InteractionRepository:
    """SQLite access for HITL state, kept outside the Phase 2 repository class."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ approvals
    def save_approval(self, approval: ApprovalRequest) -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                """
                INSERT INTO approvals(
                    approval_id, workflow_id, task_id, agent_id, tool_name,
                    action, summary, arguments, argument_digest, risk_level,
                    reason, details, status, decision, created_at, expires_at,
                    decided_at, decided_by, comment, consumed_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET
                    status=excluded.status,
                    decision=excluded.decision,
                    decided_at=excluded.decided_at,
                    decided_by=excluded.decided_by,
                    comment=excluded.comment,
                    consumed_at=excluded.consumed_at,
                    metadata=excluded.metadata
                """,
                (
                    approval.approval_id,
                    approval.workflow_id,
                    approval.task_id,
                    approval.agent_id,
                    approval.tool_name or approval.action,
                    approval.action,
                    approval.summary,
                    _json(approval.arguments),
                    approval.argument_digest,
                    approval.risk_level.value,
                    approval.reason,
                    _json(approval.details),
                    approval.status.value,
                    approval.decision,
                    approval.created_at.isoformat(),
                    approval.expires_at.isoformat()
                    if approval.expires_at
                    else None,
                    approval.decided_at.isoformat()
                    if approval.decided_at
                    else None,
                    approval.decided_by,
                    approval.comment,
                    approval.consumed_at.isoformat()
                    if approval.consumed_at
                    else None,
                    _json(approval.metadata),
                ),
            )

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return self._row_to_approval(row) if row else None

    def find_pending_approval(
        self,
        *,
        workflow_id: str,
        task_id: str,
        tool_name: str,
        argument_digest: str,
    ) -> ApprovalRequest | None:
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM approvals WHERE workflow_id = ? AND task_id = ? "
                "AND tool_name = ? AND argument_digest = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (
                    workflow_id,
                    task_id,
                    tool_name,
                    argument_digest,
                    ApprovalStatus.PENDING.value,
                ),
            ).fetchone()
        return self._row_to_approval(row) if row else None

    def list_approvals(
        self,
        workflow_id: str | None = None,
        *,
        status: ApprovalStatus | None = None,
        limit: int | None = None,
    ) -> list[ApprovalRequest]:
        query = "SELECT * FROM approvals WHERE 1=1"
        params: list[Any] = []
        if workflow_id is not None:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at ASC, rowid ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.store._lock:
            rows = self.store._conn.execute(query, params).fetchall()
        return [self._row_to_approval(row) for row in rows]

    @staticmethod
    def _row_to_approval(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row["approval_id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            tool_name=row["tool_name"],
            action=row["action"],
            summary=row["summary"],
            arguments=_loads(row["arguments"], {}),
            argument_digest=row["argument_digest"],
            risk_level=RiskLevel(row["risk_level"]),
            reason=row["reason"],
            details=_loads(row["details"], {}),
            status=ApprovalStatus(row["status"]),
            decision=row["decision"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
            decided_at=_dt(row["decided_at"]),
            decided_by=row["decided_by"],
            comment=row["comment"],
            consumed_at=_dt(row["consumed_at"]),
            metadata=_loads(row["metadata"], {}),
        )

    def mark_approval_consumed(self, approval_id: str, consumed_at: datetime) -> bool:
        """Atomically consume an approved token; returns False if already used."""
        with self.store._lock, self.store._conn:
            cursor = self.store._conn.execute(
                "UPDATE approvals SET consumed_at = ? "
                "WHERE approval_id = ? AND consumed_at IS NULL AND status = ?",
                (
                    consumed_at.isoformat(),
                    approval_id,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            return cursor.rowcount == 1

    # --------------------------------------------------------------- clarifications
    def save_clarification(self, request: ClarificationRequest) -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                """
                INSERT INTO clarifications(
                    clarification_id, workflow_id, task_id, agent_id, question,
                    context, possible_interpretations, status, answer, created_at,
                    expires_at, answered_at, answered_by, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(clarification_id) DO UPDATE SET
                    status=excluded.status,
                    answer=excluded.answer,
                    answered_at=excluded.answered_at,
                    answered_by=excluded.answered_by,
                    metadata=excluded.metadata
                """,
                (
                    request.clarification_id,
                    request.workflow_id,
                    request.task_id,
                    request.agent_id,
                    request.question,
                    request.context,
                    _json(request.possible_interpretations),
                    request.status.value,
                    request.answer,
                    request.created_at.isoformat(),
                    request.expires_at.isoformat()
                    if request.expires_at
                    else None,
                    request.answered_at.isoformat()
                    if request.answered_at
                    else None,
                    request.answered_by,
                    _json(request.metadata),
                ),
            )

    def get_clarification(self, clarification_id: str) -> ClarificationRequest | None:
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM clarifications WHERE clarification_id = ?",
                (clarification_id,),
            ).fetchone()
        return self._row_to_clarification(row) if row else None

    def list_clarifications(
        self,
        workflow_id: str | None = None,
        *,
        status: ClarificationStatus | None = None,
        limit: int | None = None,
    ) -> list[ClarificationRequest]:
        query = "SELECT * FROM clarifications WHERE 1=1"
        params: list[Any] = []
        if workflow_id is not None:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at ASC, rowid ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.store._lock:
            rows = self.store._conn.execute(query, params).fetchall()
        return [self._row_to_clarification(row) for row in rows]

    @staticmethod
    def _row_to_clarification(row: sqlite3.Row) -> ClarificationRequest:
        return ClarificationRequest(
            clarification_id=row["clarification_id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            question=row["question"],
            context=row["context"],
            possible_interpretations=_loads(
                row["possible_interpretations"], []
            ),
            status=ClarificationStatus(row["status"]),
            answer=row["answer"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
            answered_at=_dt(row["answered_at"]),
            answered_by=row["answered_by"],
            metadata=_loads(row["metadata"], {}),
        )

    # --------------------------------------------------------------- workflow events
    def append_event(self, event: WorkflowEvent) -> None:
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "INSERT OR REPLACE INTO workflow_events("
                "event_id, workflow_id, event_type, created_at, metadata"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.workflow_id,
                    event.event_type,
                    event.created_at.isoformat(),
                    _json(event.metadata),
                ),
            )

    def list_events(
        self,
        workflow_id: str,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[WorkflowEvent]:
        query = "SELECT * FROM workflow_events WHERE workflow_id = ?"
        params: list[Any] = [workflow_id]
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at ASC, rowid ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.store._lock:
            rows = self.store._conn.execute(query, params).fetchall()
        return [
            WorkflowEvent(
                event_id=row["event_id"],
                workflow_id=row["workflow_id"],
                event_type=row["event_type"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=_loads(row["metadata"], {}),
            )
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.store._lock:
            for table in ("approvals", "clarifications", "workflow_events"):
                row = self.store._conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                ).fetchone()
                counts[table] = int(row["n"]) if row else 0
        return counts
