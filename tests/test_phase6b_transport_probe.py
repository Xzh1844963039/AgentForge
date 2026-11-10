"""Offline tests for the HKGAI HTTP transport probe (no real API calls)."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from agentforge.benchmark.hkgai_transport_probe import (
    MAX_REQUESTS,
    MAX_TOKENS_PER_REQUEST,
    run_hkgai_transport_probe,
)
from agentforge.config.settings import Settings
from agentforge.llm.hkgai import HKGAIProvider


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        llm_provider="hkgai",
        hkgai_api_key="test-key-not-real",
        hkgai_base_url="https://example.invalid/v1",
        hkgai_model="test-model",
        memory_evolution_enabled=False,
    )


def _provider() -> HKGAIProvider:
    return HKGAIProvider(
        api_key="test-key-not-real",
        base_url="https://example.invalid/v1",
        model="test-model",
        max_retries=0,
        timeout=30.0,
    )


def _ok_body() -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "model": "test-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 3,
                "total_tokens": 14,
            },
        }
    ).encode()


def test_probe_rebuilds_real_compact_action_prompt(tmp_path):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "connection": request.headers.get("connection"),
                "has_auth": "authorization" in request.headers,
            }
        )
        return httpx.Response(200, content=_ok_body())

    transport = httpx.MockTransport(handler)
    clients: list[httpx.AsyncClient] = []

    def factory() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=transport, timeout=10.0)
        clients.append(client)
        return client

    result = asyncio.run(
        run_hkgai_transport_probe(
            _settings(tmp_path),
            allow_live_api=False,
            output_dir=tmp_path / "runs",
            provider_override=_provider(),
            client_factory=factory,
        )
    )
    assert result.mode == "OFFLINE_TEST"
    assert result.outcome == "A_both_succeeded"
    assert result.status == "PASSED"
    assert result.total_requests == MAX_REQUESTS
    assert result.prompt_uses_compact_action is True
    assert result.prompt_includes_tools is True
    assert 600 < result.prompt_chars < 1600
    assert all(call.request_connection_header == "close" for call in result.calls)
    assert all(call.client_closed for call in result.calls)
    assert len({call.client_instance for call in result.calls}) == 2
    assert all(call.prompt_tokens == 11 for call in result.calls)
    assert all(call.total_tokens == 14 for call in result.calls)
    assert all(seen_item["connection"] == "close" for seen_item in seen)
    assert all(seen_item["has_auth"] is True for seen_item in seen)
    dumped = json.dumps(result.model_dump(mode="json")).lower()
    assert "authorization" not in dumped
    assert "test-key-not-real" not in dumped


def test_probe_stops_after_first_no_response_disconnect(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    transport = httpx.MockTransport(handler)
    result = asyncio.run(
        run_hkgai_transport_probe(
            _settings(tmp_path),
            allow_live_api=False,
            output_dir=tmp_path / "runs",
            provider_override=_provider(),
            client_factory=lambda: httpx.AsyncClient(transport=transport, timeout=10.0),
        )
    )
    assert calls["count"] == 1, "no request may be sent after a disconnect"
    assert result.total_requests == 1
    assert result.stopped_after_first_failure is True
    assert result.outcome == "C_first_failed"
    assert result.status == "FAILED"
    assert result.calls[0].error_class == "no_response_disconnect"


def test_probe_outcome_b_when_second_request_fails(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 2:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, content=_ok_body())

    transport = httpx.MockTransport(handler)
    result = asyncio.run(
        run_hkgai_transport_probe(
            _settings(tmp_path),
            allow_live_api=False,
            output_dir=tmp_path / "runs",
            provider_override=_provider(),
            client_factory=lambda: httpx.AsyncClient(transport=transport, timeout=10.0),
        )
    )
    assert calls["count"] == 2
    assert result.total_requests == 2
    assert result.outcome == "B_first_ok_second_failed"
    assert result.status == "FAILED"
    assert result.calls[0].usable is True
    assert result.calls[1].usable is False


def test_probe_requires_live_authorization_and_key(tmp_path):
    with pytest.raises(PermissionError):
        asyncio.run(
            run_hkgai_transport_probe(
                _settings(tmp_path), allow_live_api=False, output_dir=tmp_path
            )
        )
    no_key = Settings(
        data_dir=tmp_path,
        llm_provider="hkgai",
        hkgai_api_key="",
        hkgai_base_url="https://example.invalid/v1",
        hkgai_model="test-model",
    )
    with pytest.raises(PermissionError):
        asyncio.run(
            run_hkgai_transport_probe(
                no_key, allow_live_api=True, output_dir=tmp_path
            )
        )


class _LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen_connection: str | None = None
    count = 0

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        type(self).seen_connection = self.headers.get("Connection")
        type(self).count += 1
        payload = _ok_body()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # silence test output
        return


def test_probe_uses_distinct_tcp_connections_on_local_server(tmp_path):
    handler_cls = type("_Handler", (_LocalHandler,), {"count": 0, "seen_connection": None})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = HKGAIProvider(
            api_key="test-key-not-real",
            base_url=f"http://127.0.0.1:{port}/v1",
            model="test-model",
            max_retries=0,
            timeout=30.0,
        )
        result = asyncio.run(
            run_hkgai_transport_probe(
                _settings(tmp_path).model_copy(
                    update={"hkgai_base_url": f"http://127.0.0.1:{port}/v1"}
                ),
                allow_live_api=False,
                output_dir=tmp_path / "runs",
                provider_override=provider,
                # trust_env=False keeps the local test off the machine's
                # registry-configured HTTP proxy.
                client_factory=lambda: httpx.AsyncClient(
                    timeout=30.0, trust_env=False
                ),
            )
        )
    finally:
        server.shutdown()
        server.server_close()
    assert handler_cls.count == 2
    assert handler_cls.seen_connection.lower() == "close"
    assert result.total_requests == 2
    assert result.outcome == "A_both_succeeded"
    ports = {call.local_client_addr for call in result.calls}
    assert len(ports) == 2, f"expected two distinct TCP connections, got {ports}"
    assert result.calls[0].connection_reused is None
    assert result.calls[1].connection_reused is False
    assert all(call.client_closed for call in result.calls)
    assert all(call.pool_connections_at_close == 0 for call in result.calls)


def test_probe_rejects_more_than_two_requests_by_design(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_ok_body())
    )
    result = asyncio.run(
        run_hkgai_transport_probe(
            _settings(tmp_path),
            allow_live_api=False,
            output_dir=tmp_path / "runs",
            provider_override=_provider(),
            client_factory=lambda: httpx.AsyncClient(transport=transport, timeout=10.0),
        )
    )
    assert result.total_requests <= MAX_REQUESTS == 2
    assert all(
        call.requested_max_tokens <= MAX_TOKENS_PER_REQUEST for call in result.calls
    )
