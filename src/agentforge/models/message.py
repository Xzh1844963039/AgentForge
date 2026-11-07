"""Message and conversation domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agentforge.models.common import new_id, utc_now


class MessageType(str, Enum):
    """Kinds of messages routed by the platform."""

    DIRECT = "direct"
    GROUP = "group"
    TASK_REQUEST = "task_request"
    TASK_RESULT = "task_result"
    DELEGATION = "delegation"
    PLAN = "plan"
    SYSTEM = "system"
    ERROR = "error"


class ConversationType(str, Enum):
    DIRECT = "direct"
    GROUP = "group"


class Message(BaseModel):
    """A durable message exchanged between humans and agents.

    ``receiver`` is the target agent (for direct messages) or the group
    conversation id (for group messages).
    """

    message_id: str = Field(default_factory=lambda: new_id("msg"))
    conversation_id: str
    sender: str
    receiver: str | None = None
    message_type: MessageType
    content: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = None
    task_id: str | None = None
    in_reply_to: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value

    def to_transcript_line(self) -> str:
        return f"[{self.timestamp.isoformat()}] {self.sender} -> {self.receiver}: {self.content}"


class Conversation(BaseModel):
    """A direct or group conversation."""

    conversation_id: str = Field(default_factory=lambda: new_id("conv"))
    conversation_type: ConversationType
    title: str
    participants: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("participants")
    @classmethod
    def _participants_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a conversation must have at least one participant")
        if len(set(value)) != len(value):
            raise ValueError("conversation participants must be unique")
        return value
