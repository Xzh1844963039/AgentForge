"""Mock provider, factory and configuration tests."""

from __future__ import annotations

import json

import pytest

from agentforge.agents.base import parse_agent_action
from agentforge.config.settings import Settings
from agentforge.llm.base import ChatMessage
from agentforge.llm.factory import build_llm_provider
from agentforge.llm.mock import MockProvider
from agentforge.models.task import ExecutionPlan
from agentforge.orchestration.errors import AgentOutputError


async def test_mock_plan_is_valid_and_deterministic():
    provider = MockProvider()
    prompt = [
        ChatMessage.system("plan"),
        ChatMessage.user("[GOAL]\nResearch bridges and propose a plan\n\n[OTHER]\nx"),
    ]
    first = await provider.complete(prompt, purpose="plan")
    second = await provider.complete(prompt, purpose="plan")
    assert first.content == second.content

    plan = ExecutionPlan.model_validate(json.loads(first.content))
    assert [item.required_capabilities for item in plan.subtasks] == [
        ["research"],
        ["coding"],
    ]
    assert plan.subtasks[1].depends_on == [plan.subtasks[0].key]
    assert "Research bridges" in plan.goal_summary
    assert first.usage.total_tokens > 0
    assert provider.call_count == 2


async def test_mock_execute_returns_structured_action():
    provider = MockProvider()
    response = await provider.complete(
        [ChatMessage.system("agent"), ChatMessage.user("[TASK]\nresearch x")],
        purpose="execute:research:research",
    )
    action, warning = parse_agent_action(response.content)
    assert warning is None
    assert action.action.value == "complete"
    assert "Benefits of Multi-Agent Systems" in action.content


async def test_mock_review_and_chat():
    provider = MockProvider()
    review = await provider.complete(
        [ChatMessage.user("[TASK]\nreview")], purpose="execute:review:reviewer"
    )
    assert "Verdict:" in review.content

    chat = await provider.complete(
        [ChatMessage.user("hello")], purpose="chat:research"
    )
    assert "ResearchAgent here" in chat.content


async def test_mock_scripted_responses_are_consumed():
    provider = MockProvider(scripted_responses=["first", "second"])
    one = await provider.complete([ChatMessage.user("x")], purpose="plan")
    two = await provider.complete([ChatMessage.user("x")], purpose="plan")
    assert one.content == "first"
    assert two.content == "second"
    assert provider.call_count == 2
    assert provider.remaining_scripted() == 0


def test_factory_builds_mock():
    settings = Settings(_env_file=None, llm_provider="mock")
    provider = build_llm_provider(settings)
    assert provider.name == "mock"


def test_factory_rejects_missing_deepseek_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AGENTFORGE_DEEPSEEK_API_KEY", raising=False)
    settings = Settings(
        _env_file=None, llm_provider="deepseek", deepseek_api_key=None
    )
    with pytest.raises(Exception, match="no API key"):
        build_llm_provider(settings)


def test_factory_requires_hkgai_endpoint(monkeypatch):
    settings = Settings(
        _env_file=None,
        llm_provider="hkgai",
        hkgai_api_key="test-key",
        hkgai_base_url=None,
        hkgai_model=None,
    )
    with pytest.raises(Exception, match="HKGAI provider requires"):
        build_llm_provider(settings)


def test_factory_rejects_unknown_provider():
    settings = Settings(_env_file=None, llm_provider="mock")
    with pytest.raises(Exception, match="Unknown LLM provider"):
        build_llm_provider(settings, provider_name="openai")


def test_env_api_key_aliases(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek")
    monkeypatch.setenv("AGENTFORGE_HKGAI_API_KEY", "env-hkgai")
    settings = Settings(_env_file=None)
    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == "env-deepseek"
    assert settings.hkgai_api_key is not None
    assert settings.hkgai_api_key.get_secret_value() == "env-hkgai"


def test_settings_creates_directories(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "nested" / "data")
    settings.ensure_directories()
    assert settings.data_dir.is_dir()
    assert settings.resolved_sqlite_file.parent.is_dir()
    assert settings.resolved_events_file.parent.is_dir()


def test_parse_agent_action_plain_text_fallback():
    action, warning = parse_agent_action("A plain markdown answer")
    assert action.action.value == "complete"
    assert action.content == "A plain markdown answer"
    assert warning is not None


def test_parse_agent_action_rejects_malformed_action():
    with pytest.raises(AgentOutputError):
        parse_agent_action('{"action": "delegate", "content": "no subtasks"}')
