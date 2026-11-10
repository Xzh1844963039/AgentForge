"""Offline tests for the compact Agent Action protocol and runtime wiring."""

from __future__ import annotations

import json

from agentforge.agents.base import (
    ACTION_PROTOCOL,
    COMPACT_ACTION_PROTOCOL,
    parse_agent_action,
    resolve_agent_protocol,
)
from agentforge.agents import build_default_agents
from agentforge.agents.coordinator import COMPACT_PLANNER_PROTOCOL
from agentforge.config.settings import Settings
from agentforge.llm.base import LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider
from agentforge.memory import InMemoryMemoryStore
from agentforge.models.task import AgentActionType, Task
from agentforge.runtime import AgentForgeRuntime
from agentforge.tools import ToolRegistry


class _NamedMockProvider(MockProvider):
    """Mock provider that reports as a specific provider name."""

    def __init__(self, name: str, *, scripted_responses=None) -> None:
        super().__init__(scripted_responses=scripted_responses)
        self.name = name
        self.model = f"mock-{name}"


def _plan_payload() -> str:
    return json.dumps(
        {
            "goal_summary": "Create a simple note.",
            "subtasks": [
                {
                    "key": "write",
                    "description": "Create notes/simple.md containing hello.",
                    "required_capabilities": ["coding"],
                    "depends_on": [],
                    "kind": "coding",
                }
            ],
        }
    )


def _runtime(provider: MockProvider, tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        llm_provider=provider.name,
        memory_evolution_enabled=False,
    )


def test_compact_action_protocol_covers_all_actions():
    for action in [
        "complete",
        "tool_call",
        "delegate",
        "clarify",
        "reject_assignment",
        "fail",
    ]:
        assert f'"{action}"' in COMPACT_ACTION_PROTOCOL
    assert len(COMPACT_ACTION_PROTOCOL) < len(ACTION_PROTOCOL) * 0.6


def test_compact_action_protocol_parses_every_action():
    samples = [
        {"action": "complete", "content": "done", "reasoning": "ok"},
        {
            "action": "tool_call",
            "tool_calls": [{"name": "write_file", "arguments": {"path": "a.md", "content": "x"}}],
            "reasoning": "write it",
        },
        {
            "action": "delegate",
            "content": "partial",
            "delegation": {
                "reason": "need research",
                "subtasks": [
                    {
                        "description": "research",
                        "required_capabilities": ["research"],
                        "kind": "generic",
                    }
                ],
            },
            "reasoning": "split",
        },
        {
            "action": "clarify",
            "question": "which file?",
            "clarification_context": "no path",
            "possible_interpretations": ["a", "b"],
        },
        {"action": "reject_assignment", "reason": "outside role"},
        {"action": "fail", "error": "blocked", "content": "partial"},
    ]
    expected = [
        AgentActionType.COMPLETE,
        AgentActionType.TOOL_CALL,
        AgentActionType.DELEGATE,
        AgentActionType.CLARIFY,
        AgentActionType.REJECT_ASSIGNMENT,
        AgentActionType.FAIL,
    ]
    for payload, action_type in zip(samples, expected, strict=True):
        parsed, warning = parse_agent_action(json.dumps(payload))
        assert warning is None
        assert parsed.action == action_type


def test_resolve_agent_protocol_auto_selects_compact_for_hkgai():
    assert resolve_agent_protocol("auto", "hkgai") == "compact"
    assert resolve_agent_protocol("auto", "HKGAI") == "compact"
    assert resolve_agent_protocol("auto", "mock") == "full"
    assert resolve_agent_protocol("auto", "deepseek") == "full"
    assert resolve_agent_protocol("full", "hkgai") == "full"
    assert resolve_agent_protocol("compact", "mock") == "compact"


