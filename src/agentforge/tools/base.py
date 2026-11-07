"""Tool abstraction and registry.

Phase 1 defines the contract only. Web search, Python execution, file access
and GitHub tools are added in later phases behind this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    """A capability an agent may invoke.

    Concrete tools must declare a spec and implement ``run``. Tool execution is
    synchronous by design; long-running or sandboxed work will be wrapped by an
    async sandbox adapter in Phase 3.
    """

    spec: ToolSpec

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated keyword arguments."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool {tool.spec.name!r} is already registered")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool {name!r}") from exc

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def prompt_catalog(self) -> str:
        if not self._tools:
            return "(no tools are enabled in Phase 1)"
        lines = []
        for tool in self._tools.values():
            lines.append(f"- {tool.spec.name}: {tool.spec.description}")
        return "\n".join(lines)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
