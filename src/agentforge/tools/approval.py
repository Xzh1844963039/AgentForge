"""Approval gate used for destructive tools.

Phase 2 provides a minimal gate: by default, approval-required tools return
``PENDING_APPROVAL`` and no side effect is performed. Phase 4 will connect this
interface to an interactive human-in-the-loop workflow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agentforge.models.tool import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
)


class ApprovalGate(ABC):
    @abstractmethod
    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Return approved, denied, or pending for a destructive action."""


class PendingApprovalGate(ApprovalGate):
    """Default Phase 2 gate: no human channel, so destructive actions wait."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            approval_id=request.approval_id,
            status=ApprovalStatus.PENDING,
            reason=(
                "human approval is required for this action; no interactive "
                "approval channel is configured in Phase 2"
            ),
            decided_by="system",
        )


class AutoApprovalGate(ApprovalGate):
    """Deterministic gate for tests and explicitly authorized automation."""

    def __init__(self, *, approved: bool, reason: str = "test decision") -> None:
        self.approved = approved
        self.reason = reason
        self.requests: list[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision(
            approval_id=request.approval_id,
            status=(
                ApprovalStatus.APPROVED if self.approved else ApprovalStatus.DENIED
            ),
            reason=self.reason,
            decided_by="auto",
        )
