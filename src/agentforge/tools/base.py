"""Tool contracts, execution context and registry.

Phase 2 tools are structured capabilities: JSON-schema-validated input/output,
explicit permissions, timeouts, and a unified dispatcher. Agents never execute
tools directly; they request a tool call and receive the dispatcher's result.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from agentforge.models.tool import ToolResultStatus


class ToolPermission(str, Enum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    PYTHON_EXECUTE = "python_execute"
    NETWORK_READ = "network_read"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    GIT_PUSH = "git_push"
    ARTIFACT_READ = "artifact_read"
    ARTIFACT_WRITE = "artifact_write"


class ToolSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15.0, gt=0, le=600)
    permissions: list[ToolPermission] = Field(default_factory=list)
    requires_approval: bool = False
    untrusted_output: bool = False
    max_output_bytes: int | None = Field(default=None, ge=1024)


class ToolResult(BaseModel):
    """Structured result returned to an agent by the dispatcher."""

    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == ToolResultStatus.SUCCESS

    @classmethod
    def ok(
        cls,
        output: dict[str, Any],
        *,
        duration_seconds: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            status=ToolResultStatus.SUCCESS,
            output=output,
            duration_seconds=duration_seconds,
            metadata=metadata or {},
        )

    @classmethod
    def fail(
        cls,
        error: str,
        *,
        status: ToolResultStatus = ToolResultStatus.ERROR,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            status=status,
            output=output or {},
            error=error,
            metadata=metadata or {},
        )


@dataclass
class ToolExecutionContext:
    """Runtime context supplied to a tool by the dispatcher."""

    agent_id: str
    workflow_id: str | None = None
    task_id: str | None = None
    workspace: Path | None = None
    sandbox: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Base class for a capability with a validated JSON contract."""

    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel] | None] = None
    permissions: ClassVar[list[ToolPermission]] = []
    timeout_seconds: ClassVar[float] = 15.0
    requires_approval: ClassVar[bool] = False
    untrusted_output: ClassVar[bool] = False
    max_output_bytes: ClassVar[int | None] = None

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.args_model.model_json_schema(),
            output_schema=(
                self.output_model.model_json_schema() if self.output_model else {}
            ),
            timeout_seconds=self.timeout_seconds,
            permissions=list(self.permissions),
            requires_approval=self.requires_approval,
            untrusted_output=self.untrusted_output,
            max_output_bytes=self.max_output_bytes,
        )

    @abstractmethod
    async def run(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> ToolResult:
        """Execute the tool with validated arguments."""

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        parsed = self.args_model.model_validate(arguments)
        return await self.run(parsed, context)


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool {tool.spec.name!r} is already registered")
        if not isinstance(tool, BaseTool):
            raise TypeError("only BaseTool instances can be registered")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool {name!r}") from exc

    def spec(self, name: str) -> ToolSpec:
        return self.get(name).spec

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def prompt_catalog(self) -> str:
        if not self._tools:
            return "(no tools are enabled)"
        lines: list[str] = []
        for tool in self._tools.values():
            spec = tool.spec
            schema = json.dumps(spec.input_schema, ensure_ascii=False, separators=(",", ":"))
            if len(schema) > 1400:
                schema = schema[:1400] + "...(truncated)"
            perms = ", ".join(permission.value for permission in spec.permissions)
            lines.append(
                f"- {spec.name}: {spec.description}\n"
                f"  permissions: {perms or 'none'}; timeout: {spec.timeout_seconds:g}s; "
                f"requires_approval: {str(spec.requires_approval).lower()}\n"
                f"  input_schema: {schema}"
            )
        return "\n".join(lines)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
