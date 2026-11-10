"""Provider HTTP transport lifecycle, connection reuse and error classification."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from agentforge.config.settings import Settings
from agentforge.llm.base import ChatMessage, LLMProviderError
from agentforge.llm.hkgai import HKGAIProvider
from agentforge.llm.openai_compatible import OpenAICompatibleProvider

_OK = json.dumps(
    {
        "id": "x",
        "model": "m",
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }
).encode()


class _KeepAliveHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ports: list[int] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        type(self).ports.append(self.client_address[1])
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_OK)))
        self.end_headers()
        self.wfile.write(_OK)

    def log_message(self, *args) -> None:
        return


def _local_server(handler_cls) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _local_provider(port: int, client: httpx.AsyncClient) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test",
        api_key="test-key",
        base_url=f"http://127.0.0.1:{port}/v1",
        model="m",
        max_retries=0,
        timeout=10.0,
        client=client,
    )


def test_provider_reuses_one_client_and_aclose_recreates(tmp_path):
    provider = HKGAIProvider(
        api_key="test-key", base_url="https://example.invalid/v1", model="m"
    )
    first = provider._get_client()
    assert provider._get_client() is first
    asyncio.run(provider.aclose())
    assert first.is_closed is True
    second = provider._get_client()
    assert second is not first
    assert second.is_closed is False
    asyncio.run(provider.aclose())


def test_default_keep_alive_reuses_the_same_tcp_connection():
    handler_cls = type("_H", (_KeepAliveHandler,), {"ports": []})
    server = _local_server(handler_cls)
    port = server.server_address[1]
    try:
        client = httpx.AsyncClient(timeout=10.0, trust_env=False)
        provider = _local_provider(port, client)

        async def run() -> None:
            await provider.complete([ChatMessage.user("hi")], purpose="t")
            await provider.complete([ChatMessage.user("hi")], purpose="t")
            await provider.aclose()

        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
    assert len(handler_cls.ports) == 2
    assert handler_cls.ports[0] == handler_cls.ports[1], (
        "default httpx keep-alive must reuse the pooled connection"
    )


def test_connection_close_header_forces_a_new_tcp_connection():
    handler_cls = type("_H2", (_KeepAliveHandler,), {"ports": []})
    server = _local_server(handler_cls)
    port = server.server_address[1]
    try:
        client = httpx.AsyncClient(
            timeout=10.0, trust_env=False, headers={"Connection": "close"}
        )
        provider = _local_provider(port, client)

        async def run() -> None:
            await provider.complete([ChatMessage.user("hi")], purpose="t")
            await provider.complete([ChatMessage.user("hi")], purpose="t")
            await provider.aclose()

        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
    assert len(handler_cls.ports) == 2
    assert handler_cls.ports[0] != handler_cls.ports[1], (
        "Connection: close must open a fresh TCP connection per request"
    )


def _provider_with_transport(handler) -> HKGAIProvider:
    transport = httpx.MockTransport(handler)
    return HKGAIProvider(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="m",
        max_retries=0,
        timeout=5.0,
        client=httpx.AsyncClient(transport=transport, timeout=5.0, trust_env=False),
    )


def test_error_classification_no_response_disconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    provider = _provider_with_transport(handler)
    with pytest.raises(LLMProviderError) as excinfo:
        asyncio.run(provider.complete([ChatMessage.user("hi")]))
    assert excinfo.value.error_class == "no_response_disconnect"
    assert excinfo.value.status_code is None
    asyncio.run(provider.aclose())


def test_error_classification_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    provider = _provider_with_transport(handler)
    with pytest.raises(LLMProviderError) as excinfo:
        asyncio.run(provider.complete([ChatMessage.user("hi")]))
    assert excinfo.value.error_class == "timeout"
    asyncio.run(provider.aclose())


def test_error_classification_http_status_and_parse():
    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    provider = _provider_with_transport(server_error)
    with pytest.raises(LLMProviderError) as excinfo:
        asyncio.run(provider.complete([ChatMessage.user("hi")]))
    assert excinfo.value.error_class == "http_server_error"
    assert excinfo.value.status_code == 503
    asyncio.run(provider.aclose())

    def client_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    provider = _provider_with_transport(client_error)
    with pytest.raises(LLMProviderError) as excinfo:
        asyncio.run(provider.complete([ChatMessage.user("hi")]))
    assert excinfo.value.error_class == "http_client_error"
    asyncio.run(provider.aclose())

    def bad_body(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    provider = _provider_with_transport(bad_body)
    with pytest.raises(LLMProviderError) as excinfo:
        asyncio.run(provider.complete([ChatMessage.user("hi")]))
    assert excinfo.value.error_class == "parse_error"
    asyncio.run(provider.aclose())


def test_http_trust_env_setting_reaches_the_client():
    assert Settings().http_trust_env is True
    provider = HKGAIProvider(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="m",
        trust_env=False,
    )
    assert provider.trust_env is False
    client = provider._get_client()
    assert client.trust_env is False
    assert provider._get_client() is client
    asyncio.run(provider.aclose())
    assert client.is_closed is True