def test_compact_tool_catalog_keeps_names_permissions_and_required_args(tmp_path):
    import asyncio

    async def load_catalog():
        settings = Settings(data_dir=tmp_path, llm_provider="mock")
        async with AgentForgeRuntime(settings) as runtime:
            return runtime.tools

    tools = asyncio.run(load_catalog())
    compact = tools.prompt_catalog(compact=True)
    for spec in tools.list_specs():
        assert spec.name in compact
        required = spec.input_schema.get("required") or []
        for name in required:
            assert f"{name}*" in compact or f"{name}*" in compact.replace(" ", "")
    assert "approval" in compact
    assert "python_execute" in compact
    assert "delete_file" in compact
    assert len(compact) < len(tools.prompt_catalog()) * 0.3


def test_hkgai_runtime_uses_compact_planner_and_compact_actions(tmp_path):
    import asyncio

    provider = _NamedMockProvider("hkgai", scripted_responses=[_plan_payload()])

    async def run():
        settings = Settings(
            data_dir=tmp_path,
            llm_provider="hkgai",
            memory_evolution_enabled=False,
        )
        async with AgentForgeRuntime(settings, provider=provider) as runtime:
            coordinator = runtime.get_agent("coordinator")
            assert coordinator.planner_protocol == "compact"
            assert all(
                agent.agent_protocol == "compact" for agent in runtime.agents.list()
            )
            for agent in runtime.agents.list():
                system = agent._system_prompt()
                assert COMPACT_ACTION_PROTOCOL in system
                assert ACTION_PROTOCOL not in system
                assert "[TOOLS]" in system
            workflow = await runtime.submit_goal(
                "Create notes/simple.md containing hello."
            )
            return runtime, workflow

    runtime, workflow = asyncio.run(run())
    assert workflow.status.value == "completed"
    first_messages = provider.calls[0]["messages"]
    assert first_messages[0]["content"] == COMPACT_PLANNER_PROTOCOL
    assert "[AGENTS]" in first_messages[1]["content"]
    assert any(
        call["purpose"].startswith("execute:coding")
        for call in provider.calls
    )
    system_prompts = [
        message["content"]
        for call in provider.calls
        for message in call["messages"]
        if message["role"] == "system"
    ]
    assert all(ACTION_PROTOCOL not in prompt for prompt in system_prompts)


def test_deepseek_runtime_keeps_full_protocols(tmp_path):
    import asyncio

    provider = _NamedMockProvider("deepseek", scripted_responses=[_plan_payload()])

    async def run():
        settings = Settings(
            data_dir=tmp_path,
            llm_provider="deepseek",
            memory_evolution_enabled=False,
        )
        async with AgentForgeRuntime(settings, provider=provider) as runtime:
            coordinator = runtime.get_agent("coordinator")
            assert coordinator.planner_protocol == "full"
            assert all(
                agent.agent_protocol == "full" for agent in runtime.agents.list()
            )
            for agent in runtime.agents.list():
                assert ACTION_PROTOCOL in agent._system_prompt()
            await runtime.submit_goal("Create notes/simple.md containing hello.")

    asyncio.run(run())
    first_messages = provider.calls[0]["messages"]
    assert first_messages[0]["content"] != COMPACT_PLANNER_PROTOCOL
    assert "planning module" in first_messages[0]["content"]


def test_compact_mode_still_enforces_tool_validation(tmp_path):
    """A malformed tool_call must still be rejected by the dispatcher."""
    import asyncio

    bad_tool_call = json.dumps(
        {
            "action": "tool_call",
            "tool_calls": [{"name": "write_file", "arguments": {"path": "x.md"}}],
            "reasoning": "missing content",
        }
    )
    provider = _NamedMockProvider(
        "hkgai", scripted_responses=[_plan_payload(), bad_tool_call]
    )

    async def run():
        settings = Settings(
            data_dir=tmp_path,
            llm_provider="hkgai",
            memory_evolution_enabled=False,
        )
        async with AgentForgeRuntime(settings, provider=provider) as runtime:
            workflow = await runtime.submit_goal(
                "Create notes/simple.md containing hello."
            )
            records = runtime.list_tool_calls(workflow.workflow_id)
            return [(record.status.value, record.error or "") for record in records], (
                workflow.status.value
            )

    records, workflow_status = asyncio.run(run())
    assert records, "the invalid tool call should still be audited"
    assert all(status != "success" for status, _error in records)
    assert any("content" in error for _status, error in records)
    assert workflow_status in {"completed", "failed"}


