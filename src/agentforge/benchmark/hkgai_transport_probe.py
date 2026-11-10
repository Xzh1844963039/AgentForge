"""Two-request HKGAI HTTP transport diagnostic probe.

Tests whether a *fresh* ``httpx.AsyncClient`` per request plus an explicit
``Connection: close`` removes the ~56-64s pre-response disconnect that the
runtime hits on the second request of a session.

Design constraints (fixed by the authorization for this experiment):

- exact same HKGAI provider/model/base URL as the runtime;
- the real (previously failing) compact Agent Action prompt, rebuilt through the
  real runtime message builder;
- non-streaming payload, ``retries=0``, timeout <= 120s, ``max_tokens`` <= 1024;
- no tool execution and no workspace/Git side effects;
- a brand-new client per request, ``Connection: close``, client and response
  closed after every call;
- requests are strictly sequential; the second request only runs after the first
  one returned a usable response;
- if a request ends without a response (disconnect/timeout), the probe stops and
  never sends another request.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx
from pydantic import BaseModel, Field

from agentforge.agents.base import COMPACT_ACTION_PROTOCOL
from agentforge.benchmark.hkgai_stage2 import STAGE2_GOAL
from agentforge.config.settings import Settings
from agentforge.llm.base import BaseLLMProvider, ChatMessage, LLMProviderError
from agentforge.llm.factory import build_llm_provider
from agentforge.models.common import utc_now
from agentforge.models.task import Task
from agentforge.runtime import AgentForgeRuntime

MAX_REQUESTS = 2
MAX_TOKENS_PER_REQUEST = 1024
MAX_TIMEOUT_SECONDS = 120.0

_PROBE_TASK_DESCRIPTION = (
    'Execute Python code in the sandbox that prints "STAGE2_SUM=5" and report '
    "the recorded stdout and exit code."
)
_RESPONSE_HEADER_WHITELIST = (
    "content-type",
    "content-length",
    "connection",
    "server",
    "date",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "retry-after",
)


class TransportCall(BaseModel):
    index: int
    request_id: str
    purpose: str = "transport_probe:coding"
    workflow_id: str = "transport_probe"
    agent_id: str = "coding"
    provider_instance: str = ""
    provider_name: str = ""
    provider_model: str = ""
    client_instance: str = ""
    client_fresh: bool = True
    client_trust_env: bool = True
    client_class: str = ""
    proxy_url: str | None = None
    proxy_transport: str | None = None
    request_connection_header: str = "close"
    endpoint_host: str = ""
    prompt_chars: int = 0
    prompt_sha256: str = ""
    requested_max_tokens: int = 0
    started_at: datetime = Field(default_factory=utc_now)
    headers_arrived_at: datetime | None = None
    headers_ms: float | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float = 0.0
    http_status: int | None = None
    http_version: str | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_connection: str | None = None
    local_client_addr: str | None = None
    remote_addr: str | None = None
    connection_reused: bool | None = None
    pool_connections_at_close: int | None = None
    client_closed: bool = False
    content_chars: int = 0
    content_preview: str = ""
    finish_reason: str | None = None
    response_model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usable: bool = False
    status: str = "pending"
    error_type: str | None = None
    error_class: str | None = None
    error_detail: str | None = None


class TransportProbeResult(BaseModel):
    status: str = "NOT_VERIFIED"
    outcome: str = "NOT_RUN"
    mode: str = "LIVE"
    configured_model: str = "unknown"
    endpoint_host: str = ""
    provider_instance: str = ""
    proxy_url: str | None = None
    proxy_transport: str | None = None
    trust_env: bool = True
    prompt_chars: int = 0
    prompt_sha256: str = ""
    prompt_preview: str = ""
    prompt_uses_compact_action: bool = False
    prompt_includes_tools: bool = False
    calls: list[TransportCall] = Field(default_factory=list)
    total_requests: int = 0
    total_tokens: int = 0
    stopped_after_first_failure: bool = False
    interpretation: str = ""
    output_dir: str = ""
    checked_at: datetime = Field(default_factory=utc_now)


def _sanitize(text: str, secret: str | None) -> str:
    if secret and secret in text:
        text = text.replace(secret, "[REDACTED]")
    return text


def _proxy_url(client: httpx.AsyncClient, url: str) -> str | None:
    """Return the proxy URL that httpx resolved for this request, sanitized."""
    try:
        mapping = client._get_proxy_map(None, client.trust_env)
    except Exception:
        return None
    key = "https://" if url.startswith("https://") else "http://"
    proxy = mapping.get(key)
    if proxy is None:
        return None
    try:
        return str(proxy.url.copy_with(username=None, password=None))
    except Exception:
        return None


def _proxy_transport(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        request = httpx.Request("POST", url)
        return type(client._transport_for_url(request)).__name__
    except Exception:
        return None


def _address(network_stream: Any | None, key: str) -> str | None:
    if network_stream is None:
        return None
    try:
        value = network_stream.get_extra_info(key)
    except Exception:
        return None
    if value is None:
        return None
    if isinstance(value, tuple):
        return ":".join(str(part) for part in value)
    return str(value)


def _classify_exception(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(
        exc,
        (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
        ),
    ):
        return "no_response_disconnect"
    if isinstance(exc, httpx.TransportError):
        return "transport"
    return "unknown"


def _request_headers(provider: Any) -> dict[str, str]:
    """Provider auth headers plus an explicit Connection: close.

    The Authorization value is never copied into probe records; only the
    explicit ``Connection`` header is logged.
    """
    headers = dict(provider._headers())
    headers["Connection"] = "close"
    return headers


async def _probe_once(
    provider: Any,
    messages: Sequence[ChatMessage],
    index: int,
    *,
    timeout: float,
    max_tokens: int,
    client_factory: Callable[[], httpx.AsyncClient],
    secret: str | None,
    previous_client_addr: str | None,
) -> TransportCall:
    url = provider.endpoint
    payload = provider._payload(messages, None, max_tokens)
    prompt_text = "\n\n".join(message.content for message in messages)
    call = TransportCall(
        index=index,
        request_id=f"transport-{index}-{int(time.time() * 1000)}",
        provider_instance=f"provider@{id(provider)}",
        provider_name=provider.name,
        provider_model=provider.model,
        endpoint_host=httpx.URL(url).host or "",
        prompt_chars=len(prompt_text),
        prompt_sha256=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16],
        requested_max_tokens=max_tokens,
    )
    client = client_factory()
    call.client_instance = f"client@{id(client)}"
    call.client_class = type(client).__name__
    call.client_trust_env = client.trust_env
    call.proxy_url = _proxy_url(client, url)
    call.proxy_transport = _proxy_transport(client, url)
    call.started_at = utc_now()
    started = time.monotonic()
    try:
        async with client.stream(
            "POST",
            url,
            json=payload,
            headers=_request_headers(provider),
            timeout=timeout,
        ) as response:
            headers_at = time.monotonic()
            call.headers_arrived_at = utc_now()
            call.headers_ms = round((headers_at - started) * 1000.0, 3)
            call.http_status = response.status_code
            call.http_version = response.http_version
            call.response_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() in _RESPONSE_HEADER_WHITELIST
            }
            call.response_connection = response.headers.get("connection")
            stream = response.extensions.get("network_stream")
            call.local_client_addr = _address(stream, "client_addr")
            call.remote_addr = _address(stream, "server_addr")
            if call.local_client_addr and previous_client_addr:
                call.connection_reused = (
                    call.local_client_addr == previous_client_addr
                )
            body = await response.aread()
            latency_ms = (time.monotonic() - started) * 1000.0
            if response.status_code < 400:
                parsed = provider._parse_response(
                    response, latency_ms, call.purpose
                )
                call.content_chars = len(parsed.content)
                call.content_preview = parsed.content[:300]
                call.finish_reason = parsed.finish_reason
                call.response_model = parsed.model
                call.prompt_tokens = parsed.usage.prompt_tokens
                call.completion_tokens = parsed.usage.completion_tokens
                call.total_tokens = parsed.usage.total_tokens or (
                    parsed.usage.prompt_tokens + parsed.usage.completion_tokens
                )
                call.usable = bool(parsed.content.strip())
                call.status = "success" if call.usable else "empty_content"
            else:
                detail = body.decode("utf-8", errors="replace")[:500]
                call.status = "http_error"
                call.error_type = "HTTPStatusError"
                call.error_class = (
                    "http_client_error"
                    if response.status_code < 500
                    else "http_server_error"
                )
                call.error_detail = _sanitize(detail.replace("\n", " "), secret)
    except Exception as exc:
        call.status = "failed"
        call.error_type = type(exc).__name__
        call.error_class = (
            exc.error_class
            if isinstance(exc, LLMProviderError)
            else _classify_exception(exc)
        )
        call.error_detail = _sanitize(str(exc)[:1000], secret)
        if isinstance(exc, LLMProviderError):
            call.http_status = exc.status_code
    finally:
        transport = getattr(client, "_transport", None)
        pool = getattr(transport, "_pool", None)
        connections = getattr(pool, "_connections", None)
        if isinstance(connections, list):
            call.pool_connections_at_close = len(connections)
        await client.aclose()
        call.client_closed = client.is_closed
        call.finished_at = utc_now()
        call.elapsed_seconds = round(time.monotonic() - started, 3)
    return call


def _interpretation(result: TransportProbeResult) -> str:
    if result.outcome == "A_both_succeeded":
        return (
            "Both requests succeeded with a brand-new client and Connection: "
            "close per request: the second-request disconnect is consistent with "
            "HTTP connection reuse (keep-alive) on the shared client/proxy "
            "tunnel, not with model or prompt behavior."
        )
    if result.outcome == "B_first_ok_second_failed":
        return (
            "The first request succeeded and the second still failed even with a "
            "fresh client and Connection: close: TCP/HTTP connection reuse does "
            "not explain the disconnect. Per-key throttling, gateway queueing or "
            "an upstream pre-response timeout must be investigated."
        )
    if result.outcome == "C_first_failed":
        return (
            "The first request already failed without a usable response: the "
            "route is unavailable for this request shape right now; no further "
            "request was sent."
        )
    if result.outcome == "PARTIAL":
        return (
            "Only the first of the two authorized requests was spent and it "
            "succeeded; inspect stop conditions before continuing."
        )
    return "No live requests were made."


async def run_hkgai_transport_probe(
    settings: Settings,
    *,
    allow_live_api: bool,
    output_dir: str | Path = "benchmarks/runs",
    timeout: float = 120.0,
    max_tokens: int = MAX_TOKENS_PER_REQUEST,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
    provider_override: BaseLLMProvider | None = None,
    trust_env: bool | None = None,
) -> TransportProbeResult:
    """Run the bounded two-request fresh-connection experiment."""
    live = provider_override is None and client_factory is None
    if client_factory is None and provider_override is None:
        if not allow_live_api:
            raise PermissionError(
                "Transport probe requires explicit --allow-live-api"
            )
        if (
            settings.hkgai_api_key is None
            or not settings.hkgai_api_key.get_secret_value().strip()
        ):
            raise PermissionError("HKGAI API key is not configured")
        if not settings.hkgai_base_url or not settings.hkgai_model:
            raise PermissionError("HKGAI base URL/model are not configured")

    effective_timeout = min(float(timeout), MAX_TIMEOUT_SECONDS)
    effective_max_tokens = min(int(max_tokens), MAX_TOKENS_PER_REQUEST)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    root = Path(output_dir) / f"hkgai_transport_{stamp}"
    root.mkdir(parents=True, exist_ok=True)

    probe_settings = settings.model_copy(
        update={
            "llm_provider": "hkgai",
            "hkgai_stream": False,
            "max_retries": 0,
            "request_timeout": effective_timeout,
            "max_tokens": effective_max_tokens,
            "planner_protocol": "compact",
            "agent_protocol": "compact",
            "data_dir": root / "data",
        }
    )
    secret = (
        settings.hkgai_api_key.get_secret_value()
        if settings.hkgai_api_key is not None
        else None
    )

    # Rebuild the real (previously failing) compact Agent Action prompt through
    # the real runtime message builder. No tool runs, no workspace writes.
    builder_settings = probe_settings.model_copy(
        update={"llm_provider": "mock"}
    )
    async with AgentForgeRuntime(
        builder_settings, provider=build_llm_provider(builder_settings)
    ) as builder:
        coding = builder.get_agent("coding")
        task = Task(
            workflow_id="transport_probe",
            description=_PROBE_TASK_DESCRIPTION,
            required_capabilities=["coding"],
            assigned_agent="coding",
            metadata={"kind": "coding"},
        )
        messages = coding._build_task_messages(
            task=task,
            goal=STAGE2_GOAL,
            dependency_results=(),
            delegated_results=(),
            tool_evidence=(),
            memory_hits=(),
        )
    prompt_text = "\n\n".join(message.content for message in messages)

    result = TransportProbeResult(
        mode="LIVE" if live else "OFFLINE_TEST",
        configured_model=settings.hkgai_model or "unknown",
        endpoint_host=httpx.URL(
            settings.hkgai_base_url or "https://invalid"
        ).host or "",
        prompt_chars=len(prompt_text),
        prompt_sha256=hashlib.sha256(
            prompt_text.encode("utf-8")
        ).hexdigest()[:16],
        prompt_preview=prompt_text[:400],
        prompt_uses_compact_action=COMPACT_ACTION_PROTOCOL in prompt_text,
        prompt_includes_tools="[TOOLS]" in prompt_text,
        output_dir=str(root),
    )
    base_provider = provider_override or build_llm_provider(probe_settings)
    result.provider_instance = f"provider@{id(base_provider)}"

    def default_client() -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(effective_timeout)}
        if trust_env is not None:
            kwargs["trust_env"] = trust_env
        return httpx.AsyncClient(**kwargs)

    factory = client_factory or default_client
    result.trust_env = trust_env if trust_env is not None else True
    previous_addr: str | None = None
    try:
        for index in (1, 2):
            call = await _probe_once(
                base_provider,
                messages,
                index,
                timeout=effective_timeout,
                max_tokens=effective_max_tokens,
                client_factory=factory,
                secret=secret,
                previous_client_addr=previous_addr,
            )
            result.calls.append(call)
            result.total_requests += 1
            result.total_tokens += call.total_tokens
            if result.proxy_url is None:
                result.proxy_url = call.proxy_url
                result.proxy_transport = call.proxy_transport
            previous_addr = call.local_client_addr or previous_addr
            if not call.usable:
                result.stopped_after_first_failure = True
                break
    finally:
        await base_provider.aclose()

    usable = [call.usable for call in result.calls]
    if len(usable) == 2 and all(usable):
        result.outcome = "A_both_succeeded"
        result.status = "PASSED"
    elif usable and usable[0]:
        result.outcome = (
            "PARTIAL" if len(usable) == 1 else "B_first_ok_second_failed"
        )
        result.status = "PARTIAL" if len(usable) == 1 else "FAILED"
    else:
        result.outcome = "C_first_failed"
        result.status = "FAILED"
    result.interpretation = _interpretation(result)

    (root / "transport_probe.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_report(root, result)
    return result


def _write_report(root: Path, result: TransportProbeResult) -> None:
    lines = [
        "# HKGAI HTTP Transport Probe (fresh connection per request)",
        "",
        f"- Mode: **{result.mode}**",
        f"- Outcome: **{result.outcome}**",
        f"- Status: **{result.status}**",
        f"- Configured model: `{result.configured_model}`",
        f"- Endpoint host: `{result.endpoint_host}`",
        f"- Proxy: `{result.proxy_url or 'none'}` via "
        f"`{result.proxy_transport or 'n/a'}`",
        f"- Prompt: {result.prompt_chars} chars, sha256[:16] "
        f"`{result.prompt_sha256}`, compact_action="
        f"{result.prompt_uses_compact_action}, tools="
        f"{result.prompt_includes_tools}",
        f"- Requests: {result.total_requests}; tokens: {result.total_tokens}",
        f"- Stopped after first failure: {result.stopped_after_first_failure}",
        "",
        "## Per-request transport evidence",
        "",
        "| # | client | proxy | conn hdr | headers ms | total s | HTTP | "
        "local addr | reused | pool | closed | tokens | status |",
        "|---:|---|---|---|---:|---:|---:|---|---|---|---:|---:|---|",
    ]
    for call in result.calls:
        lines.append(
            f"| {call.index} | `{call.client_instance}` | "
            f"`{call.proxy_url or 'none'}` | {call.request_connection_header} | "
            f"{call.headers_ms} | {call.elapsed_seconds} | {call.http_status} | "
            f"`{call.local_client_addr or 'n/a'}` | {call.connection_reused} | "
            f"{call.pool_connections_at_close} | {call.client_closed} | "
            f"{call.total_tokens} | {call.status} |"
        )
    lines.extend(["", "## Errors", ""])
    any_error = False
    for call in result.calls:
        if call.error_type:
            any_error = True
            lines.append(
                f"- request {call.index}: `{call.error_type}` "
                f"({call.error_class}) after {call.elapsed_seconds}s: "
                f"{call.error_detail}"
            )
    if not any_error:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            result.interpretation,
            "",
            "No Authorization header or API key is recorded anywhere in this "
            "report or its JSON.",
        ]
    )
    (root / "TRANSPORT_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
