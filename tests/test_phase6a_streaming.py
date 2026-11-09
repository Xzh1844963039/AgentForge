"""Offline SSE streaming tests for the OpenAI-compatible provider."""

from __future__ import annotations

import json

import httpx
import pytest

from agentforge.llm.base import ChatMessage, LLMProviderError
from agentforge.llm.deepseek import DeepSeekProvider
from agentforge.llm.hkgai import HKGAIProvider


def _sse(*events: dict | str) -> bytes:
    parts = []
    for event in events:
        value = event if isinstance(event, str) else json.dumps(event)
        parts.append(f"data: {value}\n\n")
    return "".join(parts).encode("utf-8")


def _chunk(content: str | None = None, *, finish: str | None = None) -> dict:
    delta = {}
    if content is not None:
        delta["content"] = content
    return {
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish,
            }
        ]
    }


def _client(body: bytes, captured: dict | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["body"] = json.loads(request.content.decode("utf-8"))
            captured["url"] = str(request.url)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_streaming_concatenates_content_and_usage():
    captured: dict = {}
    body = _sse(
        _chunk("Hello "),
        _chunk("world", finish="stop"),
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
        "[DONE]",
    )
    client = _client(body, captured)
    provider = HKGAIProvider(
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="stream-model",
        client=client,
        stream=True,
    )
    response = await provider.complete(
        [ChatMessage.user("hello")], temperature=0.3, max_tokens=99
    )
    assert response.content == "Hello world"
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 15
    assert response.provider == "hkgai"
    assert response.raw["usage_reported"] is True
    metrics = response.raw["stream_metrics"]
    assert metrics["terminal_event"] is True
    assert metrics["terminal_event_missing"] is False
    assert metrics["chunks"] == 2
    assert metrics["content_chars"] == 11
    assert metrics["first_byte_ms"] is not None
    assert metrics["first_event_ms"] is not None
    assert metrics["first_content_ms"] is not None
    assert metrics["http_status"] == 200
    assert captured["body"]["stream"] is True
    assert captured["body"]["temperature"] == 0.3
    assert captured["body"]["max_tokens"] == 99
    assert captured["body"]["model"] == "stream-model"
    await provider.aclose()


async def test_streaming_reasoning_is_metadata_not_content():
    body = _sse(
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "thinking about the answer",
                        "content": "OK",
                    },
                    "finish_reason": "stop",
                }
            ]
        },
        "[DONE]",
    )
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True
    )
    response = await provider.complete([ChatMessage.user("hi")])
    assert response.content == "OK"
    assert response.raw["stream_metrics"]["reasoning_chars"] == 25
    assert "thinking" not in response.content
    await provider.aclose()


async def test_streaming_content_parts_are_supported():
    body = _sse(
        {
            "choices": [
                {
                    "delta": {
                        "content": [
                            {"type": "text", "text": "Hello "},
                            {"type": "text", "text": "parts"},
                        ]
                    },
                    "finish_reason": "stop",
                }
            ]
        },
        "[DONE]",
    )
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True
    )
    response = await provider.complete([ChatMessage.user("hi")])
    assert response.content == "Hello parts"
    await provider.aclose()


async def test_streaming_missing_usage_is_tolerated():
    body = _sse(_chunk("OK", finish="stop"), "[DONE]")
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True
    )
    response = await provider.complete([ChatMessage.user("hi")])
    assert response.usage.total_tokens == 0
    assert response.raw["usage_reported"] is False
    await provider.aclose()


async def test_streaming_missing_done_but_finish_reason_is_marked():
    body = _sse(_chunk("complete", finish="stop"))
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True
    )
    response = await provider.complete([ChatMessage.user("hi")])
    assert response.content == "complete"
    assert response.raw["stream_metrics"]["terminal_event"] is False
    assert response.raw["stream_metrics"]["terminal_event_missing"] is True
    await provider.aclose()


async def test_streaming_without_terminal_or_finish_raises():
    body = _sse(_chunk("partial"))
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True, max_retries=0
    )
    with pytest.raises(LLMProviderError, match="before a terminal event"):
        await provider.complete([ChatMessage.user("hi")])
    await provider.aclose()


async def test_streaming_empty_raises():
    body = _sse("[DONE]")
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True, max_retries=0
    )
    with pytest.raises(LLMProviderError, match="no content"):
        await provider.complete([ChatMessage.user("hi")])
    await provider.aclose()


async def test_streaming_malformed_sse_json_raises():
    body = b"data: {not-json}\n\ndata: [DONE]\n\n"
    client = _client(body)
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True, max_retries=0
    )
    with pytest.raises(LLMProviderError, match="malformed SSE JSON"):
        await provider.complete([ChatMessage.user("hi")])
    await provider.aclose()


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
        raise httpx.ReadError("simulated mid-stream disconnect")

    async def aclose(self) -> None:
        return None


async def test_partial_stream_is_never_returned_as_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_BrokenStream(),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, stream=True, max_retries=0
    )
    with pytest.raises(LLMProviderError) as raised:
        await provider.complete([ChatMessage.user("hi")])
    assert "partial" not in str(raised.value)
    assert raised.value.details["stream_metrics"]["chunks"] == 1
    await provider.aclose()


async def test_streaming_http_error_is_not_parsed_as_sse():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="sk-test",
        client=client,
        stream=True,
        max_retries=0,
    )
    with pytest.raises(LLMProviderError, match="HTTP 429"):
        await provider.complete([ChatMessage.user("hi")])
    await provider.aclose()
