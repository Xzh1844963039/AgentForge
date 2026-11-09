"""Benchmark runtime assembly and single-agent baseline construction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agentforge.agents.base import resolve_agent_protocol
from agentforge.agents.coordinator import (
    CoordinatorAgent,
    resolve_planner_protocol,
)
from agentforge.agents.factory import DEFAULT_PROFILES
from agentforge.agents.registry import AgentRegistry
from agentforge.benchmark.models import (
    BenchmarkConfig,
    BenchmarkMode,
    BenchmarkTask,
    ProviderKind,
)
from agentforge.benchmark.provider import (
    BudgetedProvider,
    FixturePolicyProvider,
)
from agentforge.config.settings import Settings
from agentforge.llm.base import BaseLLMProvider
from agentforge.llm.factory import build_llm_provider
from agentforge.models.agent import AgentProfile, AgentRole
from agentforge.orchestration.control import WorkflowControlService
from agentforge.orchestration.orchestrator import TaskOrchestrator
from agentforge.runtime import AgentForgeRuntime
from agentforge.tools.base import ToolPermission

logger = logging.getLogger("agentforge.benchmark.execution")

SOLO_AGENT_ID = "solo"


@dataclass
class BenchmarkRuntimeHandle:
    runtime: AgentForgeRuntime
    provider: BudgetedProvider
    settings: Settings
    mode: BenchmarkMode
    agent_aliases: dict[str, str]
    data_dir: Path

    def agent_for(self, role_or_id: str) -> str:
        if role_or_id in self.agent_aliases:
            return self.agent_aliases[role_or_id]
        return role_or_id

    async def close(self) -> None:
        await self.runtime.aclose()


def benchmark_settings(
    task: BenchmarkTask,
    config: BenchmarkConfig,
    data_dir: Path,
) -> Settings:
    memory_file = (
        Path(config.shared_memory_file)
        if config.shared_memory_file
        else data_dir / "memory_evolution.db"
    )
    return Settings(
        _env_file=None,
        llm_provider="mock" if config.provider_kind == ProviderKind.FIXTURE else config.provider_name,
        data_dir=data_dir,
        workspace_root=data_dir / "workspaces",
        artifacts_root=data_dir / "artifacts",
        memory_file=memory_file,
        log_level="WARNING",
        mock_latency_ms=0,
        max_delegation_depth=task.max_delegation_depth,
        max_execution_steps=task.max_execution_steps,
        max_tool_calls_per_task=task.tool_budget,
        memory_evolution_enabled=config.memory_evolution,
        memory_recall_enabled=config.memory_recall,
        sandbox_timeout_seconds=min(
            30, max(2, int(task.time_budget_seconds))
        ),
    )


def _single_agent_runtime(runtime: AgentForgeRuntime) -> None:
    """Replace the fleet with one all-capability coordinator.

    Tool dispatch, permissions, sandboxing, persistence and governance remain the
    Phase 1–4 code paths. Only the agent fleet changes.
    """
    all_capabilities = sorted(
        {
            capability
            for profile in DEFAULT_PROFILES
            for capability in profile.capabilities
        }
    )
    profile = AgentProfile(
        agent_id=SOLO_AGENT_ID,
        name="SoloAgent",
        role=AgentRole.COORDINATOR,
        capabilities=all_capabilities,
        description=(
            "Single-agent benchmark baseline. It plans and executes the whole "
            "task itself through the same tool dispatcher and safety limits."
        ),
    )
    solo = CoordinatorAgent(
        profile=profile,
        provider=runtime.provider,
        memory=runtime.memory,
        tools=runtime.tools,
        memory_recall_limit=runtime.settings.memory_recall_limit,
        tool_executor=runtime.tool_executor,
        max_tool_calls=runtime.settings.max_tool_calls_per_task,
        max_output_repairs=runtime.settings.structured_output_repairs,
        require_structured_actions=runtime.settings.require_structured_actions,
        max_task_tokens=runtime.settings.max_task_tokens,
        max_repeated_tool_actions=runtime.settings.max_repeated_tool_actions,
        planner_protocol=resolve_planner_protocol(
            runtime.settings.planner_protocol, runtime.provider.name
        ),
        agent_protocol=resolve_agent_protocol(
            runtime.settings.agent_protocol, runtime.provider.name
        ),
    )
    for permission in ToolPermission:
        runtime.tool_policy.grant(SOLO_AGENT_ID, permission)
    runtime.agents = AgentRegistry()
    runtime.agents.register(solo)
    runtime.bus.register_agent(solo)
    runtime.orchestrator = TaskOrchestrator(
        agents=runtime.agents,
        bus=runtime.bus,
        conversations=runtime.conversations,
        store=runtime.store,
        events=runtime.events,
        settings=runtime.settings,
        tool_context_factory=runtime._tool_context_for_task,
        approval_service=runtime.approval_service,
        clarification_service=runtime.clarification_service,
        workflow_control=WorkflowControlService(runtime.store, runtime.events),
    )


def _seed_workspace(handle: BenchmarkRuntimeHandle, task: BenchmarkTask) -> None:
    for seed in task.initial_workspace:
        agent_id = handle.agent_for(seed.agent)
        workspace = handle.runtime.workspaces.workspace_for(agent_id)
        target = (workspace / seed.path).resolve()
        if not target.is_relative_to(workspace.resolve()):
            raise ValueError(
                f"seed path escapes workspace: {seed.path!r}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(seed.content, encoding="utf-8")


async def create_benchmark_runtime(
    task: BenchmarkTask,
    config: BenchmarkConfig,
    data_dir: Path,
    *,
    provider_override: BaseLLMProvider | None = None,
) -> BenchmarkRuntimeHandle:
    settings = benchmark_settings(task, config, data_dir)
    settings.ensure_directories()
    base_provider: BaseLLMProvider
    if provider_override is not None:
        base_provider = provider_override
    elif config.provider_kind == ProviderKind.FIXTURE:
        base_provider = FixturePolicyProvider(task)
    else:
        base_provider = build_llm_provider(settings)
    provider = BudgetedProvider(
        base_provider,
        token_budget=task.token_budget,
        name=base_provider.name,
    )
    runtime = AgentForgeRuntime(settings, provider=provider)
    if config.mode == BenchmarkMode.SINGLE_AGENT:
        _single_agent_runtime(runtime)
    if runtime.tool_executor is None:
        await runtime.aclose()
        raise RuntimeError("benchmark requires tools_enabled=true")
    if task.allowed_tools is not None:
        runtime.tool_executor.allowed_tools = frozenset(task.allowed_tools)
    aliases = (
        {
            "coordinator": SOLO_AGENT_ID,
            "research": SOLO_AGENT_ID,
            "coding": SOLO_AGENT_ID,
            "reviewer": SOLO_AGENT_ID,
            "solo": SOLO_AGENT_ID,
        }
        if config.mode == BenchmarkMode.SINGLE_AGENT
        else {
            "coordinator": "coordinator",
            "research": "research",
            "coding": "coding",
            "reviewer": "reviewer",
            "solo": SOLO_AGENT_ID,
        }
    )
    handle = BenchmarkRuntimeHandle(
        runtime=runtime,
        provider=provider,
        settings=settings,
        mode=config.mode,
        agent_aliases=aliases,
        data_dir=data_dir,
    )
    provider.set_fixture_runtime(runtime)
    _seed_workspace(handle, task)
    return handle
