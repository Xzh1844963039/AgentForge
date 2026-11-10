"""Provider-agnostic LLM interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Sequence

import httpx
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


ERROR_CLASS_NO_RESPONSE = "no_response_disconnect"
ERROR_CLASS_TIMEOUT = "timeout"
ERROR_CLASS_TRANSPORT = "transport"
ERROR_CLASS_HTTP_CLIENT = "http_client_error"
ERROR_CLASS_HTTP_SERVER = "http_server_error"
ERROR_CLASS_PARSE = "parse_error"
ERROR_CLASS_PROVIDER = "provider_error"


def classify_transport_error(exc: BaseException) -> str:
    """Classify an httpx exception into a stable, loggable error class."""
    if isinstance(exc, httpx.TimeoutException):
        return ERROR_CLASS_TIMEOUT
    if isinstance(
        exc,
        (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
        ),
    ):
        return ERROR_CLASS_NO_RESPONSE
    if isinstance(exc, httpx.TransportError):
        return ERROR_CLASS_TRANSPORT
    return ERROR_CLASS_PROVIDER


class LLMProviderError(RuntimeError):
    """Raised when an LLM call fails after retries.

    ``error_class`` gives callers a stable taxonomy instead of string matching:
    ``no_response_disconnect``, ``timeout``, ``transport``, ``http_client_error``,
    ``http_server_error``, ``parse_error`` or ``provider_error``.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        error_class: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.details = details or {}
        if error_class is not None:
            self.error_class = error_class
        elif status_code is None:
            self.error_class = ERROR_CLASS_PROVIDER
        elif status_code >= 500:
            self.error_class = ERROR_CLASS_HTTP_SERVER
        else:
            self.error_class = ERROR_CLASS_HTTP_CLIENT


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
