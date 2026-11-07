"""Safe local Git tools.

Commands are executed with argument lists (never a shell), restricted to one
authorized repository root, with credential-bearing environment variables
scrubbed. ``git_push`` is gated by the dispatcher's approval policy.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolResult,
)
from agentforge.tools.workspace import WorkspaceError

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")
_SAFE_ENV_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "USERPROFILE",
    "HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
}
_CREDENTIAL_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "APIKEY",
    "AUTH",
    "CREDENTIAL",
    "DOCKER",
    "KUBE",
    "AWS_",
    "AZURE_",
    "GH_",
    "GITHUB_",
    "SSH_AUTH_SOCK",
)


class GitError(RuntimeError):
    pass


@dataclass
class GitCommandResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float


def _safe_git_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.upper() in _SAFE_ENV_KEYS and not any(
            marker in key.upper() for marker in _CREDENTIAL_MARKERS
        ):
            env[key] = value
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "LC_ALL": "C.UTF-8",
        }
    )
    return env


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


class SafeGitRepo:
    """A repository boundary and parameterized command runner."""

    def __init__(self, root: str | Path, *, timeout: float = 30.0) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise GitError(f"git repository root does not exist: {self.root}")
        if not (self.root / ".git").exists():
            raise GitError(f"not a git worktree: {self.root}")
        self.timeout = timeout

    def validate_relative(self, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceError("git path must not be empty")
        raw = value.strip().replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
            raise WorkspaceError("absolute git paths are not allowed")
        parts = [part for part in raw.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise WorkspaceError("git path traversal is not allowed")
        return Path(*parts).as_posix()

    def validate_ref(self, value: str, *, field: str) -> str:
        if not _REF_RE.match(value) or ".." in value or "@{" in value:
            raise WorkspaceError(f"invalid git {field}: {value!r}")
        return value

    async def run(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        output_limit: int = 262_144,
    ) -> GitCommandResult:
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_safe_git_env(),
        )
        stdout_task = asyncio.create_task(
            _read_limited(process.stdout, output_limit)
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, output_limit)
        )
        timed_out = False
        try:
            await asyncio.wait_for(
                process.wait(), timeout=timeout or self.timeout
            )
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        stdout_raw, _ = await stdout_task
        stderr_raw, _ = await stderr_task
        return GitCommandResult(
            stdout=stdout_raw.decode("utf-8", errors="replace"),
            stderr=stderr_raw.decode("utf-8", errors="replace"),
            exit_code=process.returncode,
            timed_out=timed_out,
            duration_seconds=max(0.0, time.perf_counter() - started),
        )


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitStatusArgs(_Args):
    short: bool = True


class GitStatusOutput(BaseModel):
    status: str
    clean: bool


class GitDiffArgs(_Args):
    path: str | None = Field(default=None, max_length=240)
    staged: bool = False
    stat: bool = False


class GitDiffOutput(BaseModel):
    diff: str
    empty: bool


class GitLogArgs(_Args):
    max_count: int = Field(default=10, ge=1, le=100)


class GitLogOutput(BaseModel):
    log: str
    entries: list[str]


class GitAddArgs(_Args):
    paths: list[str] = Field(min_length=1, max_length=50)


class GitAddOutput(BaseModel):
    paths: list[str]


class GitCommitArgs(_Args):
    message: str = Field(min_length=1, max_length=2000)
    paths: list[str] = Field(min_length=1, max_length=50)


class GitCommitOutput(BaseModel):
    commit: str
    message: str
    paths: list[str]


class GitPushArgs(_Args):
    remote: str = Field(default="origin", max_length=100)
    branch: str | None = Field(default=None, max_length=120)
    set_upstream: bool = False


class GitPushOutput(BaseModel):
    remote: str
    branch: str | None
    stdout: str
    stderr: str


def _git_failure(result: GitCommandResult, action: str) -> ToolResult:
    if result.timed_out:
        return ToolResult.fail(f"git {action} timed out")
    return ToolResult.fail(
        f"git {action} failed with exit code {result.exit_code}: "
        f"{(result.stderr or result.stdout).strip()[:800]}"
    )


class _GitTool(BaseTool):
    def __init__(self, repo: SafeGitRepo) -> None:
        self.repo = repo
        super().__init__()

    def _paths(self, paths: list[str]) -> list[str]:
        return [self.repo.validate_relative(path) for path in paths]


class GitStatusTool(_GitTool):
    name = "git_status"
    description = "Show working-tree status for the authorized local repository."
    args_model = GitStatusArgs
    output_model = GitStatusOutput
    permissions = [ToolPermission.GIT_READ]
    timeout_seconds = 20

    async def run(
        self, arguments: GitStatusArgs, context: ToolExecutionContext
    ) -> ToolResult:
        result = await self.repo.run(
            ["status", "--short"] if arguments.short else ["status"]
        )
        if result.exit_code != 0:
            return _git_failure(result, "status")
        return ToolResult.ok(
            GitStatusOutput(
                status=result.stdout, clean=not result.stdout.strip()
            ).model_dump()
        )


class GitDiffTool(_GitTool):
    name = "git_diff"
    description = "Show unstaged or staged diff for the authorized local repository."
    args_model = GitDiffArgs
    output_model = GitDiffOutput
    permissions = [ToolPermission.GIT_READ]
    timeout_seconds = 20

    async def run(
        self, arguments: GitDiffArgs, context: ToolExecutionContext
    ) -> ToolResult:
        args = ["diff"]
        if arguments.staged:
            args.append("--cached")
        if arguments.stat:
            args.append("--stat")
        if arguments.path:
            args.extend(["--", self.repo.validate_relative(arguments.path)])
        result = await self.repo.run(args)
        if result.exit_code != 0:
            return _git_failure(result, "diff")
        return ToolResult.ok(
            GitDiffOutput(
                diff=result.stdout, empty=not result.stdout.strip()
            ).model_dump()
        )


class GitLogTool(_GitTool):
    name = "git_log"
    description = "Show recent commit history for the authorized local repository."
    args_model = GitLogArgs
    output_model = GitLogOutput
    permissions = [ToolPermission.GIT_READ]
    timeout_seconds = 20

    async def run(
        self, arguments: GitLogArgs, context: ToolExecutionContext
    ) -> ToolResult:
        result = await self.repo.run(
            ["log", "--oneline", "--decorate", "-n", str(arguments.max_count)]
        )
        if result.exit_code != 0:
            return _git_failure(result, "log")
        entries = [line for line in result.stdout.splitlines() if line.strip()]
        return ToolResult.ok(
            GitLogOutput(log=result.stdout, entries=entries).model_dump()
        )


class GitAddTool(_GitTool):
    name = "git_add"
    description = "Stage explicitly listed files in the authorized local repository."
    args_model = GitAddArgs
    output_model = GitAddOutput
    permissions = [ToolPermission.GIT_WRITE]
    timeout_seconds = 20

    async def run(
        self, arguments: GitAddArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            paths = self._paths(arguments.paths)
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        result = await self.repo.run(["add", "--", *paths])
        if result.exit_code != 0:
            return _git_failure(result, "add")
        return ToolResult.ok(GitAddOutput(paths=paths).model_dump())


class GitCommitTool(_GitTool):
    name = "git_commit"
    description = (
        "Stage and commit an explicit list of files in the authorized local "
        "repository. An explicit file scope is mandatory."
    )
    args_model = GitCommitArgs
    output_model = GitCommitOutput
    permissions = [ToolPermission.GIT_WRITE]
    timeout_seconds = 30

    async def run(
        self, arguments: GitCommitArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            paths = self._paths(arguments.paths)
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        add_result = await self.repo.run(["add", "--", *paths])
        if add_result.exit_code != 0:
            return _git_failure(add_result, "add")
        commit_result = await self.repo.run(
            [
                "-c",
                "user.name=AgentForge",
                "-c",
                "user.email=agentforge@localhost",
                "commit",
                "-m",
                arguments.message,
                "--",
                *paths,
            ]
        )
        if commit_result.exit_code != 0:
            return _git_failure(commit_result, "commit")
        hash_result = await self.repo.run(["rev-parse", "--short", "HEAD"])
        if hash_result.exit_code != 0:
            return _git_failure(hash_result, "rev-parse")
        return ToolResult.ok(
            GitCommitOutput(
                commit=hash_result.stdout.strip(),
                message=arguments.message,
                paths=paths,
            ).model_dump()
        )


class GitPushTool(_GitTool):
    name = "git_push"
    description = (
        "Push the authorized local repository to a configured remote. Requires "
        "explicit human approval; no credentials are supplied by AgentForge."
    )
    args_model = GitPushArgs
    output_model = GitPushOutput
    permissions = [ToolPermission.GIT_PUSH]
    requires_approval = True
    timeout_seconds = 60

    async def run(
        self, arguments: GitPushArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            remote = self.repo.validate_ref(arguments.remote, field="remote")
            branch = (
                self.repo.validate_ref(arguments.branch, field="branch")
                if arguments.branch
                else None
            )
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        args = ["push"]
        if arguments.set_upstream:
            args.append("--set-upstream")
        args.append(remote)
        if branch:
            args.append(branch)
        result = await self.repo.run(args)
        if result.exit_code != 0:
            return _git_failure(result, "push")
        return ToolResult.ok(
            GitPushOutput(
                remote=remote,
                branch=branch,
                stdout=result.stdout,
                stderr=result.stderr,
            ).model_dump()
        )


def git_tools(repo_root: str | Path, *, timeout: float = 30.0) -> list[BaseTool]:
    repo = SafeGitRepo(repo_root, timeout=timeout)
    return [
        GitStatusTool(repo),
        GitDiffTool(repo),
        GitLogTool(repo),
        GitAddTool(repo),
        GitCommitTool(repo),
        GitPushTool(repo),
    ]
