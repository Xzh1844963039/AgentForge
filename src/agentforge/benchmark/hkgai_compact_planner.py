"""One-request Compact Planner probe for HKGAI Stage 2."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentforge.agents.base import extract_json_payload
from agentforge.agents.coordinator import build_planner_messages
from agentforge.agents.factory import DEFAULT_PROFILES
from agentforge.benchmark.provider import LiveBudgetProvider
from agentforge.config.settings import Settings
from agentforge.llm.base import LLMProviderError
from agentforge.llm.factory import build_llm_provider
from agentforge.models.agent import AgentCard
from agentforge.models.common import utc_now
from agentforge.models.task import ExecutionPlan
from agentforge.orchestration.assignment import CapabilityAssigner
from agentforge.orchestration.planner import validate_plan


class CompactPlannerResult(BaseModel):
    configured_model: str
    response_model: str | None = None
    status: str = "NOT_VERIFIED"
    http_status: int | None = None
    prompt_chars: int = 0
    system_chars: int = 0
    catalog_chars: int = 0
    elapsed_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str | None = None
    content_chars: int = 0
    content_preview: str = ""
    valid_json: bool = False
    plan_valid: bool = False
    capability_valid: bool = False
    subtask_count: int = 0
    error_type: str | None = None
    error_detail: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    output_dir: str = ""
    checked_at: datetime = Field(default_factory=utc_now)


def compact_planner_messages() -> list:
    return build_planner_messages(
        'Create notes/simple.md containing "hello".',
        [AgentCard.from_profile(profile) for profile in DEFAULT_PROFILES],
        3,
        protocol="compact",
    )


def validate_compact_plan(content: str) -> tuple[bool, bool, bool, int]:
    try:
        payload = extract_json_payload(content)
    except Exception:
        return False, False, False, 0
    if not isinstance(payload, dict):
        return False, False, False, 0
    raw_subtasks = payload.get("subtasks")
    raw_count = len(raw_subtasks) if isinstance(raw_subtasks, list) else 0
    try:
        plan = ExecutionPlan.model_validate(payload)
        ordered = validate_plan(plan, max_subtasks=3)
    except Exception:
        return True, False, False, raw_count
    capability_ok = False
    try:
        assigner = CapabilityAssigner(
            [AgentCard.from_profile(profile) for profile in DEFAULT_PROFILES]
        )
        for subtask in ordered:
            assigner.assign(subtask.required_capabilities)
        capability_ok = True
    except Exception:
        capability_ok = False
    return True, len(ordered) == 1, capability_ok, len(ordered)


async def run_hkgai_compact_planner(
    settings: Settings,
    *,
    allow_live_api: bool,
    output_dir: str | Path = "benchmarks/runs",
    timeout: float = 120.0,
) -> CompactPlannerResult:
    if not allow_live_api:
        raise PermissionError(
            "Compact Planner probe requires explicit --allow-live-api"
        )
    if (
        settings.hkgai_api_key is None
        or not settings.hkgai_api_key.get_secret_value().strip()
    ):
        raise PermissionError("HKGAI API key is not configured")
    if not settings.hkgai_base_url or not settings.hkgai_model:
        raise PermissionError("HKGAI base URL/model are not configured")

    messages = compact_planner_messages()
    provider_settings = settings.model_copy(
        update={
            "llm_provider": "hkgai",
            "hkgai_stream": False,
            "max_retries": 0,
            "request_timeout": timeout,
            "max_tokens": 768,
        }
    )
    base = build_llm_provider(provider_settings)
    budget = LiveBudgetProvider(base, max_requests=1, max_tokens=10_000)
    result = CompactPlannerResult(
        configured_model=settings.hkgai_model or "unknown",
        prompt_chars=sum(len(message.content) for message in messages),
        system_chars=len(messages[0].content),
        catalog_chars=len(messages[1].content.split("[AGENTS]\n", 1)[-1].split(
            "\n\n[MAX_SUBTASKS]", 1
        )[0]),
    )
    started = time.monotonic()
    try:
        response = await budget.complete(
            messages,
            purpose="hkgai_compact_planner",
            temperature=0.2,
            max_tokens=768,
        )
        result.http_status = response.raw.get("http_status")
        result.response_model = response.model
        result.finish_reason = response.finish_reason
        result.prompt_tokens = response.usage.prompt_tokens
        result.completion_tokens = response.usage.completion_tokens
        result.total_tokens = response.usage.total_tokens
        result.content_chars = len(response.content)
        result.content_preview = response.content[:1200]
        valid_json, plan_valid, capability_valid, count = validate_compact_plan(
            response.content
        )
        result.valid_json = valid_json
        result.plan_valid = plan_valid
        result.capability_valid = capability_valid
        result.subtask_count = count
        result.status = (
            "PASSED"
            if valid_json and plan_valid and capability_valid
            else "FAILED"
        )
    except Exception as exc:
        result.status = "FAILED"
        result.error_type = type(exc).__name__
        secret = (
            settings.hkgai_api_key.get_secret_value()
            if settings.hkgai_api_key is not None
            else ""
        )
        detail = str(exc)[:1000]
        result.error_detail = (
            detail.replace(secret, "[REDACTED]") if secret else detail
        )
        if isinstance(exc, LLMProviderError):
            result.http_status = exc.status_code
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        result.budget = budget.snapshot()
        await budget.close_underlying()

    root = Path(output_dir) / f"hkgai_compact_planner_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True, exist_ok=True)
    result.output_dir = str(root)
    (root / "compact_planner.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "COMPACT_PLANNER_REPORT.md").write_text(
        "\n".join(
            [
                "# HKGAI Compact Planner Probe",
                "",
                f"- Configured model: `{result.configured_model}`",
                f"- Response model: `{result.response_model or 'n/a'}`",
                f"- Status: **{result.status}**",
                f"- HTTP status: {result.http_status}",
                f"- Prompt chars: {result.prompt_chars} "
                f"(system {result.system_chars}, catalog {result.catalog_chars})",
                f"- Elapsed: {result.elapsed_seconds}s",
                f"- Tokens: {result.prompt_tokens} + {result.completion_tokens} "
                f"= {result.total_tokens}",
                f"- finish_reason: {result.finish_reason}",
                f"- JSON / plan / capability: {result.valid_json} / "
                f"{result.plan_valid} / {result.capability_valid}",
                f"- Subtasks: {result.subtask_count}",
                f"- Error: {result.error_type or 'none'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return result
