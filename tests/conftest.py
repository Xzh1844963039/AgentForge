"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio

from agentforge.config.settings import Settings
from agentforge.runtime import AgentForgeRuntime


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=tmp_path / "data",
        log_level="WARNING",
        mock_latency_ms=0,
        max_execution_steps=30,
        max_delegation_depth=3,
        max_delegation_rounds=2,
        max_plan_subtasks=8,
        max_tasks_per_workflow=60,
        enable_final_review=True,
    )


@pytest_asyncio.fixture
async def make_runtime(settings):
    """Factory that builds runtimes sharing one test settings object."""
    runtimes: list[AgentForgeRuntime] = []

    def factory(*, provider=None, **overrides) -> AgentForgeRuntime:
        runtime_settings = (
            settings.model_copy(deep=True, update=overrides)
            if overrides
            else settings.model_copy(deep=True)
        )
        runtime = AgentForgeRuntime(runtime_settings, provider=provider)
        runtimes.append(runtime)
        return runtime

    yield factory

    for runtime in runtimes:
        try:
            await runtime.aclose()
        except Exception:
            pass
