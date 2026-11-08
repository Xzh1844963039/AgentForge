"""Approval interfaces, parameter binding and suspension signalling."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any

from agentforge.models.tool import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ToolCall,
)


def arguments_digest(arguments: dict[str, Any]) -> str:
    """Canonical SHA-256 digest binding an approval to exact arguments."""
    canonical = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApprovalRequired(RuntimeError):
    """Raised by ToolExecutor when a tool needs a human decision."""

    def __init__(
        self,
        request: ApprovalRequest,
        tool_call: ToolCall,
        *,
        reason: str = "approval_required",
    ) -> None:
        super().__init__(reason)
        self.request = request
        self.tool_call = tool_call
        self.reason = reason


class ApprovalGate(ABC):
    @abstractmethod
    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Return approved, rejected, pending or expired for an action."""

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
        """Validate and consume a previously approved token exactly once."""
        return ApprovalDecision(
            approval_id=approval_id,
            status=ApprovalStatus.REJECTED,
            reason="this approval gate does not support token consumption",
            decided_by="system",
        )


class PendingApprovalGate(ApprovalGate):
    """Default non-interactive gate: no human channel, so actions wait."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            approval_id=request.approval_id,
            status=ApprovalStatus.PENDING,
            reason=(
                "human approval is required for this action; no interactive "
                "approval channel is configured"
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
                ApprovalStatus.APPROVED
                if self.approved
                else ApprovalStatus.REJECTED
            ),
            reason=self.reason,
            decided_by="auto",
        )
