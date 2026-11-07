"""OpenAI-compatible chat-completions provider with retry and timeouts.

Only DeepSeek and HKGAI endpoints are used by this project. The protocol is
OpenAI-compatible, but no OpenAI SDK or OpenAI API endpoint is used.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Sequence

import httpx

from agentforge.llm.base import (
    BaseLLMProvider,
    ChatMessage,
    LLMProviderError,
    LLMResponse,
    TokenUsage,
)

logger = logging.getLogger("agentforge.llm")


class OpenAICompatibleProvider(BaseLLMProvider):
    """Minimal async client for OpenAI-style ``/chat/completions`` endpoints."""

    RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        default_temperature: float = 0.2,
        default_max_tokens: int = 2048,
        timeout: float = 60.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not model:
            raise ValueError("model must not be empty")

        super().__init__(
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
        )
        self.name = name
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = client
        self._owns_client = client is None

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
            self._owns_client = True
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict:
        return {
            "model": self.model,
            "messages": [message.model_dump(mode="json") for message in messages],
            "temperature": (
                self.default_temperature if temperature is None else temperature
            ),
            "max_tokens": (
                self.default_max_tokens if max_tokens is None else max_tokens
            ),
            "stream": False,
        }

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not messages:
            raise ValueError("at least one message is required")

        payload = self._payload(messages, temperature, max_tokens)
        last_error: LLMProviderError | None = None
        attempts = self._max_retries + 1

        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                response = await self._get_client().post(
                    self.endpoint,
                    json=payload,
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                latency_ms = (time.perf_counter() - started) * 1000.0

                if response.status_code >= 400:
                    detail = response.text[:500].replace("\n", " ")
                    error = LLMProviderError(
                        f"{self.name} request failed with HTTP "
                        f"{response.status_code}: {detail}",
                        provider=self.name,
                        status_code=response.status_code,
                    )
                    if (
                        response.status_code not in self.RETRYABLE_STATUS_CODES
                        or attempt >= attempts - 1
                    ):
                        raise error
                    last_error = error
                    await self._sleep_before_retry(attempt, purpose)
                    continue

                return self._parse_response(response, latency_ms, purpose)

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                latency_ms = (time.perf_counter() - started) * 1000.0
                last_error = LLMProviderError(
                    f"{self.name} transport error after {latency_ms:.0f} ms: "
                    f"{type(exc).__name__}: {exc}",
                    provider=self.name,
                )
                if attempt >= attempts - 1:
                    raise last_error from exc
                await self._sleep_before_retry(attempt, purpose)

        # Defensive: the loop either returns or raises.
        raise last_error or LLMProviderError(
            f"{self.name} request failed without a response", provider=self.name
        )

    async def _sleep_before_retry(self, attempt: int, purpose: str | None) -> None:
        delay = min(0.5 * (2**attempt), 4.0)
        logger.warning(
            "%s call failed (purpose=%s); retrying in %.2fs",
            self.name,
            purpose or "unspecified",
            delay,
        )
        await asyncio.sleep(delay)

    def _parse_response(
        self,
        response: httpx.Response,
        latency_ms: float,
        purpose: str | None,
    ) -> LLMResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMProviderError(
                f"{self.name} returned non-JSON content",
                provider=self.name,
                status_code=response.status_code,
            ) from exc

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"{self.name} returned an unexpected response shape "
                f"(purpose={purpose or 'unspecified'})",
                provider=self.name,
                status_code=response.status_code,
            ) from exc

        usage_data = data.get("usage") or {}
        prompt_tokens = int(usage_data.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage_data.get("completion_tokens", 0) or 0)
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(
                usage_data.get("total_tokens", prompt_tokens + completion_tokens) or 0
            ),
        )

        logger.info(
            "%s completion ok (purpose=%s, tokens=%d, latency=%.0fms)",
            self.name,
            purpose or "unspecified",
            usage.total_tokens,
            latency_ms,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=str(data.get("model") or self.model),
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason"),
            raw={"id": data.get("id"), "object": data.get("object")},
        )

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
