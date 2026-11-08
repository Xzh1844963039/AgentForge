"""Workflow-level state and metrics models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agentforge.models.common import new_id, utc_now


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowMetrics(BaseModel):
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    blocked_tasks: int = 0
    task_completion_rate: float = 0.0
    delegation_count: int = 0
    max_depth: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_execution_seconds: float = 0.0
    total_task_seconds: float = 0.0
    tool_calls: int = 0
    tool_failures: int = 0
    tool_seconds: float = 0.0
    approval_requests: int = 0
    approval_approved: int = 0
    approval_rejected: int = 0
    approval_expired: int = 0
    approval_rate: float = 0.0
    approval_rejection_rate: float = 0.0
    average_approval_wait_seconds: float = 0.0
    clarifications: int = 0
    clarification_answered: int = 0
    average_clarification_wait_seconds: float = 0.0
    workflow_pauses: int = 0
    workflow_resumes: int = 0
    workflow_cancellations: int = 0
    delegation_tasks: int = 0
    delegation_completed: int = 0
    delegation_success_rate: float = 0.0
    reassignments: int = 0


class WorkflowEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("wfevent"))
    workflow_id: str
    event_type: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: new_id("wf"))
    goal: str = Field(min_length=1)
    conversation_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    final_result: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metrics: WorkflowMetrics = Field(default_factory=WorkflowMetrics)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.created_at).total_seconds())
