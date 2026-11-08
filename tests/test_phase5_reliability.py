"""Phase 5 reliability and fault-injection tests."""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from pydantic import BaseModel

from agentforge.benchmark.models import BenchmarkConfig
from agentforge.benchmark.runner import BenchmarkRunner
from agentforge.llm.base import LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider
from agentforge.models.tool import (
    ReconciliationStatus,
    ToolCall,
    ToolResultStatus,
)
from agentforge.models.workflow import WorkflowStatus
from agentforge.persistence import EventLogger, SQLiteStore
from agentforge.sandbox.base import SandboxConfig
from agentforge.sandbox.docker import DockerSandbox
from agentforge.tools.approval import AutoApprovalGate
from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolRegistry,
    ToolResult,
)
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.policy import ToolPermissionPolicy


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        provider="mock",
        model="reliability-fixture",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class InvalidActionProvider(MockProvider):
    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        if purpose == "plan":
            return await super().complete(
                messages, purpose=purpose, temperature=temperature, max_tokens=max_tokens
            )
        return _response(
            '{"action": "tool_call", "tool_calls": '
            '[{"name": "Bad Tool Name", "arguments": {}}]}'
        )


class RepeatActionProvider(MockProvider):
    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        if purpose == "plan":
            return await super().complete(
                messages, purpose=purpose, temperature=temperature, max_tokens=max_tokens
            )
        return _response(
            json.dumps(
                {
                    "action": "tool_call",
                    "tool_calls": [
                        {
                            "name": "write_file",
                            "arguments": {
                                "path": "notes/loop.md",
                                "content": "loop",
                            },
                        }
                    ],
                }
            )
        )


class ClarifyProvider(MockProvider):
    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        if purpose == "plan":
            return await super().complete(
                messages, purpose=purpose, temperature=temperature, max_tokens=max_tokens
            )
        return _response(
            json.dumps(
                {
                    "action": "clarify",
                    "question": "Which policy applies?",
                    "clarification_context": "No jurisdiction was specified.",
                    "possible_interpretations": ["A", "B"],
                }
            )
        )


class EmptyArgs(BaseModel):
    pass


class SlowTool(BaseTool):
    name = "slow_tool"
    description = "Sleeps beyond its own timeout (test only)."
    args_model = EmptyArgs
    timeout_seconds = 1

    async def run(self, arguments: EmptyArgs, context) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult.ok({"unexpected": True})


class HighRiskTool(BaseTool):
    name = "delete_file"
    description = "Observable destructive side effect (test only)."
    args_model = EmptyArgs
    permissions = [ToolPermission.FILE_DELETE]
    requires_approval = True
    timeout_seconds = 5

    def __init__(self, effects: list[str]) -> None:
        super().__init__()
        self.effects = effects

    async def run(self, arguments: EmptyArgs, context) -> ToolResult:
        self.effects.append("executed")
        return ToolResult.ok({"effects": len(self.effects)})


async def test_invalid_json_action_fails_without_fabrication(make_runtime):
    runtime = make_runtime(provider=InvalidActionProvider())
    workflow = await runtime.submit_goal("Trigger a malformed agent action")
    assert workflow.status == WorkflowStatus.FAILED
    assert "AgentOutputError" in (workflow.error or "") or "invalid agent action" in (
        workflow.error or ""
    ).lower()
    assert workflow.final_result is None


async def test_tool_schema_validation_rejects_argument_before_tool(make_runtime):
    runtime = make_runtime(provider=MockProvider())
    executor = runtime.tool_executor
    assert executor is not None
    result = await executor.execute(
        ToolCall(name="read_file", arguments={"path": 123}),
        ToolExecutionContext(agent_id="coding", workflow_id="wf", task_id="t"),
    )
    assert result.status == ToolResultStatus.ERROR
    records = runtime.list_tool_calls("wf")
    assert records[-1].metadata["reason"] == "schema_validation"


async def test_repeated_action_is_stopped_by_tool_budget(make_runtime):
    runtime = make_runtime(
        provider=RepeatActionProvider(), max_tool_calls_per_task=2
    )
    workflow = await runtime.submit_goal("Repeat the same tool action forever")
    assert workflow.status == WorkflowStatus.FAILED
    assert "maximum tool calls exceeded" in (workflow.error or "")


async def test_tool_timeout_is_structured(tmp_path):
    store = SQLiteStore(tmp_path / "timeout.db")
    events = EventLogger(tmp_path / "events.jsonl")
    try:
        registry = ToolRegistry()
        registry.register(SlowTool())
        executor = ToolExecutor(
            registry,
            store,
            events,
            ToolPermissionPolicy({"coding": set()}),
            AutoApprovalGate(approved=True),
        )
        result = await executor.execute(
            ToolCall(name="slow_tool", arguments={}),
            ToolExecutionContext(agent_id="coding", workflow_id="wf", task_id="t"),
        )
        assert result.status == ToolResultStatus.TIMEOUT
        assert "timed out" in (result.error or "")
    finally:
        events.close()
        store.close()


