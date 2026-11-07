"""Python tool tests (static, fake sandbox, Docker-gated integration)."""

from __future__ import annotations

import pytest

from agentforge.models.tool import ToolCall, ToolResultStatus
from agentforge.persistence import EventLogger, SQLiteStore
from agentforge.sandbox.base import SandboxResult, SandboxUnavailableError
from agentforge.sandbox.docker import DockerSandbox
from agentforge.tools.approval import AutoApprovalGate
from agentforge.tools.base import ToolExecutionContext, ToolRegistry
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.policy import ToolPermissionPolicy
from agentforge.tools.python_tools import (
    PythonExecuteTool,
    PythonSyntaxCheckTool,
    PythonTestTool,
)

DOCKER_AVAILABLE, DOCKER_REASON = DockerSandbox.check_available()
requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason=f"Docker unavailable: {DOCKER_REASON}"
)


class FakeSandbox:
    def __init__(
        self,
        *,
        success: bool = True,
        stdout: str = "ok\n",
        stderr: str = "",
        exit_code: int = 0,
        timed_out: bool = False,
    ) -> None:
        self.result = SandboxResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=0.01,
            timed_out=timed_out,
            metadata={"output_truncated": False},
        )
        self.calls: list[dict] = []

    async def execute_python(self, code, **kwargs):
        self.calls.append({"code": code, **kwargs})
        return self.result


class UnavailableSandbox:
    async def execute_python(self, code, **kwargs):
        raise SandboxUnavailableError("docker CLI not found")


def context(tmp_path, sandbox):
    return ToolExecutionContext(agent_id="coding", workspace=tmp_path, sandbox=sandbox)


async def test_syntax_check_valid_and_invalid():
    tool = PythonSyntaxCheckTool()
    good = await tool.execute(
        {"code": "def f():\n    return 1\n"}, context(None, None)
    )
    assert good.status is ToolResultStatus.SUCCESS
    assert good.output == {"valid": True, "error": None, "line": None, "column": None}

    bad = await tool.execute(
        {"code": "def f(:\n    pass\n"}, context(None, None)
    )
    assert bad.status is ToolResultStatus.ERROR
    assert bad.output["valid"] is False
    assert bad.output["line"] == 1


async def test_python_execute_success_and_failure(tmp_path):
    tool = PythonExecuteTool()
    sandbox = FakeSandbox(stdout="primes\n", exit_code=0)
    result = await tool.execute(
        {"code": "print('primes')", "filename": "primes.py"},
        context(tmp_path, sandbox),
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output["stdout"] == "primes\n"
    assert result.output["exit_code"] == 0
    assert result.output["script_path"] == "scripts/primes.py"
    assert sandbox.calls[0]["subdir"] if "subdir" in sandbox.calls[0] else True

    failing = FakeSandbox(
        success=False, stdout="", stderr="NameError", exit_code=1
    )
    result = await tool.execute(
        {"code": "raise NameError", "filename": "bad.py"},
        context(tmp_path, failing),
    )
    assert result.status is ToolResultStatus.ERROR
    assert result.output["exit_code"] == 1
    assert result.output["stderr"] == "NameError"
    assert "exited with code 1" in (result.error or "")


async def test_python_execute_timeout(tmp_path):
    tool = PythonExecuteTool()
    sandbox = FakeSandbox(
        success=False, stdout="", exit_code=124, timed_out=True
    )
    result = await tool.execute(
        {"code": "while True: pass"}, context(tmp_path, sandbox)
    )
    assert result.status is ToolResultStatus.ERROR
    assert result.output["timed_out"] is True
    assert "timed out" in (result.error or "")


async def test_python_test_uses_tests_subdir(tmp_path):
    tool = PythonTestTool()
    sandbox = FakeSandbox(stdout="OK\n")
    result = await tool.execute(
        {
            "code": "import unittest\nclass T(unittest.TestCase):\n    pass\n",
            "filename": "test_primes.py",
        },
        context(tmp_path, sandbox),
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output["script_path"] == "tests/test_primes.py"
    assert sandbox.calls[0]["subdir"] == "tests"
    assert sandbox.calls[0]["filename"] == "test_primes.py"


async def test_missing_sandbox_is_explicit_error(tmp_path):
    tool = PythonExecuteTool()
    result = await tool.execute({"code": "print(1)"}, context(tmp_path, None))
    assert result.status is ToolResultStatus.ERROR
    assert "no sandbox" in (result.error or "")


async def test_sandbox_unavailable_does_not_execute_on_host(tmp_path):
    sentinel = tmp_path / "should_not_exist.txt"
    code = f"open({str(sentinel)!r}, 'w', encoding='utf-8').write('hosted')\n"
    tool = PythonExecuteTool()
    result = await tool.execute(
        {"code": code, "filename": "sentinel.py"},
        context(tmp_path, UnavailableSandbox()),
    )
    assert result.status is ToolResultStatus.ERROR
    assert "Docker sandbox unavailable" in (result.error or "")
    assert not sentinel.exists()


async def test_python_tool_through_dispatcher(tmp_path):
    store = SQLiteStore(tmp_path / "python.db")
    events = EventLogger(tmp_path / "events.jsonl")
    registry = ToolRegistry()
    registry.register(PythonExecuteTool())
    executor = ToolExecutor(
        registry,
        store,
        events,
        ToolPermissionPolicy(),
        AutoApprovalGate(approved=True),
    )
    tool_context = ToolExecutionContext(
        agent_id="coding",
        workflow_id="wf",
        task_id="task",
        workspace=tmp_path,
        sandbox=FakeSandbox(stdout="42\n"),
    )
    result = await executor.execute(
        ToolCall(name="python_execute", arguments={"code": "print(42)"}),
        tool_context,
    )
    assert result.status is ToolResultStatus.SUCCESS
    records = store.list_tool_calls("wf")
    assert records[0].tool_name == "python_execute"
    assert records[0].permissions == ["python_execute"]
    events.close()
    store.close()


@pytest.mark.docker
@requires_docker
async def test_python_execute_docker_integration(tmp_path):
    from agentforge.sandbox.base import SandboxConfig
    from agentforge.tools.workspace import WorkspaceManager

    workspaces = WorkspaceManager(tmp_path / "workspaces")
    sandbox = DockerSandbox(
        "coding",
        SandboxConfig(workspace_root=tmp_path / "workspaces"),
        workspaces=workspaces,
    )
    try:
        tool = PythonExecuteTool()
        result = await tool.execute(
            {
                "code": (
                    "def primes(limit=20):\n"
                    "    return [n for n in range(2, limit + 1) if all(n % d for d in range(2, int(n ** 0.5) + 1))]\n"
                    "print('Primes up to 20:', primes(20))\n"
                ),
                "filename": "primes.py",
            },
            ToolExecutionContext(
                agent_id="coding",
                workspace=workspaces.workspace_for("coding"),
                sandbox=sandbox,
            ),
        )
        assert result.status is ToolResultStatus.SUCCESS, result.error
        assert result.output["stdout"].strip() == "Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]"
        assert result.output["exit_code"] == 0
    finally:
        await sandbox.stop()
