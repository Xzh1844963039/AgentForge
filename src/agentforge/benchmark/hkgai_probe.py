"""One-request HKGAI streaming probe for the failed Coordinator prompt.

This command is intentionally separate from the Stage ladder. It makes exactly
one request when explicitly authorized, never calls tools and never retries.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentforge.agents.coordinator import PLANNER_PROTOCOL
from agentforge.agents.factory import DEFAULT_PROFILES
from agentforge.benchmark.provider import LiveBudgetProvider
from agentforge.config.settings import Settings
from agentforge.llm.base import ChatMessage, LLMProviderError
from agentforge.llm.factory import build_llm_provider
from agentforge.models.agent import AgentCard
from agentforge.models.common import utc_now


class HKGAIProbeResult(BaseModel):
    provider: str = "hkgai"
    configured_model: str
    response_model: str | None = None
    stream: bool = True
    http_status: int | None = None
    first_byte_ms: float | None = None
    first_event_ms: float | None = None
    first_content_ms: float | None = None
    stream_events: int = 0
    chunks: int = 0
    content_chars: int = 0
    reasoning_chars: int = 0
    finish_reason: str | None = None
    terminal_event: bool = False
    terminal_event_missing: bool = False
    usage_reported: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    complete: bool = False
    error_type: str | None = None
    error_detail: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=utc_now)


def _planner_messages() -> list[ChatMessage]:
    cards = [AgentCard.from_profile(profile) for profile in DEFAULT_PROFILES]
    catalog = "\n".join(
        f"- {card.agent_id} ({card.name}, role={card.role.value}): "
        f"capabilities={', '.join(card.capabilities)}. {card.description}"
        for card in cards
    )
    goal = "Create a short Markdown note and have it reviewed."
    user = (
        f"[GOAL]\n{goal}\n\n"
        f"[AVAILABLE_AGENTS]\n{catalog}\n\n"
        "[MAX_SUBTASKS]\n3\n\n"
        "Return the JSON execution plan now."
    )
    return [
        ChatMessage.system(PLANNER_PROTOCOL),
        ChatMessage.user(user),
    ]


async def run_hkgai_probe(
    settings: Settings,
    *,
    allow_live_api: bool,
    max_tokens: int = 256,
    timeout: float = 120.0,
    output_dir: str | Path = "benchmarks/runs",
) -> HKGAIProbeResult:
    if not allow_live_api:
        raise PermissionError(
            "HKGAI probe requires explicit --allow-live-api authorization"
        )
    if settings.hkgai_api_key is None or not settings.hkgai_api_key.get_secret_value().strip():
        raise PermissionError("HKGAI API key is not configured")
    if not settings.hkgai_base_url or not settings.hkgai_model:
        raise PermissionError("HKGAI base URL/model are not configured")

    probe_settings = settings.model_copy(
        update={
            "llm_provider": "hkgai",
            "hkgai_stream": True,
            "max_retries": 0,
            "request_timeout": timeout,
            "max_tokens": max_tokens,
        }
    )
    base = build_llm_provider(probe_settings)
    budget = LiveBudgetProvider(base, max_requests=1, max_tokens=10_000)
    result = HKGAIProbeResult(configured_model=settings.hkgai_model or "unknown")
    started = time.monotonic()
    try:
        response = await budget.complete(
            _planner_messages(),
            purpose="hkgai_stream_probe",
            temperature=0.2,
            max_tokens=max_tokens,
        )
        result.response_model = response.model
        result.finish_reason = response.finish_reason
        result.usage_reported = bool(
            response.raw.get("usage_reported", False)
        )
        result.prompt_tokens = response.usage.prompt_tokens
        result.completion_tokens = response.usage.completion_tokens
        result.total_tokens = response.usage.total_tokens
        metrics = response.raw.get("stream_metrics") or {}
        result.http_status = metrics.get("http_status")
        result.first_byte_ms = metrics.get("first_byte_ms")
        result.first_event_ms = metrics.get("first_event_ms")
        result.first_content_ms = metrics.get("first_content_ms")
        result.stream_events = int(metrics.get("events", 0) or 0)
        result.chunks = int(metrics.get("chunks", 0) or 0)
        result.content_chars = int(metrics.get("content_chars", 0) or 0)
        result.reasoning_chars = int(metrics.get("reasoning_chars", 0) or 0)
        result.terminal_event = bool(metrics.get("terminal_event", False))
        result.terminal_event_missing = bool(
            metrics.get("terminal_event_missing", False)
        )
        result.complete = bool(response.content.strip())
    except Exception as exc:
        result.error_type = type(exc).__name__
        detail = str(exc)[:1000]
        secret = (
            settings.hkgai_api_key.get_secret_value()
            if settings.hkgai_api_key is not None
            else ""
        )
        result.error_detail = detail.replace(secret, "[REDACTED]") if secret else detail
        if isinstance(exc, LLMProviderError):
            result.http_status = exc.status_code
            metrics = exc.details.get("stream_metrics") or {}
            result.first_byte_ms = metrics.get("first_byte_ms")
            result.first_event_ms = metrics.get("first_event_ms")
            result.first_content_ms = metrics.get("first_content_ms")
            result.stream_events = int(metrics.get("events", 0) or 0)
            result.chunks = int(metrics.get("chunks", 0) or 0)
            result.content_chars = int(metrics.get("content_chars", 0) or 0)
            result.reasoning_chars = int(metrics.get("reasoning_chars", 0) or 0)
            result.terminal_event = bool(metrics.get("terminal_event", False))
            result.terminal_event_missing = bool(
                metrics.get("terminal_event_missing", False)
            )
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        result.budget = budget.snapshot()
        await budget.close_underlying()

    root = Path(output_dir) / f"hkgai_probe_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "probe.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "PROBE_REPORT.md").write_text(
        "\n".join(
            [
                "# HKGAI Streaming Probe",
                "",
                f"- Configured model: `{result.configured_model}`",
                f"- Response model: `{result.response_model or 'n/a'}`",
                f"- Stream: {result.stream}",
                f"- HTTP status: {result.http_status}",
                f"- First byte ms: {result.first_byte_ms}",
                f"- First event ms: {result.first_event_ms}",
                f"- First content ms: {result.first_content_ms}",
                f"- SSE events / content chunks: {result.stream_events} / {result.chunks}",
                f"- Content chars: {result.content_chars}",
                f"- Reasoning chars: {result.reasoning_chars}",
                f"- finish_reason: {result.finish_reason}",
                f"- Terminal event / missing: {result.terminal_event} / {result.terminal_event_missing}",
                f"- Usage reported: {result.usage_reported}",
                f"- Tokens: {result.prompt_tokens} + {result.completion_tokens} = {result.total_tokens}",
                f"- Elapsed: {result.elapsed_seconds}s",
                f"- Complete: {result.complete}",
                f"- Error: {result.error_type or 'none'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return result
