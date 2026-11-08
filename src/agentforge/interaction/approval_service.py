"""Persistent, parameter-bound human approval service."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from agentforge.interaction.repository import InteractionRepository
from agentforge.models.common import utc_now
from agentforge.models.tool import ApprovalDecision, ApprovalRequest, ApprovalStatus
from agentforge.models.workflow import WorkflowEvent
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.tools.approval import ApprovalGate

logger = logging.getLogger("agentforge.interaction.approvals")


class ApprovalStateError(RuntimeError):
    pass


class ApprovalService(ApprovalGate):
    """Approval gate backed by SQLite with binding, expiry and one-time consume."""

    def __init__(
        self,
        store: SQLiteStore,
        events: EventLogger,
        *,
        ttl_seconds: int = 3600,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repo = InteractionRepository(store)
        self.events = events
        self.ttl_seconds = max(1, ttl_seconds)
        self.clock = clock

    # ------------------------------------------------------------------- gate API
    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.expire_overdue()
        now = self.clock()
        if request.expires_at is None:
            request = request.model_copy(
                update={
                    "expires_at": now + timedelta(seconds=self.ttl_seconds),
                    "status": ApprovalStatus.PENDING,
                }
            )
        self.repo.save_approval(request)
        self.events.event(
            "approval_requested",
            approval_id=request.approval_id,
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            tool_name=request.tool_name,
            argument_digest=request.argument_digest,
            risk_level=request.risk_level.value,
            expires_at=request.expires_at.isoformat()
            if request.expires_at
            else None,
        )
        self._workflow_event(
            request.workflow_id, "approval_requested", approval_id=request.approval_id
        )
        return ApprovalDecision(
            approval_id=request.approval_id,
            status=ApprovalStatus.PENDING,
            reason="awaiting human decision",
            decided_by="system",
        )

    async def consume_approval(
        self,
        approval_id: str,
        *,
        tool_name: str,
        argument_digest: str,
        workflow_id: str | None,
        task_id: str | None,
        agent_id: str,
    ) -> ApprovalDecision:
        self.expire_overdue()
        request = self.repo.get_approval(approval_id)
        if request is None:
            return self._deny(approval_id, "unknown approval request")
        if request.consumed_at is not None:
            return self._deny(
                approval_id, "approval token has already been consumed"
            )
        if request.tool_name != tool_name or request.argument_digest != argument_digest:
            return self._deny(
                approval_id,
                "approval arguments do not match the requested tool call",
            )
        if (
            request.workflow_id != workflow_id
            or request.task_id != task_id
            or request.agent_id != agent_id
        ):
            return self._deny(
                approval_id, "approval is bound to a different task/agent/workflow"
            )
        if request.expires_at is not None and self.clock() >= request.expires_at:
            self._set_status(request, ApprovalStatus.EXPIRED, "expired")
            return ApprovalDecision(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="approval has expired",
                decided_by="system",
            )
        if request.status != ApprovalStatus.APPROVED:
            return ApprovalDecision(
                approval_id=approval_id,
                status=request.status,
                reason=f"approval status is {request.status.value}",
                decided_by="system",
            )

        now = self.clock()
        if not self.repo.mark_approval_consumed(approval_id, now):
            return self._deny(
                approval_id, "approval token was consumed concurrently"
            )
        self.events.event(
            "approval_consumed",
            approval_id=approval_id,
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            tool_name=tool_name,
        )
        self._workflow_event(
            request.workflow_id, "approval_consumed", approval_id=approval_id
        )
        return ApprovalDecision(
            approval_id=approval_id,
            status=ApprovalStatus.APPROVED,
            reason="approval token consumed",
            decided_by=request.decided_by or "human",
        )

    # ------------------------------------------------------------------- human API
    def get(self, approval_id: str) -> ApprovalRequest | None:
        self.expire_overdue()
        return self.repo.get_approval(approval_id)

    def list(
        self,
        workflow_id: str | None = None,
        *,
        status: ApprovalStatus | None = None,
        limit: int | None = None,
    ) -> list[ApprovalRequest]:
        self.expire_overdue()
        return self.repo.list_approvals(
            workflow_id, status=status, limit=limit
        )

    def approve(
        self,
        approval_id: str,
        *,
        decided_by: str = "human",
        comment: str = "",
    ) -> ApprovalRequest:
        self.expire_overdue()
        request = self.repo.get_approval(approval_id)
        if request is None:
            raise ApprovalStateError(f"unknown approval {approval_id!r}")
        if request.status == ApprovalStatus.APPROVED:
            return request
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalStateError(
                f"cannot approve approval in status {request.status.value}"
            )
        return self._decide(
            request,
            ApprovalStatus.APPROVED,
            "approved",
            decided_by,
            comment,
        )

    def reject(
        self,
        approval_id: str,
        *,
        decided_by: str = "human",
        comment: str = "",
    ) -> ApprovalRequest:
        self.expire_overdue()
        request = self.repo.get_approval(approval_id)
        if request is None:
            raise ApprovalStateError(f"unknown approval {approval_id!r}")
        if request.status == ApprovalStatus.REJECTED:
            return request
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalStateError(
                f"cannot reject approval in status {request.status.value}"
            )
        return self._decide(
            request,
            ApprovalStatus.REJECTED,
            "rejected",
            decided_by,
            comment,
        )

    def cancel(self, approval_id: str, *, reason: str = "cancelled") -> ApprovalRequest:
        request = self.repo.get_approval(approval_id)
        if request is None:
            raise ApprovalStateError(f"unknown approval {approval_id!r}")
        if request.status in {
            ApprovalStatus.CANCELLED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.REJECTED,
        }:
            return request
        if request.consumed_at is not None:
            raise ApprovalStateError("cannot cancel a consumed approval")
        return self._decide(
            request,
            ApprovalStatus.CANCELLED,
            reason,
            "system",
            reason,
        )

    def cancel_for_workflow(self, workflow_id: str) -> list[str]:
        cancelled: list[str] = []
        for request in self.repo.list_approvals(
            workflow_id, status=ApprovalStatus.PENDING
        ):
            self._decide(
                request,
                ApprovalStatus.CANCELLED,
                "workflow_cancelled",
                "system",
                "workflow cancelled",
            )
            cancelled.append(request.approval_id)
        return cancelled

    def expire_overdue(self) -> list[str]:
        now = self.clock()
        expired: list[str] = []
        for request in self.repo.list_approvals(status=ApprovalStatus.PENDING):
            if request.expires_at is not None and now >= request.expires_at:
                self._set_status(request, ApprovalStatus.EXPIRED, "expired")
                expired.append(request.approval_id)
        return expired

    # ------------------------------------------------------------------- metrics
    def metrics(self, workflow_id: str) -> dict[str, Any]:
        approvals = self.repo.list_approvals(workflow_id)
        decided = [
            item
            for item in approvals
            if item.decided_at is not None
            and item.status
            in {
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
            }
        ]
        waits = [
            (item.decided_at - item.created_at).total_seconds()
            for item in decided
            if item.decided_at is not None
        ]
        approved = sum(
            1 for item in approvals if item.status == ApprovalStatus.APPROVED
        )
        rejected = sum(
            1 for item in approvals if item.status == ApprovalStatus.REJECTED
        )
        expired = sum(
            1 for item in approvals if item.status == ApprovalStatus.EXPIRED
        )
        decided_count = len(decided)
        return {
            "approval_requests": len(approvals),
            "approval_approved": approved,
            "approval_rejected": rejected,
            "approval_expired": expired,
            "approval_rate": approved / decided_count if decided_count else 0.0,
            "approval_rejection_rate": (
                rejected / decided_count if decided_count else 0.0
            ),
            "average_approval_wait_seconds": (
                sum(waits) / len(waits) if waits else 0.0
            ),
        }

    # ------------------------------------------------------------------- helpers
    def _decide(
        self,
        request: ApprovalRequest,
        status: ApprovalStatus,
        decision: str,
        decided_by: str,
        comment: str,
    ) -> ApprovalRequest:
        updated = request.model_copy(
            update={
                "status": status,
                "decision": decision,
                "decided_at": self.clock(),
                "decided_by": decided_by,
                "comment": comment,
            }
        )
        self.repo.save_approval(updated)
        self.events.event(
            "approval_decided",
            approval_id=updated.approval_id,
            workflow_id=updated.workflow_id,
            task_id=updated.task_id,
            status=status.value,
            decision=decision,
            decided_by=decided_by,
            comment=comment,
        )
        self._workflow_event(
            updated.workflow_id,
            "approval_decided",
            approval_id=updated.approval_id,
            status=status.value,
        )
        return updated

    def _set_status(
        self, request: ApprovalRequest, status: ApprovalStatus, decision: str
    ) -> ApprovalRequest:
        return self._decide(
            request, status, decision, "system", decision
        )

    def _deny(self, approval_id: str, reason: str) -> ApprovalDecision:
        return ApprovalDecision(
            approval_id=approval_id,
            status=ApprovalStatus.REJECTED,
            reason=reason,
            decided_by="system",
        )

    def _workflow_event(
        self, workflow_id: str | None, event_type: str, **metadata: Any
    ) -> None:
        if not workflow_id:
            return
        self.repo.append_event(
            WorkflowEvent(
                workflow_id=workflow_id,
                event_type=event_type,
                metadata=metadata,
            )
        )