def _truncated_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        provider="mock",
        model="mock-deterministic-v1",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=1024, total_tokens=1034),
        finish_reason="length",
    )


def test_plan_retry_does_not_echo_truncated_content():
    import asyncio

    provider = MockProvider(
        scripted_responses=[
            _truncated_response(
                '{"goal_summary":"x","subtasks":[{"key":"a","description":"trunc'
            ),
            _plan_payload(),
        ]
    )
    agents = build_default_agents(
        provider=provider,
        memory=InMemoryMemoryStore(),
        tools=ToolRegistry(),
        planner_protocol="compact",
    )
    coordinator = next(a for a in agents if a.agent_id == "coordinator")

    result = asyncio.run(
        coordinator.plan_goal(
            "Create notes/simple.md containing hello.",
            agent_cards=[agent.card for agent in agents],
            max_subtasks=3,
        )
    )
    assert len(result.attempts) == 2
    assert result.attempts[0].error is not None
    second = provider.calls[1]["messages"]
    assert not any(message["role"] == "assistant" for message in second)
    assert "[RETRY]" in second[-1]["content"]


def test_action_retry_does_not_echo_truncated_content():
    import asyncio

    provider = MockProvider(
        scripted_responses=[
            _truncated_response('{"action":"complete","content":"trunc'),
            json.dumps(
                {"action": "complete", "content": "ok", "reasoning": "done"}
            ),
        ]
    )
    agents = build_default_agents(
        provider=provider,
        memory=InMemoryMemoryStore(),
        tools=ToolRegistry(),
        agent_protocol="compact",
    )
    coding = next(a for a in agents if a.agent_id == "coding")
    task = Task(
        workflow_id="wf_test",
        description="Return a completion.",
        required_capabilities=["coding"],
        assigned_agent="coding",
        metadata={"kind": "coding"},
    )

    result = asyncio.run(coding.execute_task(task, goal="test goal"))
    assert result.repair_count == 1
    assert result.action.content == "ok"
    second = provider.calls[1]["messages"]
    assert not any(message["role"] == "assistant" for message in second)
    assert "[RETRY]" in second[-1]["content"]


def test_compact_tool_turns_keep_prompts_bounded(tmp_path):
    """Compact tool loops must not grow the prompt with raw tool history."""
    import asyncio

    plan = json.dumps(
        {
            "goal_summary": "Write and verify a note.",
            "subtasks": [
                {
                    "key": "write",
                    "description": "Write notes/ctx.txt and read it back.",
                    "required_capabilities": ["coding"],
                    "depends_on": [],
                    "kind": "coding",
                }
            ],
        }
    )
    script = [
        plan,
        json.dumps(
            {
                "action": "tool_call",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "arguments": {"path": "notes/ctx.txt", "content": "bounded\n"},
                    }
                ],
            }
        ),
        json.dumps(
            {
                "action": "tool_call",
                "tool_calls": [
                    {"name": "read_file", "arguments": {"path": "notes/ctx.txt"}}
                ],
            }
        ),
        json.dumps({"action": "complete", "content": "done"}),
        json.dumps({"action": "complete", "content": "draft"}),
        json.dumps({"action": "complete", "content": "final"}),
    ]
    provider = _NamedMockProvider("hkgai", scripted_responses=script)

    async def run():
        settings = Settings(
            data_dir=tmp_path,
            llm_provider="hkgai",
            memory_evolution_enabled=False,
            enable_final_review=False,
        )
        async with AgentForgeRuntime(settings, provider=provider) as runtime:
            workflow = await runtime.submit_goal("Write and verify a note.")
            return workflow.status.value

    status = asyncio.run(run())
    assert status == "completed"
    worker_prompts = [
        len("\n\n".join(message["content"] for message in call["messages"]))
        for call in provider.calls
        if call["purpose"].startswith("execute:coding")
    ]
    assert len(worker_prompts) == 3
    assert max(worker_prompts) < 2000, worker_prompts
    assert worker_prompts[2] - worker_prompts[1] < 400, worker_prompts
