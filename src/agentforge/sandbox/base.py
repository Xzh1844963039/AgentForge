"""Sandbox contracts for isolated per-agent execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class SandboxStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class SandboxUnavailableError(RuntimeError):
    """Docker is missing, the daemon is down, or permissions are insufficient."""


class SandboxError(RuntimeError):
    """Sandbox lifecycle or execution failure."""


class SandboxResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float | None = None
    timed_out: bool = False
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SandboxConfig(BaseModel):
    image: str = "python:3.11-slim"
    workspace_root: Path = Path("data/workspaces")
    cpu_limit: float = Field(default=1.0, gt=0, le=16)
    memory_limit_mb: int = Field(default=256, ge=64, le=8192)
    pids_limit: int = Field(default=64, ge=16, le=4096)
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    output_limit_bytes: int = Field(default=65_536, ge=1024, le=10_000_000)
    network_enabled: bool = False
    container_user: str = "65534:65534"
    read_only_root: bool = True
    tmpfs_size_mb: int = Field(default=64, ge=8, le=1024)
    max_code_bytes: int = Field(default=512_000, ge=1024)
    name_prefix: str = "agentforge-sandbox"

    @model_validator(mode="after")
    def _reject_root_container_user(self) -> "SandboxConfig":
        user = self.container_user.strip().lower()
        if user in {"0", "0:0", "root", "root:root", "0:root", "root:0"}:
            raise ValueError("the sandbox container user must not be root")
        return self

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_root)


class BaseSandbox(ABC):
    """Per-agent isolated execution environment."""

    def __init__(self, agent_id: str, config: SandboxConfig) -> None:
        self.agent_id = agent_id
        self.config = config
        self.status = SandboxStatus.CREATED
        self.last_error: str | None = None

    @abstractmethod
    async def start(self) -> SandboxStatus:
        """Create and start the sandbox."""

    @abstractmethod
    async def stop(self) -> SandboxStatus:
        """Stop and remove the sandbox."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True when the sandbox is running and usable."""

    @abstractmethod
    async def execute_python(
        self,
        code: str,
        *,
        filename: str = "snippet.py",
        subdir: str = "scripts",
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        """Write code into the private workspace and execute it in the sandbox."""

    @abstractmethod
    async def execute_python_file(
        self,
        relative_path: str,
        *,
        args: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        """Execute an existing .py file inside the private workspace."""

    @abstractmethod
    async def write_text_file(self, relative_path: str, content: str) -> SandboxResult:
        """Write a .md or .txt file inside the agent workspace."""

    @abstractmethod
    async def read_text_file(self, relative_path: str) -> SandboxResult:
        """Read a .md or .txt file inside the agent workspace."""

    async def restart(self) -> SandboxStatus:
        await self.stop()
        return await self.start()

    async def cleanup(self) -> None:
        await self.stop()
