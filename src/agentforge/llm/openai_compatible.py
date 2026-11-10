"""OpenAI-compatible chat-completions provider with retry and timeouts.

Only DeepSeek and HKGAI endpoints are used by this project. The protocol is
OpenAI-compatible, but no OpenAI SDK or OpenAI API endpoint is used.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Sequence

import httpx

from agentforge.llm.base import (
    ERROR_CLASS_HTTP_CLIENT,
    ERROR_CLASS_HTTP_SERVER,
    ERROR_CLASS_PARSE,
    BaseLLMProvider,
    ChatMessage,
    LLMProviderError,
    LLMResponse,
    TokenUsage,
    classify_transport_error,
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
        stream: bool = False,
        trust_env: bool = True,
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
        self.stream = stream
        self.trust_env = trust_env

    @property
    def endpoint(self) -> str:
        if self._base_url.endswith("/chat/completions"):
            return self._base_url
        return f"{self._base_url}/chat/completions"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                trust_env=self.trust_env,
            )
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
            "stream": self.stream,
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
        if self.stream:
            return await self._complete_streaming(
                payload, purpose=purpose, started_at=time.perf_counter()
            )
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
                    detail = detail.replace(self._api_key, "[REDACTED]")
                    error = LLMProviderError(
                        f"{self.name} request failed with HTTP "
                        f"{response.status_code}: {detail}",
                        provider=self.name,
                        status_code=response.status_code,
                        error_class=(
                            ERROR_CLASS_HTTP_CLIENT
                            if response.status_code < 500
                            else ERROR_CLASS_HTTP_SERVER
                        ),
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
                    error_class=classify_transport_error(exc),
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

    async def _complete_streaming(
        self,
        payload: dict,
        *,
        purpose: str | None,
        started_at: float,
    ) -> LLMResponse:
        """Consume Chat Completions SSE with bounded retries.

        Partial content is never returned as a successful response. If the
        connection dies after content arrives, the raised error carries
        diagnostic metrics and the caller cannot mistake it for a valid action.
        """
        last_error: LLMProviderError | None = None
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            attempt_started = time.perf_counter()
            metrics = {
                "stream": True,
                "http_status": None,
                "headers_ms": None,
                "first_byte_ms": None,
                "first_event_ms": None,
                "first_content_ms": None,
                "events": 0,
                "chunks": 0,
                "reasoning_chars": 0,
                "terminal_event": False,
                "terminal_event_missing": False,
                "content_chars": 0,
                "elapsed_ms": None,
            }
            content_parts: list[str] = []
            finish_reason: str | None = None
            response_model = self.model
            usage_data: dict | None = None
            done = False
            first_byte = True
            first_event = True
            first_content = True
            try:
                async with self._get_client().stream(
                    "POST",
                    self.endpoint,
                    json=payload,
                    headers=self._headers(),
                    timeout=self._timeout,
                ) as response:
                    metrics["http_status"] = response.status_code
                    metrics["headers_ms"] = round(
                        (time.perf_counter() - attempt_started) * 1000.0, 3
                    )
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = body.decode("utf-8", errors="replace")[:500]
                        detail = detail.replace("\n", " ").replace(
                            self._api_key, "[REDACTED]"
                        )
                        error = LLMProviderError(
                            f"{self.name} streaming request failed with HTTP "
                            f"{response.status_code}: {detail}",
                            provider=self.name,
                            status_code=response.status_code,
                            details={"stream_metrics": metrics},
                            error_class=(
                                ERROR_CLASS_HTTP_CLIENT
                                if response.status_code < 500
                                else ERROR_CLASS_HTTP_SERVER
                            ),
                        )
                        if (
                            response.status_code not in self.RETRYABLE_STATUS_CODES
                            or attempt >= attempts - 1
                        ):
                            raise error
                        last_error = error
                        await self._sleep_before_retry(attempt, purpose)
                        continue

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        now_ms = (
                            time.perf_counter() - attempt_started
                        ) * 1000.0
                        if first_byte:
                            metrics["first_byte_ms"] = round(now_ms, 3)
                            first_byte = False
                        stripped = line.strip()
                        if not stripped.startswith("data:"):
                            continue
                        if first_event:
                            metrics["first_event_ms"] = round(now_ms, 3)
                            first_event = False
                        metrics["events"] += 1
                        data = stripped[5:].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            done = True
                            metrics["terminal_event"] = True
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise LLMProviderError(
                                f"{self.name} returned malformed SSE JSON",
                                provider=self.name,
                                status_code=response.status_code,
                                details={"stream_metrics": metrics},
                                error_class=ERROR_CLASS_PARSE,
                            ) from exc
                        if not isinstance(chunk, dict):
                            raise LLMProviderError(
                                f"{self.name} returned a non-object SSE chunk",
                                provider=self.name,
                                status_code=response.status_code,
                                details={"stream_metrics": metrics},
                                error_class=ERROR_CLASS_PARSE,
                            )
                        if isinstance(chunk.get("model"), str) and chunk["model"]:
                            response_model = chunk["model"]
                        if isinstance(chunk.get("usage"), dict):
                            usage_data = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta") or {}
                            if not isinstance(delta, dict):
                                delta = {}
                            text = self._content_text(delta.get("content"))
                            if text:
                                if first_content:
                                    metrics["first_content_ms"] = round(
                                        now_ms, 3
                                    )
                                    first_content = False
                                content_parts.append(text)
                                metrics["chunks"] += 1
                            reasoning = delta.get("reasoning_content")
                            if reasoning is None:
                                reasoning = delta.get("reasoning")
                            if isinstance(reasoning, str):
                                metrics["reasoning_chars"] += len(reasoning)
                            if choice.get("finish_reason"):
                                finish_reason = str(choice["finish_reason"])

                elapsed_ms = round(
                    (time.perf_counter() - attempt_started) * 1000.0, 3
                )
                metrics["elapsed_ms"] = elapsed_ms
                content = "".join(content_parts)
                metrics["content_chars"] = len(content)
                if not content:
                    raise LLMProviderError(
                        f"{self.name} streaming response had no content "
                        f"(reasoning_chars={metrics['reasoning_chars']})",
                        provider=self.name,
                        details={"stream_metrics": metrics},
                        error_class=ERROR_CLASS_PARSE,
                    )
                if not done and finish_reason is None:
                    raise LLMProviderError(
                        f"{self.name} stream ended before a terminal event",
                        provider=self.name,
                        details={"stream_metrics": metrics},
                        error_class=ERROR_CLASS_PARSE,
                    )
                if not done and finish_reason is not None:
                    metrics["terminal_event_missing"] = True
                usage = self._usage_from(usage_data)
                logger.info(
                    "%s streaming completion ok (purpose=%s, tokens=%d, "
                    "events=%d, chunks=%d, elapsed=%.0fms)",
                    self.name,
                    purpose or "unspecified",
                    usage.total_tokens,
                    metrics["events"],
                    metrics["chunks"],
                    elapsed_ms,
                )
                return LLMResponse(
                    content=content,
                    provider=self.name,
                    model=response_model,
                    usage=usage,
                    latency_ms=elapsed_ms,
                    finish_reason=finish_reason,
                    raw={
                        "stream_metrics": metrics,
                        "usage_reported": usage_data is not None,
                    },
                )
            except LLMProviderError as exc:
                if exc.details.get("stream_metrics") is None:
                    exc.details["stream_metrics"] = metrics
                if content_parts:
                    raise
                if attempt >= attempts - 1:
                    raise
                last_error = exc
                await self._sleep_before_retry(attempt, purpose)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                elapsed_ms = round(
                    (time.perf_counter() - attempt_started) * 1000.0, 3
                )
                metrics["elapsed_ms"] = elapsed_ms
                error = LLMProviderError(
                    f"{self.name} streaming transport error after "
                    f"{elapsed_ms:.0f} ms: {type(exc).__name__}: {exc}",
                    provider=self.name,
                    details={"stream_metrics": metrics},
                    error_class=classify_transport_error(exc),
                )
                if content_parts:
                    raise error from exc
                if attempt >= attempts - 1:
                    raise error from exc
                last_error = error
                await self._sleep_before_retry(attempt, purpose)

        raise last_error or LLMProviderError(
            f"{self.name} streaming request failed without a response",
            provider=self.name,
        )

    @staticmethod
    def _content_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(
                str(part.get("text", ""))
                if isinstance(part, dict)
                else str(part)
                for part in value
            )
        return str(value)

    @staticmethod
    def _usage_from(usage_data: dict | None) -> TokenUsage:
        usage_data = usage_data or {}
        prompt_tokens = int(usage_data.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage_data.get("completion_tokens", 0) or 0)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(
                usage_data.get(
                    "total_tokens", prompt_tokens + completion_tokens
                )
                or 0
            ),
        )

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
                error_class=ERROR_CLASS_PARSE,
            ) from exc

        try:
            choice = data["choices"][0]
            raw_content = choice["message"]["content"]
            if raw_content is None:
                content = ""
            elif isinstance(raw_content, str):
                content = raw_content
            elif isinstance(raw_content, list):
                # Some compatible providers return content parts.
                content = "".join(
                    str(part.get("text", ""))
                    if isinstance(part, dict)
                    else str(part)
                    for part in raw_content
                )
            else:
                content = str(raw_content)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"{self.name} returned an unexpected response shape "
                f"(purpose={purpose or 'unspecified'})",
                provider=self.name,
                status_code=response.status_code,
                error_class=ERROR_CLASS_PARSE,
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
            raw={
                "id": data.get("id"),
                "object": data.get("object"),
                "http_status": response.status_code,
            },
        )

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
