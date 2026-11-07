"""DeepSeek provider tests with a mocked HTTP transport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from agentforge.llm.base import ChatMessage, LLMProviderError
from agentforge.llm.deepseek import DeepSeekProvider


def success_payload(content: str = "hello") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


@pytest.mark.asyncio
async def test_complete_parses_response_and_headers():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=success_payload("deepseek says hi"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(api_key="sk-secret", client=client)

    response = await provider.complete(
        [ChatMessage.system("system"), ChatMessage.user("user")],
        purpose="test",
        temperature=0.1,
        max_tokens=128,
    )

    assert response.content == "deepseek says hi"
    assert response.provider == "deepseek"
    assert response.usage.total_tokens == 15
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["auth"] == "Bearer sk-secret"
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["temperature"] == 0.1
    assert captured["body"]["max_tokens"] == 128
    assert captured["body"]["messages"][0]["role"] == "system"

    await provider.aclose()
    assert client.is_closed


@pytest.mark.asyncio
async def test_retry_on_server_error(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(500, text="temporary failure")
        return httpx.Response(200, json=success_payload("recovered"))

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(
        "agentforge.llm.openai_compatible.asyncio.sleep", no_sleep
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(api_key="sk-test", client=client, max_retries=1)

    response = await provider.complete([ChatMessage.user("hi")])
    assert response.content == "recovered"
    assert attempts["count"] == 2
    await provider.aclose()


@pytest.mark.asyncio
async def test_non_retryable_error_raises():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, text="bad key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(api_key="bad", client=client, max_retries=3)

    with pytest.raises(LLMProviderError, match="HTTP 401"):
        await provider.complete([ChatMessage.user("hi")])
    assert attempts["count"] == 1
    await provider.aclose()


@pytest.mark.asyncio
async def test_unexpected_shape_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(api_key="sk", client=client)
    with pytest.raises(LLMProviderError, match="unexpected response shape"):
        await provider.complete([ChatMessage.user("hi")])
    await provider.aclose()


@pytest.mark.asyncio
async def test_empty_messages_rejected():
    provider = DeepSeekProvider(api_key="sk")
    with pytest.raises(ValueError, match="at least one message"):
        await provider.complete([])
    await provider.aclose()
