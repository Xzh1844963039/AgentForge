"""Python execution tools backed by the Docker sandbox.

python_execute and python_test never fall back to host execution. If Docker is
unavailable they return an explicit error. python_syntax_check performs static
parsing only (no code execution).
"""

from __future__ import annotations

import ast

from pydantic import BaseModel, ConfigDict, Field

from agentforge.sandbox.base import (
    SandboxError,
    SandboxResult,
    SandboxUnavailableError,
)
from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolResult,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PythonExecuteArgs(_Args):
    code: str = Field(min_length=1, max_length=512_000)
    filename: str = Field(default="snippet.py", max_length=96)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)


class PythonExecuteOutput(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float | None
    script_path: str
    output_truncated: bool


class PythonTestArgs(_Args):
    code: str = Field(min_length=1, max_length=512_000)
    filename: str = Field(default="test_snippet.py", max_length=96)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)


class PythonSyntaxCheckArgs(_Args):
    code: str = Field(min_length=1, max_length=512_000)
    filename: str = Field(default="snippet.py", max_length=96)


class PythonSyntaxCheckOutput(BaseModel):
    valid: bool
    error: str | None = None
    line: int | None = None
    column: int | None = None


def _sandbox_result(result: SandboxResult, script_path: str) -> ToolResult:
    payload = PythonExecuteOutput(
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
        script_path=script_path,
        output_truncated=bool(result.metadata.get("output_truncated")),
    )
    if result.success:
        return ToolResult.ok(payload.model_dump(), metadata=result.metadata)
    if result.timed_out:
        error = "python execution timed out"
    else:
        error = f"python exited with code {result.exit_code}"
    return ToolResult.fail(error, output=payload.model_dump(), metadata=result.metadata)


class PythonExecuteTool(BaseTool):
    name = "python_execute"
    description = (
        "Write a Python file into the agent's private sandbox workspace and "
        "execute it inside the isolated Docker container. Returns stdout, stderr "
        "and exit code. Never executes on the host."
    )
    args_model = PythonExecuteArgs
    output_model = PythonExecuteOutput
    permissions = [ToolPermission.PYTHON_EXECUTE]
    timeout_seconds = 60
    max_output_bytes = 65_536

    async def run(
        self, arguments: PythonExecuteArgs, context: ToolExecutionContext
    ) -> ToolResult:
        sandbox = context.sandbox
        if sandbox is None:
            return ToolResult.fail("no sandbox is configured for this agent")
        script_path = f"scripts/{arguments.filename}"
        try:
            result = await sandbox.execute_python(
                arguments.code,
                filename=arguments.filename,
                timeout_seconds=arguments.timeout_seconds,
            )
        except SandboxUnavailableError as exc:
            return ToolResult.fail(
                f"Docker sandbox unavailable: {exc}",
                metadata={"sandbox_unavailable": True},
            )
        except SandboxError as exc:
            return ToolResult.fail(f"sandbox error: {exc}")
        return _sandbox_result(result, script_path)


class PythonTestTool(BaseTool):
    name = "python_test"
    description = (
        "Run unittest-style Python test code in the agent's isolated Docker "
        "sandbox and return stdout, stderr and exit code."
    )
    args_model = PythonTestArgs
    output_model = PythonExecuteOutput
    permissions = [ToolPermission.PYTHON_EXECUTE]
    timeout_seconds = 60
    max_output_bytes = 65_536

    async def run(
        self, arguments: PythonTestArgs, context: ToolExecutionContext
    ) -> ToolResult:
        sandbox = context.sandbox
        if sandbox is None:
            return ToolResult.fail("no sandbox is configured for this agent")
        filename = arguments.filename
        if not filename.startswith("test_"):
            filename = "test_" + filename
        script_path = f"tests/{filename}"
        try:
            result = await sandbox.execute_python(
                arguments.code,
                filename=filename,
                subdir="tests",
                timeout_seconds=arguments.timeout_seconds,
            )
        except SandboxUnavailableError as exc:
            return ToolResult.fail(
                f"Docker sandbox unavailable: {exc}",
                metadata={"sandbox_unavailable": True},
            )
        except SandboxError as exc:
            return ToolResult.fail(f"sandbox error: {exc}")
        return _sandbox_result(result, script_path)


class PythonSyntaxCheckTool(BaseTool):
    name = "python_syntax_check"
    description = (
        "Statically parse Python source and report syntax errors without "
        "executing the code."
    )
    args_model = PythonSyntaxCheckArgs
    output_model = PythonSyntaxCheckOutput
    permissions = [ToolPermission.PYTHON_EXECUTE]
    timeout_seconds = 10

    async def run(
        self, arguments: PythonSyntaxCheckArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            ast.parse(arguments.code, filename=arguments.filename)
        except SyntaxError as exc:
            payload = PythonSyntaxCheckOutput(
                valid=False,
                error=f"{exc.msg}",
                line=exc.lineno,
                column=exc.offset,
            )
            return ToolResult.fail(
                f"syntax error at line {exc.lineno}: {exc.msg}",
                output=payload.model_dump(),
            )
        return ToolResult.ok(
            PythonSyntaxCheckOutput(valid=True).model_dump(),
            metadata={"static_check": True},
        )


def python_tools() -> list[BaseTool]:
    return [PythonExecuteTool(), PythonTestTool(), PythonSyntaxCheckTool()]
