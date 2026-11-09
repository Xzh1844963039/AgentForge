"""Offline tests for the bounded real Stage 2 runner."""

from __future__ import annotations

import json

import pytest

from agentforge.benchmark.hkgai_stage2 import (
    MAX_TOKENS_PER_REQUEST,
    EVIDENCE_MARKER,
    STAGE2_GOAL,
    Stage2BudgetExceeded,
    Stage2BudgetProvider,
    Stage2Stopped,
    run_hkgai_stage2,
)
from agentforge.config.settings import Settings
from agentforge.llm.base import LLMProviderError, LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider


class _NamedMockProvider(MockProvider):
    def __init__(self, name: str, *, scripted_responses=None) -> None:
        super().__init__(scripted_responses=scripted_responses)
        self.name = name
        self.model = f"mock-{name}"


def _action(payload: dict) -> str:
    return json.dumps(payload)


def _plan() -> str:
    return json.dumps(
        {
            "goal_summary": "Create and verify the stage2 note.",
            "subtasks": [
                {
                    "key": "write_note",
                    "description": (
                        f"Write notes/stage2.txt containing {EVIDENCE_MARKER} and "
                        "read it back with read_file."
                    ),
                    "required_capabilities": ["coding"],
                    "depends_on": [],
                    "kind": "coding",
                }
            ],
        }
    )


def _stage2_script() -> list[str]:
    return [
        _plan(),
        _action(
            {
                "action": "tool_call",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "arguments": {
                            "path": "notes/stage2.txt",
                            "content": f"{EVIDENCE_MARKER}\n",
                        },
                    }
                ],
                "reasoning": "write the note",
            }
        ),
        _action(
            {
                "action": "tool_call",
                "tool_calls": [
                    {
                        "name": "read_file",
                        "arguments": {"path": "notes/stage2.txt"},
                    }
                ],
                "reasoning": "verify recorded content",
            }
        ),
        _action(
            {
                "action": "complete",
                "content": f"Wrote and verified {EVIDENCE_MARKER}.",
                "reasoning": "tool evidence confirms",
            }
        ),
        _action(
            {
                "action": "complete",
                "content": f"# Draft\n\nEvidence: {EVIDENCE_MARKER}",
                "reasoning": "synthesize",
            }
        ),
        _action(
            {
                "action": "complete",
                "content": "Verdict: APPROVED",
                "reasoning": "review",
            }
        ),
        _action(
            {
                "action": "complete",
                "content": (
                    f"# Final Report\n\nRecorded stdout contains {EVIDENCE_MARKER} "
                    "and the tool audit recorded exit code 0."
                ),
                "reasoning": "finalize",
            }
        ),
    ]


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        llm_provider="hkgai",
        memory_evolution_enabled=False,
    )


def test_stage2_runner_passes_offline_with_scripted_runtime(tmp_path):
    provider = _NamedMockProvider("hkgai", scripted_responses=_stage2_script())
    result = pytest.importorskip("asyncio").run(
        run_hkgai_stage2(
            _settings(tmp_path),
            allow_live_api=False,
            output_dir=tmp_path / "runs",
            provider_override=provider,
            require_sandbox=False,
            required_tool="read_file",
        )
    )
    assert result.mode == "OFFLINE_TEST"
    assert result.status == "MOCK_VALIDATED"
    assert result.workflow_status == "completed"
    assert result.plan_subtasks == 1
    assert result.plan_attempts == 1
    assert result.total_requests == 7
    assert len(result.calls) == 7
    assert all(call.requested_max_tokens <= MAX_TOKENS_PER_REQUEST for call in result.calls)
    assert sum(call.total_tokens for call in result.calls) == result.total_tokens
    failed = {key: check for key, check in result.checks.items() if not check.passed}
    assert not failed, failed
    assert result.checks["F_tool_execution"].passed
    assert result.checks["G_tool_evidence_in_final_report"].passed
    assert result.tool_calls
    assert (tmp_path / "runs").exists()
    run_dirs = list((tmp_path / "runs").glob("hkgai_stage2_*"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "stage2.json").exists()
    assert (run_dirs[0] / "STAGE2_REPORT.md").exists()


def test_stage2_runner_requires_live_authorization(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(PermissionError):
        pytest.importorskip("asyncio").run(
            run_hkgai_stage2(settings, allow_live_api=False, output_dir=tmp_path)
        )


class _DummyProvider(MockProvider):
    name = "dummy"

    def __init__(self) -> None:
        super().__init__()
        self.max_tokens_seen: list[int] = []

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        self.max_tokens_seen.append(max_tokens or 0)
        return LLMResponse(
            content="ok",
            provider=self.name,
            model="dummy-1",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            finish_reason="stop",
            raw={"http_status": 200},
        )


def test_stage2_budget_provider_caps_tokens_and_enforces_limits(tmp_path):
    import asyncio

    base = _DummyProvider()
    budget = Stage2BudgetProvider(base, max_requests=2, max_tokens=15)

    async def run():
        await budget.complete([], max_tokens=4096)
        with pytest.raises(Stage2BudgetExceeded, match="token budget exceeded"):
            await budget.complete([], max_tokens=256)

    asyncio.run(run())
    assert base.max_tokens_seen == [MAX_TOKENS_PER_REQUEST]
    assert budget.requests == 1
    assert budget.tokens == 15
    assert budget.budget_exhausted is True


def test_stage2_budget_provider_hard_stops_on_disconnect(tmp_path):
    import asyncio

    class _DisconnectProvider(MockProvider):
        name = "hkgai"

        async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
            raise LLMProviderError(
                "hkgai transport error after 100 ms: RemoteProtocolError",
                provider="hkgai",
            )

    budget = Stage2BudgetProvider(_DisconnectProvider())

    async def run():
        with pytest.raises(LLMProviderError):
            await budget.complete([])
        with pytest.raises(Stage2Stopped):
            await budget.complete([])

    asyncio.run(run())
    assert budget.requests == 1
    assert budget.hard_stopped is True
    assert "RemoteProtocolError" in budget.hard_stop_reason
    assert len(budget.calls) == 1
    assert budget.calls[0].status == "failed"


def test_stage2_runner_fails_fast_on_disconnect(tmp_path):
    import asyncio

    calls = {"count": 0}

    class _DisconnectProvider(MockProvider):
        name = "hkgai"

        async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
            calls["count"] += 1
            raise LLMProviderError(
                "hkgai transport error after 100 ms: RemoteProtocolError",
                provider="hkgai",
            )

    result = asyncio.run(
        run_hkgai_stage2(
            _settings(tmp_path),
            allow_live_api=False,
            output_dir=tmp_path / "runs",
            provider_override=_DisconnectProvider(),
            require_sandbox=False,
        )
    )
    assert calls["count"] == 1, "no automatic retry or extra request is allowed"
    assert result.hard_stopped is True
    assert result.status == "FAILED"
    assert result.total_requests == 1


def test_stage2_goal_is_tool_and_evidence_focused():
    assert "stage2_sum.py" in STAGE2_GOAL
    assert EVIDENCE_MARKER in STAGE2_GOAL
