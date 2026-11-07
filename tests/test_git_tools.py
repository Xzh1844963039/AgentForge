"""Local Git tool and approval-gate tests (no remote network)."""

from __future__ import annotations

import subprocess

import pytest

from agentforge.models.tool import ToolCall, ToolResultStatus
from agentforge.persistence import EventLogger, SQLiteStore
from agentforge.tools.approval import AutoApprovalGate, PendingApprovalGate
from agentforge.tools.base import ToolExecutionContext, ToolRegistry
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.git_tools import GitError, SafeGitRepo, _safe_git_env, git_tools
from agentforge.tools.policy import ToolPermissionPolicy


def git(*args: str, cwd=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def git_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    remote = tmp_path / "remote.git"
    git("init", "--bare", str(remote))
    git("init", str(repo))
    git("config", "user.name", "Test", cwd=repo)
    git("config", "user.email", "test@example.com", cwd=repo)
    (repo / "README.md").write_text("# Test repo\n", encoding="utf-8")
    git("add", "README.md", cwd=repo)
    git("commit", "-m", "initial", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("branch", "-M", "master", cwd=repo)

    store = SQLiteStore(tmp_path / "git.db")
    events = EventLogger(tmp_path / "events.jsonl")
    registry = ToolRegistry()
    for tool in git_tools(repo):
        registry.register(tool)
    executor = ToolExecutor(
        registry,
        store,
        events,
        ToolPermissionPolicy(),
        AutoApprovalGate(approved=True),
    )
    context = ToolExecutionContext(
        agent_id="coding",
        workflow_id="wf",
        task_id="task",
        workspace=repo,
    )
    yield repo, remote, store, events, registry, executor, context
    events.close()
    store.close()


def call(name: str, arguments: dict | None = None, call_id: str | None = None) -> ToolCall:
    return ToolCall(call_id=call_id or f"call_{name}", name=name, arguments=arguments or {})


async def test_status_diff_log(git_env):
    repo, remote, store, events, registry, executor, context = git_env
    (repo / "README.md").write_text("# Test repo\nchanged\n", encoding="utf-8")

    status = await executor.execute(call("git_status"), context)
    assert status.status is ToolResultStatus.SUCCESS
    assert "README.md" in status.output["status"]

    diff = await executor.execute(call("git_diff"), context)
    assert diff.status is ToolResultStatus.SUCCESS
    assert "changed" in diff.output["diff"]

    log = await executor.execute(call("git_log", {"max_count": 5}), context)
    assert log.status is ToolResultStatus.SUCCESS
    assert "initial" in log.output["log"]


async def test_add_and_commit_explicit_scope(git_env):
    repo, remote, store, events, registry, executor, context = git_env
    (repo / "README.md").write_text("# Test repo\nchanged\n", encoding="utf-8")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")

    add = await executor.execute(
        call("git_add", {"paths": ["README.md"]}), context
    )
    assert add.status is ToolResultStatus.SUCCESS

    commit = await executor.execute(
        call(
            "git_commit",
            {"message": "update readme", "paths": ["README.md"]},
        ),
        context,
    )
    assert commit.status is ToolResultStatus.SUCCESS
    assert commit.output["message"] == "update readme"
    assert commit.output["paths"] == ["README.md"]
    assert commit.output["commit"]

    log = git("log", "--oneline", "-1", cwd=repo).stdout
    assert "update readme" in log
    show = git("show", "--name-only", "--format=", "HEAD", cwd=repo).stdout
    assert "README.md" in show
    assert "other.txt" not in show


async def test_commit_requires_path_scope(git_env):
    repo, remote, store, events, registry, executor, context = git_env
    (repo / "README.md").write_text("changed", encoding="utf-8")
    result = await executor.execute(
        call("git_commit", {"message": "no scope"}), context
    )
    assert result.status is ToolResultStatus.ERROR
    assert "invalid tool arguments" in (result.error or "")


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "C:\\Windows\\win.ini"])
async def test_git_paths_are_scoped(git_env, path):
    repo, remote, store, events, registry, executor, context = git_env
    result = await executor.execute(call("git_add", {"paths": [path]}), context)
    assert result.status is ToolResultStatus.ERROR
    assert result.output == {}


async def test_push_pending_without_approval(git_env):
    repo, remote, store, events, registry, executor, context = git_env
    pending_executor = ToolExecutor(
        registry,
        store,
        events,
        ToolPermissionPolicy(),
        PendingApprovalGate(),
    )
    result = await pending_executor.execute(
        call("git_push", {"remote": "origin", "branch": "master"}), context
    )
    assert result.status is ToolResultStatus.PENDING_APPROVAL
    check = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "master"],
        capture_output=True,
        text=True,
    )
    assert check.returncode != 0
    records = store.list_tool_calls()
    assert records[-1].approval_status.value == "pending"


async def test_push_approved_to_local_bare_remote(git_env):
    repo, remote, store, events, registry, executor, context = git_env
    result = await executor.execute(
        call("git_push", {"remote": "origin", "branch": "master"}), context
    )
    assert result.status is ToolResultStatus.SUCCESS, result.error
    check = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "master"],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0


async def test_push_denied(git_env):
    repo, remote, store, events, registry, executor, context = git_env
    denied = ToolExecutor(
        registry,
        store,
        events,
        ToolPermissionPolicy(),
        AutoApprovalGate(approved=False),
    )
    result = await denied.execute(
        call("git_push", {"remote": "origin", "branch": "master"}), context
    )
    assert result.status is ToolResultStatus.DENIED
    assert "not approved" in (result.error or "")


@pytest.mark.parametrize(
    "remote",
    ["origin; rm -rf /", "origin --force", "--upload-pack=evil", "bad remote"],
)
async def test_push_rejects_injection_like_refs(git_env, remote):
    repo, remote_path, store, events, registry, executor, context = git_env
    result = await executor.execute(
        call("git_push", {"remote": remote, "branch": "master"}), context
    )
    assert result.status is ToolResultStatus.ERROR
    assert "invalid git remote" in (result.error or "")


def test_safe_git_env_scrubs_credentials(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "some-path")
    env = _safe_git_env()
    assert env["PATH"] == "some-path"
    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_invalid_repository_root_rejected(tmp_path):
    with pytest.raises(GitError, match="not a git worktree"):
        SafeGitRepo(tmp_path)