async def test_audit_write_failure_enters_reconciliation_and_blocks_replay(tmp_path):
    store = SQLiteStore(tmp_path / "recon.db")
    events = EventLogger(tmp_path / "events.jsonl")
    effects: list[str] = []
    try:
        registry = ToolRegistry()
        registry.register(HighRiskTool(effects))
        executor = ToolExecutor(
            registry,
            store,
            events,
            ToolPermissionPolicy(
                {"coding": {ToolPermission.FILE_DELETE}}
            ),
            AutoApprovalGate(approved=True),
        )
        context = ToolExecutionContext(
            agent_id="coding", workflow_id="wf-recon", task_id="task-recon"
        )
        call = ToolCall(name="delete_file", arguments={})
        original_save = store.save_tool_call

        def fail_success_audit(record):
            if record.status == ToolResultStatus.SUCCESS:
                raise RuntimeError("simulated SQLite outage")
            return original_save(record)

        store.save_tool_call = fail_success_audit
        result = await executor.execute(call, context)
        assert result.status == ToolResultStatus.NEEDS_RECONCILIATION
        assert effects == ["executed"]
        unresolved = store.list_tool_reconciliations(
            "wf-recon", status=ReconciliationStatus.UNRESOLVED
        )
        assert len(unresolved) == 1

        store.save_tool_call = original_save
        context.metadata["replay_mode"] = True
        replay = await executor.execute(call, context)
        assert replay.status == ToolResultStatus.NEEDS_RECONCILIATION
        assert effects == ["executed"], "unresolved side effect must not be replayed"
    finally:
        events.close()
        store.close()


async def test_high_risk_tool_is_not_executed_without_intent_audit(tmp_path):
    store = SQLiteStore(tmp_path / "recon-intent.db")
    events = EventLogger(tmp_path / "events.jsonl")
    effects: list[str] = []
    try:
        registry = ToolRegistry()
        registry.register(HighRiskTool(effects))
        executor = ToolExecutor(
            registry,
            store,
            events,
            ToolPermissionPolicy({"coding": {ToolPermission.FILE_DELETE}}),
            AutoApprovalGate(approved=True),
        )

        def unavailable(_reconciliation):
            raise RuntimeError("simulated SQLite outage before execution")

        store.begin_tool_reconciliation = unavailable
        result = await executor.execute(
            ToolCall(name="delete_file", arguments={}),
            ToolExecutionContext(
                agent_id="coding", workflow_id="wf", task_id="task"
            ),
        )
        assert result.status == ToolResultStatus.DENIED
        assert effects == []
        assert "not executed" in (result.error or "")
    finally:
        events.close()
        store.close()


async def test_memory_recall_failure_is_non_fatal(make_runtime):
    runtime = make_runtime(provider=MockProvider())
    runtime.memory.recall = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("simulated memory outage")
    )
    workflow = await runtime.submit_goal("Complete despite memory unavailability")
    assert workflow.status == WorkflowStatus.COMPLETED
    assert workflow.final_result


async def test_unanswered_clarification_keeps_workflow_paused(make_runtime):
    runtime = make_runtime(provider=ClarifyProvider())
    workflow = await runtime.submit_goal("Ask a blocking clarification")
    assert workflow.status == WorkflowStatus.PAUSED
    clarifications = runtime.list_clarifications(workflow.workflow_id)
    assert len(clarifications) == 1
    assert clarifications[0].status.value == "open"
    cancelled = await runtime.cancel_workflow(
        workflow.workflow_id, reason="reliability test cleanup"
    )
    assert cancelled.status == WorkflowStatus.CANCELLED


async def test_benchmark_pause_resume_has_no_duplicate_effect(tmp_path):
    config = BenchmarkConfig(
        dataset_path="benchmarks/dev/tasks.jsonl",
        output_dir=str(tmp_path / "runs"),
        task_ids=["recovery_pause_resume", "recovery_cancel", "delegation_depth_limit"],
    )
    outcome = await BenchmarkRunner(config).run_batch()
    statuses = {run.task_id: run.run_status.value for run in outcome.runs}
    assert statuses["recovery_pause_resume"] == "passed"
    assert statuses["recovery_cancel"] == "passed"
    assert statuses["delegation_depth_limit"] == "passed"


async def test_restart_after_pause_recovers_persisted_workflow(make_runtime):
    state: dict[str, object] = {"paused": False}

    def pause_once(prompt: str):
        runtime = state["runtime"]
        if not state["paused"]:
            state["paused"] = True
            latest = runtime.list_workflows(limit=1)
            if latest:
                runtime.pause_workflow(latest[0].workflow_id)
            return {"action": "complete", "content": "pre-pause response"}
        return {"action": "complete", "content": "completed after restart"}

    first = make_runtime(provider=_FunctionProvider(pause_once))
    state["runtime"] = first
    workflow = await first.submit_goal("Pause then recover after restart")
    assert workflow.status == WorkflowStatus.PAUSED
    await first.aclose()

    second = make_runtime(provider=_FunctionProvider(lambda prompt: {
        "action": "complete", "content": "completed after restart"
    }))
    resumed = await second.resume_workflow(workflow.workflow_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert "restart" in (resumed.final_result or "").lower()


class _FunctionProvider(MockProvider):
    def __init__(self, execute_fn) -> None:
        super().__init__()
        self.execute_fn = execute_fn

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        if purpose == "plan":
            return await super().complete(
                messages, purpose=purpose, temperature=temperature, max_tokens=max_tokens
            )
        if purpose and purpose.startswith("execute:"):
            return _response(json.dumps(self.execute_fn("")))
        return await super().complete(
            messages, purpose=purpose, temperature=temperature, max_tokens=max_tokens
        )


@pytest.mark.docker
async def test_docker_container_death_is_recovered_by_restart(tmp_path):
    config = SandboxConfig(
        image="python:3.11-slim",
        workspace_root=tmp_path / "workspaces",
        timeout_seconds=20,
        max_code_bytes=100_000,
    )
    sandbox = DockerSandbox("reliability", config)
    try:
        await sandbox.start()
        first = await sandbox.execute_python("print('first')")
        assert first.success and "first" in first.stdout
        subprocess.run(
            ["docker", "rm", "-f", sandbox.container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        second = await sandbox.execute_python("print('second')")
        assert second.success
        assert "second" in second.stdout
    finally:
        await sandbox.stop()
