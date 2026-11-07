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
    COMPLETED = "completed"
    FAILED = "failed"


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
