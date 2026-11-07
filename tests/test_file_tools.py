"""Private file tools, path isolation and approval tests."""

from __future__ import annotations

import os

import pytest

from agentforge.models.tool import ToolCall, ToolResultStatus
from agentforge.persistence import EventLogger, SQLiteStore
from agentforge.tools.approval import AutoApprovalGate, PendingApprovalGate
from agentforge.tools.base import ToolExecutionContext, ToolRegistry
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.file_tools import file_tools
from agentforge.tools.policy import ToolPermissionPolicy
from agentforge.tools.workspace import WorkspaceManager


@pytest.fixture
def file_env(tmp_path):
    store = SQLiteStore(tmp_path / "files.db")
    events = EventLogger(tmp_path / "events.jsonl")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    registry = ToolRegistry()
    for tool in file_tools(workspaces, max_file_bytes=1024, max_files=20):
        registry.register(tool)
    executor = ToolExecutor(
        registry,
        store,
        events,
        ToolPermissionPolicy(),
        AutoApprovalGate(approved=True),
        max_output_bytes=65_536,
    )
    coding = ToolExecutionContext(
        agent_id="coding",
        workflow_id="wf",
        task_id="task",
        workspace=workspaces.workspace_for("coding"),
    )
    research = ToolExecutionContext(
        agent_id="research",
        workflow_id="wf",
        task_id="task2",
        workspace=workspaces.workspace_for("research"),
    )
    yield store, events, workspaces, registry, executor, coding, research
    events.close()
    store.close()


def call(name: str, arguments: dict, call_id: str | None = None) -> ToolCall:
    return ToolCall(call_id=call_id or f"call_{name}", name=name, arguments=arguments)


async def test_write_read_list_search_update_delete(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env

    write = await executor.execute(
        call("write_file", {"path": "notes/demo.md", "content": "# Title\nhello world"}),
        coding,
    )
    assert write.status is ToolResultStatus.SUCCESS
    assert write.output["path"] == "notes/demo.md"
    assert write.output["size_bytes"] == 19

    read = await executor.execute(
        call("read_file", {"path": "notes/demo.md"}), coding
    )
    assert read.status is ToolResultStatus.SUCCESS
    assert read.output["content"] == "# Title\nhello world"
    assert read.output["truncated"] is False

    listing = await executor.execute(call("list_files", {}), coding)
    assert listing.status is ToolResultStatus.SUCCESS
    assert listing.output["files"][0]["path"] == "notes/demo.md"

    search = await executor.execute(
        call("search_files", {"query": "hello"}), coding
    )
    assert search.status is ToolResultStatus.SUCCESS
    assert search.output["matches"][0]["line"] == 2

    append = await executor.execute(
        call(
            "update_file",
            {"path": "notes/demo.md", "content": "more", "mode": "append"},
        ),
        coding,
    )
    assert append.status is ToolResultStatus.SUCCESS
    content = (workspaces.workspace_for("coding") / "notes/demo.md").read_text(
        encoding="utf-8"
    )
    assert content.endswith("more")

    replace = await executor.execute(
        call(
            "update_file",
            {"path": "notes/demo.md", "content": "replaced", "mode": "replace"},
        ),
        coding,
    )
    assert replace.status is ToolResultStatus.SUCCESS
    assert (workspaces.workspace_for("coding") / "notes/demo.md").read_text(
        encoding="utf-8"
    ) == "replaced"

    delete = await executor.execute(
        call("delete_file", {"path": "notes/demo.md"}), coding
    )
    assert delete.status is ToolResultStatus.SUCCESS
    assert not (workspaces.workspace_for("coding") / "notes/demo.md").exists()


async def test_delete_requires_approval(file_env, tmp_path):
    store, events, workspaces, registry, executor, coding, research = file_env
    (workspaces.workspace_for("coding") / "secret.md").write_text(
        "secret", encoding="utf-8"
    )
    pending_executor = ToolExecutor(
        registry,
        store,
        events,
        ToolPermissionPolicy(),
        PendingApprovalGate(),
    )
    result = await pending_executor.execute(
        call("delete_file", {"path": "secret.md"}), coding
    )
    assert result.status is ToolResultStatus.PENDING_APPROVAL
    assert (workspaces.workspace_for("coding") / "secret.md").exists()


@pytest.mark.parametrize(
    "path",
    [
        "../research/secret.md",
        "..\\research\\secret.md",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "notes/../../secret.md",
        "notes/evil.py",
        "notes/file",
        "CON.md",
    ],
)
async def test_path_traversal_and_extension_rejected(file_env, path):
    store, events, workspaces, registry, executor, coding, research = file_env
    (workspaces.workspace_for("research") / "secret.md").write_text(
        "research secret", encoding="utf-8"
    )
    result = await executor.execute(
        call("read_file", {"path": path}), coding
    )
    assert result.status is ToolResultStatus.ERROR
    assert result.output == {}


async def test_cannot_read_other_agent_workspace(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env
    (workspaces.workspace_for("coding") / "private.md").write_text(
        "coding private", encoding="utf-8"
    )
    result = await executor.execute(
        call("read_file", {"path": "private.md"}), research
    )
    assert result.status is ToolResultStatus.ERROR
    assert "does not exist" in (result.error or "")
    traversal = await executor.execute(
        call("read_file", {"path": "../coding/private.md"}), research
    )
    assert traversal.status is ToolResultStatus.ERROR
    assert "traversal" in (traversal.error or "")


async def test_symlink_escape_rejected(file_env, tmp_path):
    store, events, workspaces, registry, executor, coding, research = file_env
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    link = workspaces.workspace_for("coding") / "escape.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")

    result = await executor.execute(
        call("read_file", {"path": "escape.md"}), coding
    )
    assert result.status is ToolResultStatus.ERROR
    assert "symbolic links" in (result.error or "")


async def test_file_size_limit(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env
    result = await executor.execute(
        call("write_file", {"path": "big.md", "content": "x" * 2000}),
        coding,
    )
    assert result.status is ToolResultStatus.ERROR
    assert "limit" in (result.error or "")


async def test_write_does_not_overwrite_without_flag(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env
    first = await executor.execute(
        call("write_file", {"path": "a.md", "content": "one"}), coding
    )
    assert first.status is ToolResultStatus.SUCCESS
    second = await executor.execute(
        call("write_file", {"path": "a.md", "content": "two"}), coding
    )
    assert second.status is ToolResultStatus.ERROR
    assert "already exists" in (second.error or "")


async def test_update_requires_existing_file(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env
    result = await executor.execute(
        call("update_file", {"path": "missing.md", "content": "x"}), coding
    )
    assert result.status is ToolResultStatus.ERROR
    assert "does not exist" in (result.error or "")


async def test_only_md_and_txt_are_listed(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env
    workspace = workspaces.workspace_for("coding")
    (workspace / "code.py").write_text("print(1)", encoding="utf-8")
    (workspace / "note.txt").write_text("ok", encoding="utf-8")
    listing = await executor.execute(call("list_files", {}), coding)
    assert listing.output["files"] == [
        {
            "path": "note.txt",
            "size_bytes": 2,
            "modified_at": listing.output["files"][0]["modified_at"],
        }
    ]


async def test_audit_records_permission_and_duration(file_env):
    store, events, workspaces, registry, executor, coding, research = file_env
    await executor.execute(
        call("write_file", {"path": "audit.md", "content": "x"}), coding
    )
    records = store.list_tool_calls("wf")
    assert records[-1].tool_name == "write_file"
    assert records[-1].permission_decision.value == "allowed"
    assert records[-1].duration_seconds >= 0
