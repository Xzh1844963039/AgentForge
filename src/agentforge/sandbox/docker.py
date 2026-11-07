"""Docker-backed sandbox with strict isolation defaults.

The implementation uses the Docker CLI with argument lists (never a shell).
Each agent gets its own named container and bind-mounted private workspace.
Containers run as a non-root user, without network, Linux capabilities, or a
Docker socket, and with CPU/memory/PID/time limits.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass

from agentforge.sandbox.base import (
    BaseSandbox,
    SandboxConfig,
    SandboxError,
    SandboxResult,
    SandboxStatus,
    SandboxUnavailableError,
)
from agentforge.tools.workspace import (
    CODE_EXTENSIONS,
    TEXT_EXTENSIONS,
    WorkspaceError,
    WorkspaceManager,
)

logger = logging.getLogger("agentforge.sandbox.docker")

_SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.py$")


@dataclass
class CommandOutput:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    output_truncated: bool = False


async def _read_limited(
    stream: asyncio.StreamReader, limit: int
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        if total < limit:
            take = chunk[: limit - total]
            chunks.append(take)
            total += len(take)
        else:
            truncated = True
    return b"".join(chunks), truncated


class DockerCommandRunner:
    """Async Docker CLI runner with output limits and timeout kill."""

    async def run(
        self,
        args: list[str],
        *,
        timeout: float,
        env: dict[str, str] | None = None,
        output_limit: int = 65_536,
    ) -> CommandOutput:
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env or os.environ.copy(),
        )
        stdout_task = asyncio.create_task(
            _read_limited(process.stdout, output_limit) if process.stdout else _empty()
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, output_limit) if process.stderr else _empty()
        )
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        stdout_raw, stdout_truncated = await stdout_task
        stderr_raw, stderr_truncated = await stderr_task
        return CommandOutput(
            stdout=stdout_raw.decode("utf-8", errors="replace"),
            stderr=stderr_raw.decode("utf-8", errors="replace"),
            exit_code=process.returncode,
            timed_out=timed_out,
            duration_seconds=max(0.0, time.perf_counter() - started),
            output_truncated=stdout_truncated or stderr_truncated,
        )


async def _empty() -> tuple[bytes, bool]:
    return b"", False


class DockerSandbox(BaseSandbox):
    """One isolated Docker container per agent."""

    def __init__(
        self,
        agent_id: str,
        config: SandboxConfig,
        *,
        workspaces: WorkspaceManager | None = None,
        runner: DockerCommandRunner | None = None,
        preflight: bool = True,
    ) -> None:
        super().__init__(agent_id, config)
        self.workspaces = workspaces or WorkspaceManager(config.workspace_root)
        self.workspace = self.workspaces.workspace_for(agent_id)
        digest = hashlib.sha256(
            str(self.workspace.resolve()).encode("utf-8")
        ).hexdigest()[:8]
        self.container_name = f"{config.name_prefix}-{agent_id}-{digest}"
        self.runner = runner or DockerCommandRunner()
        self.preflight = preflight
        self._daemon_checked = False

    # ---------------------------------------------------------------- availability
    @classmethod
    def check_available(cls, timeout: float = 8.0) -> tuple[bool, str]:
        binary = shutil.which("docker")
        if binary is None:
            return (
                False,
                "docker CLI not found on PATH. Install Docker Desktop with WSL2 "
                "and ensure 'docker' is available.",
            )
        import subprocess

        try:
            completed = subprocess.run(
                [binary, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            return False, f"docker daemon check failed: {type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            return (
                False,
                "docker daemon is not available: "
                + (completed.stderr or completed.stdout).strip()[:500],
            )
        return True, completed.stdout.strip() or "docker daemon ready"

    async def _preflight(self) -> None:
        if not self.preflight:
            return
        available, message = await asyncio.to_thread(self.check_available)
        if not available:
            raise SandboxUnavailableError(message)
        self._daemon_checked = True

    # --------------------------------------------------------------------- lifecycle
    def _docker(self) -> list[str]:
        return [shutil.which("docker") or "docker"]

    def build_run_args(self) -> list[str]:
        mount = (
            f"type=bind,source={self.workspace.resolve()},target=/workspace"
        )
        args = [
            *self._docker(),
            "run",
            "-d",
            "--name",
            self.container_name,
            "--init",
            "--pull=missing",
            "--network",
            "bridge" if self.config.network_enabled else "none",
            "--cpus",
            f"{self.config.cpu_limit:g}",
            "--memory",
            f"{self.config.memory_limit_mb}m",
            "--memory-swap",
            f"{self.config.memory_limit_mb}m",
            "--pids-limit",
            str(self.config.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            self.config.container_user,
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "HOME=/tmp",
            "--label",
            f"agentforge.agent_id={self.agent_id}",
            "--mount",
            mount,
        ]
        if self.config.read_only_root:
            args.append("--read-only")
        args.extend(
            [
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,size={self.config.tmpfs_size_mb}m",
                self.config.image,
                "sleep",
                "infinity",
            ]
        )
        return args

    async def start(self) -> SandboxStatus:
        if self.status == SandboxStatus.RUNNING and await self.health_check():
            return self.status
        try:
            await self._preflight()
            result = await self.runner.run(
                self._docker() + ["rm", "-f", self.container_name],
                timeout=20,
            )
            if result.timed_out:
                raise SandboxError("timed out while removing a stale sandbox")
            result = await self.runner.run(
                self.build_run_args(),
                timeout=max(120.0, self.config.timeout_seconds * 2),
                output_limit=32_768,
            )
            if result.timed_out:
                raise SandboxError("timed out while starting the Docker container")
            if result.exit_code != 0:
                raise SandboxError(
                    "failed to start Docker sandbox: "
                    + (result.stderr or result.stdout).strip()[:800]
                )
            inspect = await self.runner.run(
                self._docker()
                + ["inspect", "-f", "{{.State.Running}}", self.container_name],
                timeout=15,
            )
            if inspect.exit_code != 0 or inspect.stdout.strip().lower() != "true":
                raise SandboxError(
                    "Docker container did not reach the running state: "
                    + (inspect.stderr or inspect.stdout).strip()[:500]
                )
            self.status = SandboxStatus.RUNNING
            self.last_error = None
            return self.status
        except (SandboxUnavailableError, SandboxError):
            self.status = SandboxStatus.FAILED
            raise
        except Exception as exc:
            self.status = SandboxStatus.FAILED
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise SandboxError(self.last_error) from exc

    async def stop(self) -> SandboxStatus:
        if shutil.which("docker") is None:
            self.status = SandboxStatus.STOPPED
            return self.status
        try:
            result = await self.runner.run(
                self._docker() + ["rm", "-f", self.container_name],
                timeout=25,
            )
            if (
                result.exit_code != 0
                and "no such container" not in (result.stderr or "").lower()
            ):
                logger.warning(
                    "docker rm reported an error for %s: %s",
                    self.container_name,
                    result.stderr.strip()[:300],
                )
        except FileNotFoundError:
            logger.warning("docker CLI disappeared before sandbox cleanup")
        except Exception:
            logger.exception("failed to stop sandbox %s", self.container_name)
        finally:
            self.status = SandboxStatus.STOPPED
        return self.status

    async def health_check(self) -> bool:
        if self.status != SandboxStatus.RUNNING:
            return False
        result = await self.runner.run(
            self._docker()
            + ["inspect", "-f", "{{.State.Running}}", self.container_name],
            timeout=10,
        )
        return (
            result.exit_code == 0
            and result.stdout.strip().lower() == "true"
            and not result.timed_out
        )

    # ------------------------------------------------------------------ python
    async def execute_python(
        self,
        code: str,
        *,
        filename: str = "snippet.py",
        subdir: str = "scripts",
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        if not _SCRIPT_NAME_RE.match(filename):
            raise SandboxError(
                "filename must be a simple .py file name without directories"
            )
        if subdir not in {"scripts", "tests"}:
            raise SandboxError("subdir must be 'scripts' or 'tests'")
        encoded = code.encode("utf-8")
        if len(encoded) > self.config.max_code_bytes:
            raise SandboxError(
                f"code exceeds the {self.config.max_code_bytes}-byte limit"
            )
        script_rel = f"{subdir}/{filename}"
        try:
            self.workspaces.atomic_write(
                self.agent_id,
                script_rel,
                code,
                max_bytes=self.config.max_code_bytes,
                allowed_extensions=CODE_EXTENSIONS,
                overwrite=True,
            )
        except WorkspaceError as exc:
            raise SandboxError(str(exc)) from exc
        return await self._execute_script(
            script_rel, timeout_seconds=timeout_seconds
        )

    async def execute_python_file(
        self,
        relative_path: str,
        *,
        args: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        try:
            self.workspaces.secure_path(
                self.agent_id,
                relative_path,
                allowed_extensions=CODE_EXTENSIONS,
                must_exist=True,
            )
        except WorkspaceError as exc:
            raise SandboxError(str(exc)) from exc
        return await self._execute_script(
            relative_path,
            args=args or [],
            timeout_seconds=timeout_seconds,
        )

    async def _execute_script(
        self,
        relative_path: str,
        *,
        args: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        if args:
            if len(args) > 50 or any(
                len(argument) > 500 or "\x00" in argument for argument in args
            ):
                raise SandboxError("invalid script arguments")
        await self.start()
        timeout = max(
            1,
            min(
                timeout_seconds or self.config.timeout_seconds,
                self.config.timeout_seconds,
            ),
        )
        container_script = "/workspace/" + relative_path
        command = [
            *self._docker(),
            "exec",
            "--user",
            self.config.container_user,
            "--workdir",
            "/workspace",
            self.container_name,
            "timeout",
            "--signal=TERM",
            "--kill-after=2s",
            f"{timeout}s",
            "python",
            container_script,
            *(args or []),
        ]
        result = await self.runner.run(
            command,
            timeout=timeout + 5,
            output_limit=self.config.output_limit_bytes,
        )
        timed_out = result.timed_out or result.exit_code == 124
        if result.timed_out or result.exit_code == 124:
            # The process hit the sandbox time limit (or the outer wait timed
            # out). Remove the container so no orphaned code continues running.
            await self.stop()
        success = result.exit_code == 0 and not timed_out
        return SandboxResult(
            success=success,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            timed_out=timed_out,
            metadata={
                "container": self.container_name,
                "script": relative_path,
                "output_truncated": result.output_truncated,
            },
        )

    # -------------------------------------------------------------------- files
    async def write_text_file(self, relative_path: str, content: str) -> SandboxResult:
        try:
            path, size, digest = self.workspaces.atomic_write(
                self.agent_id,
                relative_path,
                content,
                max_bytes=256 * 1024,
                allowed_extensions=TEXT_EXTENSIONS,
                overwrite=True,
            )
        except WorkspaceError as exc:
            raise SandboxError(str(exc)) from exc
        return SandboxResult(
            success=True,
            stdout=f"wrote {relative_path}",
            metadata={"path": relative_path, "size_bytes": size, "sha256": digest},
        )

    async def read_text_file(self, relative_path: str) -> SandboxResult:
        try:
            content, size, digest, truncated = self.workspaces.read_text(
                self.agent_id,
                relative_path,
                max_bytes=256 * 1024,
                allowed_extensions=TEXT_EXTENSIONS,
            )
        except WorkspaceError as exc:
            raise SandboxError(str(exc)) from exc
        return SandboxResult(
            success=True,
            stdout=content,
            metadata={
                "path": relative_path,
                "size_bytes": size,
                "sha256": digest,
                "truncated": truncated,
            },
        )
