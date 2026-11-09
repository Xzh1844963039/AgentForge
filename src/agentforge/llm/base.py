"""Provider-agnostic LLM interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, Field


class ChatRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    role: ChatRole
    content: str

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role=ChatRole.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role=ChatRole.USER, content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role=ChatRole.ASSISTANT, content=content)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    finish_reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMProviderError(RuntimeError):
    """Raised when an LLM call fails after retries."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.details = details or {}


class ProviderConfigurationError(LLMProviderError):
    """Raised when a provider cannot be constructed from configuration."""


class BaseLLMProvider(ABC):
    """Unified interface implemented by every provider."""

    name: str = "base"
    model: str = ""

    def __init__(
        self,
        *,
        default_temperature: float = 0.2,
        default_max_tokens: int = 2048,
    ) -> None:
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a completion for the given chat messages."""

    async def aclose(self) -> None:
        """Release network resources. Safe to call multiple times."""
        return None
