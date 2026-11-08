"""Task, execution-plan and agent-action domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agentforge.models.common import new_id, utc_now
from agentforge.models.tool import ToolCall


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


class Task(BaseModel):
    """A unit of work in a workflow's task graph."""

    task_id: str = Field(default_factory=lambda: new_id("task"))
    workflow_id: str
    parent_task_id: str | None = None
    description: str = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    assigned_agent: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    depth: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or utc_now()
        return max(0.0, (end - self.started_at).total_seconds())


class SubtaskSpec(BaseModel):
    """A subtask produced by the Coordinator's planner."""

    key: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    kind: str = Field(default="generic")

    @field_validator("required_capabilities")
    @classmethod
    def _clean_capabilities(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("each subtask must require at least one capability")
        return cleaned


class ExecutionPlan(BaseModel):
    """Structured, LLM-generated execution plan."""

    goal_summary: str = Field(default="", max_length=2000)
    subtasks: list[SubtaskSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> "ExecutionPlan":
        keys = [subtask.key for subtask in self.subtasks]
        if len(keys) != len(set(keys)):
            raise ValueError("subtask keys must be unique")
        return self


class DelegatedSubtask(BaseModel):
    """A subtask an executing agent asks the platform to delegate."""

    description: str = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    kind: str = Field(default="delegated")

    @field_validator("required_capabilities")
    @classmethod
    def _clean_capabilities(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("delegated subtasks need at least one capability")
        return cleaned


class DelegationRequest(BaseModel):
    subtasks: list[DelegatedSubtask] = Field(min_length=1, max_length=8)
    reason: str | None = None


class AgentActionType(str, Enum):
    COMPLETE = "complete"
    DELEGATE = "delegate"
    TOOL_CALL = "tool_call"
    CLARIFY = "clarify"
    REJECT_ASSIGNMENT = "reject_assignment"
    FAIL = "fail"


class AgentAction(BaseModel):
    """The structured decision returned by an executing agent's LLM call."""

    action: AgentActionType
    content: str = ""
    delegation: DelegationRequest | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    question: str | None = None
    clarification_context: str = ""
    possible_interpretations: list[str] = Field(default_factory=list)
    reason: str | None = None
    error: str | None = None
    reasoning: str | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self) -> "AgentAction":
        if self.action == AgentActionType.COMPLETE and not self.content.strip():
            raise ValueError("complete actions require non-empty content")
        if self.action == AgentActionType.DELEGATE:
            if self.delegation is None or not self.delegation.subtasks:
                raise ValueError("delegate actions require at least one subtask")
        if self.action == AgentActionType.TOOL_CALL and not self.tool_calls:
            raise ValueError("tool_call actions require at least one tool call")
        if self.action == AgentActionType.CLARIFY and not (
            self.question and self.question.strip()
        ):
            raise ValueError("clarify actions require a question")
        if self.action == AgentActionType.REJECT_ASSIGNMENT and not (
            (self.reason and self.reason.strip()) or self.content.strip()
        ):
            raise ValueError("reject_assignment actions require a reason")
        if self.action == AgentActionType.FAIL and not (
            (self.error and self.error.strip()) or self.content.strip()
        ):
            raise ValueError("fail actions require an error or content")
        return self
