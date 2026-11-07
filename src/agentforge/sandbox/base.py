"""Sandbox interfaces.

Phase 1 deliberately does not execute code or isolate filesystems. The contract
below is the extension point for the Docker-backed implementation in Phase 3.

TODO (Phase 3):
- one container / private workspace per agent,
- CPU, memory, wall-clock and network quotas,
- Markdown (.md) and plain-text (.txt) file operations only,
- explicit human approval for writes outside the agent workspace.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class SandboxStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class SandboxResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float | None = None
    artifacts: list[str] = Field(default_factory=list)


class SandboxConfig(BaseModel):
    image: str = "python:3.11-slim"
    workspace_root: Path = Path("data/workspaces")
    cpu_limit: float = 1.0
    memory_limit_mb: int = 512
    timeout_seconds: int = 30
    network_enabled: bool = False


class BaseSandbox(ABC):
    """Per-agent isolated execution environment contract."""

    def __init__(self, agent_id: str, config: SandboxConfig) -> None:
        self.agent_id = agent_id
        self.config = config

    @abstractmethod
    async def start(self) -> SandboxStatus:
        """Create and start the sandbox."""

    @abstractmethod
    async def stop(self) -> SandboxStatus:
        """Stop and remove the sandbox."""

    @abstractmethod
    async def execute_python(self, code: str) -> SandboxResult:
        """Execute Python inside the sandbox."""

    @abstractmethod
    async def write_text_file(self, relative_path: str, content: str) -> SandboxResult:
        """Write a .md or .txt file inside the agent workspace."""

    @abstractmethod
    async def read_text_file(self, relative_path: str) -> SandboxResult:
        """Read a .md or .txt file inside the agent workspace."""


class DockerSandbox(BaseSandbox):
    """Phase 3 placeholder.

    Instantiating this class is allowed so the interface can be exercised, but
    every operation raises ``NotImplementedError`` explicitly. No sandbox
    isolation is claimed in Phase 1.
    """

    async def start(self) -> SandboxStatus:
        raise NotImplementedError("Docker sandbox is planned for Phase 3")

    async def stop(self) -> SandboxStatus:
        raise NotImplementedError("Docker sandbox is planned for Phase 3")

    async def execute_python(self, code: str) -> SandboxResult:
        raise NotImplementedError("Python execution sandbox is planned for Phase 3")

    async def write_text_file(self, relative_path: str, content: str) -> SandboxResult:
        raise NotImplementedError("Private file workspace is planned for Phase 3")

    async def read_text_file(self, relative_path: str) -> SandboxResult:
        raise NotImplementedError("Private file workspace is planned for Phase 3")
