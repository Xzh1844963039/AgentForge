"""Phase 6A provider, structured-output, staged-live and correctness tests."""

from __future__ import annotations

import json

import httpx
import pytest

from agentforge.agents.base import BaseAgent
from agentforge.benchmark.live_memory import LiveMemoryExperimentRunner
from agentforge.benchmark.live_stages import LiveStageRunner
from agentforge.benchmark.metrics import aggregate_runs
from agentforge.benchmark.models import (
    BenchmarkMode,
    BenchmarkRun,
    ProviderKind,
    RunMetrics,
    RunStatus,
    TaskCategory,
    VerificationResult,
    VerificationStatus,
)
from agentforge.benchmark.provider import LiveBudgetProvider, LiveBudgetExceeded
from agentforge.config.settings import Settings
from agentforge.llm.base import (
    BaseLLMProvider,
    ChatMessage,
    LLMProviderError,
    LLMResponse,
    TokenUsage,
)
from agentforge.llm.deepseek import DeepSeekProvider
from agentforge.llm.factory import build_llm_provider
from agentforge.llm.mock import MockProvider
from agentforge.memory import InMemoryMemoryStore
from agentforge.models.agent import AgentProfile, AgentRole
from agentforge.models.task import AgentActionType, Task
from agentforge.tools import ToolRegistry


def _response(content: str, total_tokens: int = 15) -> LLMResponse:
    return LLMResponse(
        content=content,
        provider="mock",
        model="phase6a-test",
        usage=TokenUsage(
            prompt_tokens=10,
            completion_tokens=max(0, total_tokens - 10),
            total_tokens=total_tokens,
        ),
    )


def _agent(provider: BaseLLMProvider, **kwargs) -> BaseAgent:
    profile = AgentProfile(
        agent_id="coding",
        name="CodingAgent",
        role=AgentRole.CODING,
        capabilities=["coding"],
    )
    return BaseAgent(
        profile=profile,
        provider=provider,
        memory=InMemoryMemoryStore(),
        tools=ToolRegistry(),
        **kwargs,
    )


def _task() -> Task:
    return Task(
        workflow_id="wf-phase6a",
        description="Return a structured action.",
        required_capabilities=["coding"],
        assigned_agent="coding",
        metadata={"kind": "test"},
    )


async def test_invalid_action_is_repaired_once_then_succeeds():
    provider = MockProvider(
        scripted_responses=[
            '{"action": "tool_call", "tool_calls": "not-a-list"}',
            json.dumps({"action": "complete", "content": "repaired-ok"}),
        ]
    )
    agent = _agent(provider, max_output_repairs=1)
    result = await agent.execute_task(_task(), goal="structured output")
    assert result.action.action == AgentActionType.COMPLETE
    assert result.action.content == "repaired-ok"
    assert result.repair_count == 1
    assert len(result.calls) == 2
    assert result.calls[0].parse_error is not None
    assert "REPAIR" in provider.calls[1]["messages"][-1]["content"]


async def test_invalid_action_exhaustion_fails_and_is_audited():
    provider = MockProvider(
        scripted_responses=[
            '{"action": "tool_call", "tool_calls": "bad"}',
            '{"action": "tool_call", "tool_calls": "still-bad"}',
        ]
    )
    agent = _agent(provider, max_output_repairs=1)
    result = await agent.execute_task(_task(), goal="structured output")
    assert result.action.action == AgentActionType.FAIL
    assert result.repair_count == 1
    assert "invalid structured action" in (result.action.error or "")
    assert len(result.calls) == 2
    assert all(call.parse_error for call in result.calls)


async def test_plain_text_is_not_silently_accepted_in_strict_mode():
    provider = MockProvider(
        scripted_responses=[
            "A plain markdown answer is not a structured action.",
            json.dumps({"action": "complete", "content": "structured-ok"}),
        ]
    )
    agent = _agent(
        provider,
        max_output_repairs=1,
        require_structured_actions=True,
    )
    result = await agent.execute_task(_task(), goal="structured output")
    assert result.action.action == AgentActionType.COMPLETE
    assert result.action.content == "structured-ok"
    assert result.repair_count == 1


async def test_task_token_budget_stops_agent():
    provider = MockProvider(
        scripted_responses=[
            _response(
                json.dumps({"action": "complete", "content": "too many tokens"}),
                total_tokens=50_000,
            )
        ]
    )
    agent = _agent(provider, max_task_tokens=1000)
    result = await agent.execute_task(_task(), goal="structured output")
    assert result.action.action == AgentActionType.FAIL
    assert "maximum task tokens exceeded" in (result.action.error or "")


