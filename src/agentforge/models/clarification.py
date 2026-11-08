"""Human clarification request models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agentforge.models.common import new_id, utc_now


class ClarificationStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ClarificationRequest(BaseModel):
    clarification_id: str = Field(default_factory=lambda: new_id("clarify"))
    workflow_id: str
    task_id: str
    agent_id: str
    question: str = Field(min_length=1)
    context: str = ""
    possible_interpretations: list[str] = Field(default_factory=list)
    status: ClarificationStatus = ClarificationStatus.OPEN
    answer: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    answered_at: datetime | None = None
    answered_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("possible_interpretations")
    @classmethod
    def _clean_interpretations(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]
