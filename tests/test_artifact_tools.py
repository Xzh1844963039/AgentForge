"""Controlled cross-agent artifact transfer tests."""

from __future__ import annotations

import pytest

from agentforge.models.tool import ToolCall, ToolResultStatus
from agentforge.persistence import EventLogger, SQLiteStore
from agentforge.tools.approval import AutoApprovalGate
from agentforge.tools.artifact_store import ArtifactStore
from agentforge.tools.artifact_tools import artifact_tools
from agentforge.tools.base import ToolExecutionContext, ToolRegistry
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.file_tools import file_tools
from agentforge.tools.policy import ToolPermissionPolicy
from agentforge.tools.workspace import WorkspaceManager


@pytest.fixture
def artifact_env(tmp_path):
    store = SQLiteStore(tmp_path / "artifacts.db")
    events = EventLogger(tmp_path / "events.jsonl")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    artifacts = ArtifactStore(
        tmp_path / "artifacts", workspaces, store, max_bytes=1024
    )
    registry = ToolRegistry()
    for tool in file_tools(workspaces, max_file_bytes=1024):
        registry.register(tool)
    for tool in artifact_tools(artifacts):
        registry.register(tool)
    executor = ToolExecutor(
        registry, store, events, ToolPermissionPolicy(), AutoApprovalGate(approved=True)
    )
    coding = ToolExecutionContext(
        agent_id="coding",
        workflow_id="wf",
        task_id="task1",
        workspace=workspaces.workspace_for("coding"),
    )
    research = ToolExecutionContext(
        agent_id="research",
        workflow_id="wf",
        task_id="task2",
        workspace=workspaces.workspace_for("research"),
    )
    yield store, events, workspaces, artifacts, executor, coding, research
    events.close()
    store.close()


def call(name: str, arguments: dict, call_id: str | None = None) -> ToolCall:
    return ToolCall(call_id=call_id or f"call_{name}", name=name, arguments=arguments)


async def test_direct_cross_workspace_read_is_rejected(artifact_env):
    store, events, workspaces, artifacts, executor, coding, research = artifact_env
    await executor.execute(
        call("write_file", {"path": "private.md", "content": "coding secret"}),
        coding,
    )
    direct = await executor.execute(
        call("read_file", {"path": "../coding/private.md"}), research
    )
    assert direct.status is ToolResultStatus.ERROR
    assert "traversal" in (direct.error or "")


async def test_share_then_fetch_artifact(artifact_env):
    store, events, workspaces, artifacts, executor, coding, research = artifact_env
    await executor.execute(
        call("write_file", {"path": "result.md", "content": "shared result"}),
        coding,
    )
    share = await executor.execute(
        call("share_artifact", {"path": "result.md"}), coding
    )
    assert share.status is ToolResultStatus.SUCCESS
    artifact_id = share.output["artifact_id"]
    assert share.output["owner_agent_id"] == "coding"

    unshared = await executor.execute(
        call("fetch_artifact", {"artifact_id": "missing", "dest_path": "x.md"}),
        research,
    )
    assert unshared.status is ToolResultStatus.ERROR
    assert "unknown artifact" in (unshared.error or "")

    fetch = await executor.execute(
        call(
            "fetch_artifact",
            {"artifact_id": artifact_id, "dest_path": "review/copy.md"},
        ),
        research,
    )
    assert fetch.status is ToolResultStatus.SUCCESS
    assert fetch.output["path"] == "review/copy.md"
    assert fetch.output["owner_agent_id"] == "coding"

    read = await executor.execute(
        call("read_file", {"path": "review/copy.md"}), research
    )
    assert read.status is ToolResultStatus.SUCCESS
    assert read.output["content"] == "shared result"

    # The original remains private; the copy is independent.
    await executor.execute(
        call(
            "update_file", {"path": "review/copy.md", "content": "changed"}
        ),
        research,
    )
    original = await executor.execute(
        call("read_file", {"path": "result.md"}), coding
    )
    assert original.output["content"] == "shared result"


async def test_artifact_integrity_failure(artifact_env, tmp_path):
    store, events, workspaces, artifacts, executor, coding, research = artifact_env
    await executor.execute(
        call("write_file", {"path": "result.md", "content": "shared"}), coding
    )
    share = await executor.execute(
        call("share_artifact", {"path": "result.md"}), coding
    )
    artifact = store.get_artifact(share.output["artifact_id"])
    assert artifact is not None
    stored = tmp_path / "artifacts" / artifact.stored_path
    stored.write_text("tampered", encoding="utf-8")

    result = await executor.execute(
        call(
            "fetch_artifact",
            {"artifact_id": artifact.artifact_id, "dest_path": "copy.md"},
        ),
        research,
    )
    assert result.status is ToolResultStatus.ERROR
    assert "integrity" in (result.error or "")


async def test_artifact_size_limit(artifact_env):
    store, events, workspaces, artifacts, executor, coding, research = artifact_env
    (workspaces.workspace_for("coding") / "big.md").write_text(
        "x" * 2000, encoding="utf-8"
    )
    result = await executor.execute(
        call("share_artifact", {"path": "big.md"}), coding
    )
    assert result.status is ToolResultStatus.ERROR
    assert "limit" in (result.error or "")


async def test_artifact_destination_extension_enforced(artifact_env):
    store, events, workspaces, artifacts, executor, coding, research = artifact_env
    await executor.execute(
        call("write_file", {"path": "result.md", "content": "data"}), coding
    )
    share = await executor.execute(
        call("share_artifact", {"path": "result.md"}), coding
    )
    result = await executor.execute(
        call(
            "fetch_artifact",
            {
                "artifact_id": share.output["artifact_id"],
                "dest_path": "copy.py",
            },
        ),
        research,
    )
    assert result.status is ToolResultStatus.ERROR
    assert "must be .md or .txt" in (result.error or "")