async def test_workflow_llm_call_limit(make_runtime):
    runtime = make_runtime(provider=MockProvider(), max_llm_calls_per_workflow=1)
    workflow = await runtime.submit_goal("Trigger the workflow LLM call limit")
    assert workflow.status.value == "failed"
    assert "max_llm_calls_per_workflow" in (workflow.error or "")


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(
            200,
            json={
                "model": "custom-model",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "agentforge.llm.openai_compatible.asyncio.sleep", no_sleep
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="custom-model",
        client=client,
        max_retries=1,
    )
    response = await provider.complete([ChatMessage.user("hi")])
    assert response.content == "ok"
    assert attempts["count"] == 2
    await provider.aclose()


@pytest.mark.asyncio
async def test_malformed_json_body_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(api_key="sk-test", client=client)
    with pytest.raises(LLMProviderError, match="non-JSON"):
        await provider.complete([ChatMessage.user("hi")])
    await provider.aclose()


@pytest.mark.asyncio
async def test_transport_timeout_raises_after_retries(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "agentforge.llm.openai_compatible.asyncio.sleep", no_sleep
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="sk-test", client=client, max_retries=1
    )
    with pytest.raises(LLMProviderError, match="transport error"):
        await provider.complete([ChatMessage.user("hi")])
    assert attempts["count"] == 2
    await provider.aclose()


def test_hkgai_requires_endpoint_and_model():
    settings = Settings(
        _env_file=None,
        llm_provider="hkgai",
        hkgai_api_key="test-key-not-real",
        hkgai_base_url=None,
        hkgai_model=None,
    )
    with pytest.raises(Exception, match="HKGAI provider requires"):
        build_llm_provider(settings)


class CountingProvider(BaseLLMProvider):
    name = "counting"
    model = "counting-v1"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        self.calls += 1
        return _response("ok", total_tokens=10)


async def test_live_budget_enforces_requests_and_tokens():
    provider = CountingProvider()
    budget = LiveBudgetProvider(provider, max_requests=1, max_tokens=15)
    await budget.complete([ChatMessage.user("one")])
    with pytest.raises(LiveBudgetExceeded, match="request budget"):
        await budget.complete([ChatMessage.user("two")])
    assert provider.calls == 1

    provider2 = CountingProvider()
    budget2 = LiveBudgetProvider(provider2, max_requests=5, max_tokens=15)
    await budget2.complete([ChatMessage.user("one")])
    await budget2.complete([ChatMessage.user("two")])
    assert budget2.tokens == 20
    assert budget2.token_budget_exceeded is True
    with pytest.raises(LiveBudgetExceeded, match="token budget"):
        await budget2.complete([ChatMessage.user("three")])


async def test_staged_live_without_key_is_not_verified(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        data_dir=tmp_path / "data",
        log_level="WARNING",
    )
    report = await LiveStageRunner(
        settings,
        allow_live_api=True,
        output_dir=str(tmp_path / "stages"),
    ).run()
    assert report.api_key_configured is False
    assert report.overall_status == "NOT_VERIFIED"
    assert len(report.stages) == 6
    assert all(stage.status == "NOT_VERIFIED" for stage in report.stages)
    assert list((tmp_path / "stages").rglob("LIVE_STAGES_REPORT.md"))


def test_needs_review_is_not_counted_as_success():
    common = dict(
        batch_id="b",
        title="t",
        category=TaskCategory.RESEARCH,
        difficulty="hard",
        repeat_index=0,
        mode=BenchmarkMode.MULTI_AGENT,
        provider_kind=ProviderKind.FIXTURE,
        provider_name="mock",
        model="fixture",
    )
    review = BenchmarkRun(
        task_id="review",
        **common,
        run_status=RunStatus.NEEDS_REVIEW,
        verification=VerificationResult(
            status=VerificationStatus.NEEDS_REVIEW,
            manual_review_reason="human quality review",
        ),
        metrics=RunMetrics(goal_success=False),
    )
    passed = BenchmarkRun(
        **common,
        task_id="passed",
        run_status=RunStatus.PASSED,
        verification=VerificationResult(status=VerificationStatus.PASSED),
        metrics=RunMetrics(goal_success=True),
    )
    aggregate = aggregate_runs([review, passed])
    assert aggregate.total_runs == 2
    assert aggregate.passed == 1
    assert aggregate.needs_review == 1
    assert aggregate.goal_completion_rate == 0.5


async def test_live_memory_framework_runs_without_crashing(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=tmp_path / "data",
        log_level="WARNING",
    )
    result = await LiveMemoryExperimentRunner(
        settings,
        MockProvider(),
        output_dir=str(tmp_path / "memory"),
    ).run()
    assert result.status == "OBSERVED"
    assert len(result.learning_runs) == 2
    assert result.off_run is not None
    assert result.on_run is not None
    assert result.initial_memory_entries > 0
    assert any("inconclusive" in note for note in result.notes)


@pytest.mark.asyncio
async def test_full_endpoint_base_url_and_content_parts_are_supported():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "model": "custom",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "Hello "},
                                {"type": "text", "text": "world"},
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="sk-secret",
        base_url="https://example.invalid/v1/chat/completions",
        model="custom",
        client=client,
    )
    response = await provider.complete([ChatMessage.user("hi")])
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert response.content == "Hello world"
    assert response.usage.total_tokens == 3
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_error_redacts_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key sk-secret-value")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="sk-secret-value", client=client, max_retries=0
    )
    with pytest.raises(LLMProviderError) as raised:
        await provider.complete([ChatMessage.user("hi")])
    assert "sk-secret-value" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)
    await provider.aclose()
