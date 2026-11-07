"""Tool-call, approval and artifact domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agentforge.models.common import new_id, utc_now


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class PermissionDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"


class ToolCall(BaseModel):
    """A structured tool request produced by an agent LLM call."""

    call_id: str = Field(default_factory=lambda: new_id("call"))
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    """Durable audit record for one dispatcher invocation."""

    tool_call_id: str = Field(default_factory=lambda: new_id("toolcall"))
    workflow_id: str | None = None
    task_id: str | None = None
    agent_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0
    permission_decision: PermissionDecision = PermissionDecision.ALLOWED
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    approval_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=lambda: new_id("approval"))
    agent_id: str
    action: str
    summary: str
    workflow_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalDecision(BaseModel):
    approval_id: str
    status: ApprovalStatus
    reason: str = ""
    decided_by: str = "system"


class ArtifactRecord(BaseModel):
    """A controlled cross-agent artifact transfer."""

    artifact_id: str = Field(default_factory=lambda: new_id("artifact"))
    owner_agent_id: str
    filename: str
    stored_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    source_path: str
    workflow_id: str | None = None
    task_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filename")
    @classmethod
    def _filename_not_blank(cls, value: str) -> str:
        if not value.strip() or "/" in value or "\\" in value or ".." in value:
            raise ValueError("artifact filename must be a simple file name")
        return value
