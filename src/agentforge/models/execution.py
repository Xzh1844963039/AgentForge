"""Agent execution records used for observability and evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agentforge.models.common import new_id, utc_now


class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class AgentExecution(BaseModel):
    """One LLM-backed agent invocation (task execution or private chat)."""

    execution_id: str = Field(default_factory=lambda: new_id("exec"))
    workflow_id: str | None = None
    task_id: str | None = None
    agent_id: str
    purpose: str
    provider: str = ""
    model: str = ""
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.COMPLETED
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds())
