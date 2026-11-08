"""Persistent human clarification service."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from agentforge.interaction.repository import InteractionRepository
from agentforge.models.clarification import ClarificationRequest, ClarificationStatus
from agentforge.models.common import utc_now
from agentforge.models.workflow import WorkflowEvent
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger

logger = logging.getLogger("agentforge.interaction.clarifications")


class ClarificationStateError(RuntimeError):
    pass


class ClarificationService:
    def __init__(
        self,
        store: SQLiteStore,
        events: EventLogger,
        *,
        ttl_seconds: int = 86400,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repo = InteractionRepository(store)
        self.events = events
        self.ttl_seconds = max(1, ttl_seconds)
        self.clock = clock

    def create(
        self,
        *,
        workflow_id: str,
        task_id: str,
        agent_id: str,
        question: str,
        context: str = "",
        possible_interpretations: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClarificationRequest:
        request = ClarificationRequest(
            workflow_id=workflow_id,
            task_id=task_id,
            agent_id=agent_id,
            question=question,
            context=context,
            possible_interpretations=possible_interpretations or [],
            expires_at=self.clock() + timedelta(seconds=self.ttl_seconds),
            metadata=metadata or {},
        )
        self.repo.save_clarification(request)
        self.events.event(
            "clarification_requested",
            clarification_id=request.clarification_id,
            workflow_id=workflow_id,
            task_id=task_id,
            agent_id=agent_id,
            question=question,
        )
        self._workflow_event(
            workflow_id,
            "clarification_requested",
            clarification_id=request.clarification_id,
        )
        return request

    def get(self, clarification_id: str) -> ClarificationRequest | None:
        self.expire_overdue()
        return self.repo.get_clarification(clarification_id)

    def list(
        self,
        workflow_id: str | None = None,
        *,
        status: ClarificationStatus | None = None,
        limit: int | None = None,
    ) -> list[ClarificationRequest]:
        self.expire_overdue()
        return self.repo.list_clarifications(
            workflow_id, status=status, limit=limit
        )

    def answer(
        self,
        clarification_id: str,
        answer: str,
        *,
        answered_by: str = "human",
    ) -> ClarificationRequest:
        answer = answer.strip()
        if not answer:
            raise ClarificationStateError("clarification answer must not be empty")
        self.expire_overdue()
        request = self.repo.get_clarification(clarification_id)
        if request is None:
            raise ClarificationStateError(
                f"unknown clarification {clarification_id!r}"
            )
        if request.status == ClarificationStatus.ANSWERED:
            return request
        if request.status != ClarificationStatus.OPEN:
            raise ClarificationStateError(
                f"cannot answer clarification in status {request.status.value}"
            )
        updated = request.model_copy(
            update={
                "status": ClarificationStatus.ANSWERED,
                "answer": answer,
                "answered_at": self.clock(),
                "answered_by": answered_by,
            }
        )
        self.repo.save_clarification(updated)
        self.events.event(
            "clarification_answered",
            clarification_id=clarification_id,
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            answered_by=answered_by,
        )
        self._workflow_event(
            request.workflow_id,
            "clarification_answered",
            clarification_id=clarification_id,
        )
        return updated

    def cancel(
        self, clarification_id: str, *, reason: str = "cancelled"
    ) -> ClarificationRequest:
        request = self.repo.get_clarification(clarification_id)
        if request is None:
            raise ClarificationStateError(
                f"unknown clarification {clarification_id!r}"
            )
        if request.status == ClarificationStatus.CANCELLED:
            return request
        if request.status == ClarificationStatus.ANSWERED:
            return request
        updated = request.model_copy(
            update={"status": ClarificationStatus.CANCELLED, "answer": reason}
        )
        self.repo.save_clarification(updated)
        self._workflow_event(
            request.workflow_id,
            "clarification_cancelled",
            clarification_id=clarification_id,
        )
        return updated

    def cancel_for_workflow(self, workflow_id: str) -> list[str]:
        cancelled: list[str] = []
        for request in self.repo.list_clarifications(
            workflow_id, status=ClarificationStatus.OPEN
        ):
            self.cancel(request.clarification_id, reason="workflow_cancelled")
            cancelled.append(request.clarification_id)
        return cancelled

    def expire_overdue(self) -> list[str]:
        now = self.clock()
        expired: list[str] = []
        for request in self.repo.list_clarifications(
            status=ClarificationStatus.OPEN
        ):
            if request.expires_at is not None and now >= request.expires_at:
                updated = request.model_copy(
                    update={"status": ClarificationStatus.EXPIRED}
                )
                self.repo.save_clarification(updated)
                self._workflow_event(
                    request.workflow_id,
                    "clarification_expired",
                    clarification_id=request.clarification_id,
                )
                expired.append(request.clarification_id)
        return expired

    def metrics(self, workflow_id: str) -> dict[str, Any]:
        requests = self.repo.list_clarifications(workflow_id)
        answered = [
            item
            for item in requests
            if item.answered_at is not None
            and item.status == ClarificationStatus.ANSWERED
        ]
        waits = [
            (item.answered_at - item.created_at).total_seconds()
            for item in answered
            if item.answered_at is not None
        ]
        return {
            "clarifications": len(requests),
            "clarification_answered": len(answered),
            "average_clarification_wait_seconds": (
                sum(waits) / len(waits) if waits else 0.0
            ),
        }

    def _workflow_event(
        self, workflow_id: str, event_type: str, **metadata: Any
    ) -> None:
        self.repo.append_event(
            WorkflowEvent(
                workflow_id=workflow_id,
                event_type=event_type,
                metadata=metadata,
            )
        )
