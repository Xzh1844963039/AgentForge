"""Tool registry, dispatcher, schema, permission, timeout and audit tests."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict, Field

from agentforge.models.tool import (
    ApprovalStatus,
    ToolCall,
    ToolResultStatus,
)
from agentforge.persistence import EventLogger, SQLiteStore
from agentforge.tools.approval import AutoApprovalGate, PendingApprovalGate
from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolRegistry,
    ToolResult,
)
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.policy import ToolPermissionPolicy
from agentforge.tools.workspace import WorkspaceManager


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EchoArgs(_Args):
    text: str = Field(min_length=1)


class EchoOutput(BaseModel):
    text: str
    length: int


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo text."
    args_model = EchoArgs
    output_model = EchoOutput
    timeout_seconds = 2

    async def run(self, arguments: EchoArgs, context: ToolExecutionContext) -> ToolResult:
        return ToolResult.ok(
            EchoOutput(text=arguments.text, length=len(arguments.text)).model_dump()
        )


class SlowArgs(_Args):
    seconds: float = 0.5


class SlowOutput(BaseModel):
    completed: bool


class SlowTool(BaseTool):
    name = "slow_tool"
    description = "Sleep for a long time."
    args_model = SlowArgs
    output_model = SlowOutput
    timeout_seconds = 0.1

    async def run(self, arguments: SlowArgs, context: ToolExecutionContext) -> ToolResult:
        await asyncio.sleep(arguments.seconds)
        return ToolResult.ok(SlowOutput(completed=True).model_dump())


class FailArgs(_Args):
    message: str = "boom"


class FailTool(BaseTool):
    name = "fail_tool"
    description = "Always fail."
    args_model = FailArgs
    timeout_seconds = 2

    async def run(self, arguments: FailArgs, context: ToolExecutionContext) -> ToolResult:
        raise ValueError(arguments.message)


class BadOutputTool(BaseTool):
    name = "bad_output"
    description = "Return output that violates its schema."
    args_model = _Args
    output_model = EchoOutput
    timeout_seconds = 2

    async def run(self, arguments: _Args, context: ToolExecutionContext) -> ToolResult:
        return ToolResult.ok({"unexpected": True})


class ApprovalTool(BaseTool):
    name = "approval_tool"
    description = "A destructive test tool."
    args_model = _Args
    timeout_seconds = 2
    permissions = [ToolPermission.FILE_DELETE]
    requires_approval = True
    calls = 0

    async def run(self, arguments: _Args, context: ToolExecutionContext) -> ToolResult:
        type(self).calls += 1
        return ToolResult.ok({"executed": True})


class PermissionTool(BaseTool):
    name = "permission_tool"
    description = "Requires python execution."
    args_model = _Args
    timeout_seconds = 2
    permissions = [ToolPermission.PYTHON_EXECUTE]

    async def run(self, arguments: _Args, context: ToolExecutionContext) -> ToolResult:
        return ToolResult.ok({"executed": True})


class HugeTool(BaseTool):
    name = "huge_tool"
    description = "Return a large output."
    args_model = _Args
    timeout_seconds = 2

    async def run(self, arguments: _Args, context: ToolExecutionContext) -> ToolResult:
        return ToolResult.ok({"text": "x" * 5000})


@pytest.fixture
def tool_env(tmp_path):
    store = SQLiteStore(tmp_path / "tools.db")
    events = EventLogger(tmp_path / "events.jsonl")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    registry = ToolRegistry()
    for tool in (
        EchoTool(),
        SlowTool(),
        FailTool(),
        BadOutputTool(),
        ApprovalTool(),
        PermissionTool(),
        HugeTool(),
    ):
        registry.register(tool)
    context = ToolExecutionContext(
        agent_id="coding",
        workflow_id="wf",
        task_id="task",
        workspace=workspaces.workspace_for("coding"),
    )
    yield store, events, workspaces, registry, context
    events.close()
    store.close()


def make_executor(store, events, registry, policy=None, gate=None, max_output=65_536):
    return ToolExecutor(
        registry,
        store,
        events,
        policy or ToolPermissionPolicy(),
        gate or AutoApprovalGate(approved=True),
        max_output_bytes=max_output,
    )


def call(name: str, arguments: dict | None = None, call_id: str | None = None) -> ToolCall:
    return ToolCall(call_id=call_id or f"call_{name}", name=name, arguments=arguments or {})


def test_registry_contract_and_duplicate(tool_env):
    store, events, workspaces, registry, context = tool_env
    assert "echo" in registry
    assert len(registry) == 7
    names = [spec.name for spec in registry.list_specs()]
    assert "echo" in names
    echo_spec = registry.spec("echo")
    assert echo_spec.input_schema["required"] == ["text"]
    assert "Available tools" not in registry.prompt_catalog()
    assert "echo" in registry.prompt_catalog()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())
    with pytest.raises(KeyError):
        registry.get("missing")


async def test_dispatch_success_and_audit(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)

    result = await executor.execute(call("echo", {"text": "hello"}), context)
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output == {"text": "hello", "length": 5}
    assert result.duration_seconds >= 0
    assert result.metadata["tool_call_id"] == "call_echo"

    records = store.list_tool_calls("wf")
    assert len(records) == 1
    record = records[0]
    assert record.tool_name == "echo"
    assert record.status is ToolResultStatus.SUCCESS
    assert record.agent_id == "coding"
    assert record.permission_decision.value == "allowed"
    assert record.approval_status is ApprovalStatus.NOT_REQUIRED


async def test_unknown_tool_returns_error(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    result = await executor.execute(call("missing"), context)
    assert result.status is ToolResultStatus.ERROR
    assert "unknown tool" in (result.error or "")
    assert store.list_tool_calls()[0].metadata["reason"] == "unknown_tool"


async def test_invalid_arguments_rejected(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    result = await executor.execute(call("echo", {"bad": "x"}), context)
    assert result.status is ToolResultStatus.ERROR
    assert "invalid tool arguments" in (result.error or "")
    result = await executor.execute(call("echo", {}), context)
    assert result.status is ToolResultStatus.ERROR


async def test_permission_denied(tool_env):
    store, events, workspaces, registry, context = tool_env
    policy = ToolPermissionPolicy(
        {"coding": {ToolPermission.FILE_READ}}
    )
    executor = make_executor(store, events, registry, policy=policy)
    result = await executor.execute(call("permission_tool"), context)
    assert result.status is ToolResultStatus.DENIED
    assert "lacks required permissions" in (result.error or "")
    record = store.list_tool_calls()[-1]
    assert record.permission_decision.value == "denied"
    assert "python_execute" in record.permissions


async def test_approval_pending_denied_and_approved(tool_env):
    store, events, workspaces, registry, context = tool_env
    ApprovalTool.calls = 0

    pending = make_executor(
        store, events, registry, gate=PendingApprovalGate()
    )
    result = await pending.execute(call("approval_tool"), context)
    assert result.status is ToolResultStatus.PENDING_APPROVAL
    assert ApprovalTool.calls == 0
    assert result.metadata["approval_status"] == "pending"

    denied = make_executor(
        store, events, registry, gate=AutoApprovalGate(approved=False)
    )
    result = await denied.execute(call("approval_tool", call_id="c2"), context)
    assert result.status is ToolResultStatus.DENIED
    assert ApprovalTool.calls == 0

    approved = make_executor(
        store, events, registry, gate=AutoApprovalGate(approved=True)
    )
    result = await approved.execute(call("approval_tool", call_id="c3"), context)
    assert result.status is ToolResultStatus.SUCCESS
    assert ApprovalTool.calls == 1
    assert result.metadata["approval_status"] == "approved"


async def test_timeout(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    result = await executor.execute(call("slow_tool", {"seconds": 0.5}), context)
    assert result.status is ToolResultStatus.TIMEOUT
    assert "timed out" in (result.error or "")
    assert store.list_tool_calls()[-1].status is ToolResultStatus.TIMEOUT


async def test_tool_exception_returns_error(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    result = await executor.execute(call("fail_tool", {"message": "boom"}), context)
    assert result.status is ToolResultStatus.ERROR
    assert "ValueError: boom" in (result.error or "")


async def test_output_schema_violation(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    result = await executor.execute(call("bad_output"), context)
    assert result.status is ToolResultStatus.ERROR
    assert "output failed schema validation" in (result.error or "")


async def test_output_truncation(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry, max_output=1024)
    result = await executor.execute(call("huge_tool"), context)
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output["truncated"] is True
    assert result.metadata["output_truncated"] is True
    assert len(result.output["preview"]) <= 1024


async def test_execute_many_sequential(tool_env):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    results = await executor.execute_many(
        [call("echo", {"text": "a"}, "c1"), call("echo", {"text": "b"}, "c2")],
        context,
    )
    assert [result.output["text"] for result in results] == ["a", "b"]


async def test_tool_calls_event_stream(tool_env, tmp_path):
    store, events, workspaces, registry, context = tool_env
    executor = make_executor(store, events, registry)
    await executor.execute(call("echo", {"text": "audit"}), context)
    records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(record["event"] == "tool_call" for record in records)
