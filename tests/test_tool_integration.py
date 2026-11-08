"""Agent ↔ tool dispatcher integration and Phase 2 workflow tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentforge.llm.base import LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider
from agentforge.models.task import TaskStatus
from agentforge.models.tool import ToolCall, ToolResultStatus
from agentforge.models.workflow import WorkflowStatus
from agentforge.sandbox.docker import DockerSandbox
from agentforge.tools.base import ToolExecutionContext

DOCKER_AVAILABLE, DOCKER_REASON = DockerSandbox.check_available()
requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason=f"Docker unavailable: {DOCKER_REASON}"
)

PHASE2_GOAL = (
    "Create a Python script that calculates prime numbers, execute it, verify "
    "the output, and produce a Markdown report summarizing the implementation "
    "and test results."
)


class ToolFileProvider(MockProvider):
    """Plans one coding task and performs real file tool calls."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[tuple[str, str]] = []

    def _resp(self, content: str) -> LLMResponse:
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            latency_ms=0.0,
        )

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        prompt = "\n\n".join(message.content for message in messages)
        self.prompts.append((purpose or "", prompt))
        if purpose == "plan":
            payload = {
                "goal_summary": "real file tool demo",
                "subtasks": [
                    {
                        "key": "notes",
                        "description": (
                            "Create a Markdown note and read it back to verify."
                        ),
                        "required_capabilities": ["coding"],
                        "depends_on": [],
                        "kind": "coding",
                    }
                ],
            }
            return self._resp(json.dumps(payload))
        if purpose and purpose.startswith("execute:coding:coding"):
            results = prompt.count("[TOOL_RESULTS]")
            if results == 0:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": "notes.md",
                                        "content": "# Tool demo\nreal content",
                                    },
                                }
                            ],
                            "reasoning": "write the note",
                        }
                    )
                )
            if results == 1:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "read_file",
                                    "arguments": {"path": "notes.md"},
                                }
                            ],
                            "reasoning": "verify the note",
                        }
                    )
                )
            return self._resp(
                json.dumps(
                    {
                        "action": "complete",
                        "content": (
                            "Created notes.md and verified its content using real "
                            "file tools. The file contains '# Tool demo\\nreal content'."
                        ),
                        "reasoning": "verified",
                    }
                )
            )
        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class DelegatingToolProvider(MockProvider):
    """Delegates research, then writes a note after the parent resumes."""

    def _resp(self, content: str) -> LLMResponse:
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            latency_ms=0.0,
        )

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        prompt = "\n\n".join(message.content for message in messages)
        if purpose == "plan":
            payload = {
                "goal_summary": "delegation with tools",
                "subtasks": [
                    {
                        "key": "implementation",
                        "description": "Research via delegation, then write notes.",
                        "required_capabilities": ["coding"],
                        "depends_on": [],
                        "kind": "coding",
                    }
                ],
            }
            return self._resp(json.dumps(payload))

        if purpose == "execute:coding:coding":
            if "[DELEGATED_WORK_RESULTS]\n(none)" in prompt and "[TOOL_RESULTS]" not in prompt:
                return self._resp(
                    json.dumps(
                        {
                            "action": "delegate",
                            "content": "Need a research check before writing notes.",
                            "delegation": {
                                "reason": "Gather supporting material",
                                "subtasks": [
                                    {
                                        "description": "Search for multi-agent tool safety notes.",
                                        "required_capabilities": ["research"],
                                        "kind": "research",
                                    }
                                ],
                            },
                            "reasoning": "delegate research",
                        }
                    )
                )
            if "[TOOL_RESULTS]" not in prompt:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": "notes.md",
                                        "content": "# Delegation demo\nresearched",
                                    },
                                }
                            ],
                            "reasoning": "write after research",
                        }
                    )
                )
            return self._resp(
                json.dumps(
                    {
                        "action": "complete",
                        "content": (
                            "Delegated research completed and notes.md written with "
                            "real tool execution."
                        ),
                        "reasoning": "done",
                    }
                )
            )

        if purpose == "execute:research:research":
            if "[TOOL_RESULTS]" not in prompt:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "web_search",
                                    "arguments": {
                                        "query": "multi-agent tool safety",
                                        "max_results": 2,
                                    },
                                }
                            ],
                            "reasoning": "search",
                        }
                    )
                )
            return self._resp(
                json.dumps(
                    {
                        "action": "complete",
                        "content": "Research completed with two mock search results.",
                        "reasoning": "done",
                    }
                )
            )

        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class HostEscapeProvider(MockProvider):
    """Requests python_execute with code that would write a host sentinel."""

    def __init__(self, sentinel: Path) -> None:
        super().__init__()
        self.sentinel = sentinel

    def _resp(self, content: str) -> LLMResponse:
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            latency_ms=0.0,
        )

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        prompt = "\n\n".join(message.content for message in messages)
        if purpose == "plan":
            payload = {
                "goal_summary": "sandbox unavailable test",
                "subtasks": [
                    {
                        "key": "escape",
                        "description": "Write a host sentinel using Python.",
                        "required_capabilities": ["coding"],
                        "depends_on": [],
                        "kind": "coding",
                    }
                ],
            }
            return self._resp(json.dumps(payload))
        if purpose and purpose.startswith("execute:coding:coding"):
            if "[TOOL_RESULTS]" not in prompt:
                code = (
                    f"from pathlib import Path\n"
                    f"Path({str(self.sentinel)!r}).write_text('host-executed')\n"
                )
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "python_execute",
                                    "arguments": {"code": code, "filename": "escape.py"},
                                }
                            ],
                            "reasoning": "attempt execution",
                        }
                    )
                )
            return self._resp(
                json.dumps(
                    {
                        "action": "fail",
                        "error": "python_execute did not run in a Docker sandbox",
                    }
                )
            )
        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


async def test_agent_file_tool_workflow(make_runtime):
    provider = ToolFileProvider()
    runtime = make_runtime(provider=provider)
    workflow = await runtime.submit_goal("Create and verify a note")

    assert workflow.status is WorkflowStatus.COMPLETED, workflow.error
    tasks = [
        task
        for task in runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("role") != "root"
    ]
    coding = next(task for task in tasks if task.metadata.get("kind") == "coding")
    assert coding.status is TaskStatus.COMPLETED
    assert "verified its content" in (coding.result or "")
    assert coding.metadata["tool_call_count"] == 2
    assert len(coding.metadata["tool_call_ids"]) == 2

    workspace = runtime.workspaces.workspace_for("coding")
    note = workspace / "notes.md"
    assert note.exists()
    assert note.read_text(encoding="utf-8") == "# Tool demo\nreal content"

    records = runtime.list_tool_calls(workflow.workflow_id)
    assert [record.tool_name for record in records] == ["write_file", "read_file"]
    assert all(record.status is ToolResultStatus.SUCCESS for record in records)
    assert all(record.workflow_id == workflow.workflow_id for record in records)

    # The synthesis/review/finalize prompts receive structured evidence from the
    # persisted tool audit of the upstream coding task, not just its prose result.
    for purpose in (
        "execute:synthesis:coordinator",
        "execute:review:reviewer",
        "execute:finalize:coordinator",
    ):
        prompts = [text for name, text in provider.prompts if name == purpose]
        assert prompts, f"missing prompt for {purpose}"
        assert "[TOOL_EVIDENCE]" in prompts[-1]
        assert "write_file" in prompts[-1]
        assert "read_file" in prompts[-1]
        assert "real content" in prompts[-1]

    assert "## Tool execution evidence (persisted audit)" in (
        workflow.final_result or ""
    )
    assert "write_file" in (workflow.final_result or "")
    assert "read_file" in (workflow.final_result or "")
    assert "real content" in (workflow.final_result or "")

    metrics = workflow.metrics
    assert metrics.tool_calls == 2
    assert metrics.tool_failures == 0
    assert metrics.tool_seconds >= 0
    assert metrics.llm_calls == 7  # plan + 3 coding turns + 3 governance tasks


async def test_tool_workspace_isolation_within_runtime(make_runtime):
    runtime = make_runtime(provider=MockProvider())
    executor = runtime.tool_executor
    assert executor is not None
    coding = ToolExecutionContext(
        agent_id="coding",
        workspace=runtime.workspaces.workspace_for("coding"),
        sandbox=runtime.sandboxes.get("coding"),
    )
    research = ToolExecutionContext(
        agent_id="research",
        workspace=runtime.workspaces.workspace_for("research"),
        sandbox=runtime.sandboxes.get("research"),
    )

    write = await executor.execute(
        ToolCall(
            name="write_file",
            arguments={"path": "private.md", "content": "coding only"},
        ),
        coding,
    )
    assert write.status is ToolResultStatus.SUCCESS

    direct = await executor.execute(
        ToolCall(
            name="read_file",
            arguments={"path": "../coding/private.md"},
        ),
        research,
    )
    assert direct.status is ToolResultStatus.ERROR
    assert "traversal" in (direct.error or "")

    share = await executor.execute(
        ToolCall(name="share_artifact", arguments={"path": "private.md"}),
        coding,
    )
    assert share.status is ToolResultStatus.SUCCESS
    fetch = await executor.execute(
        ToolCall(
            name="fetch_artifact",
            arguments={
                "artifact_id": share.output["artifact_id"],
                "dest_path": "received.md",
            },
        ),
        research,
    )
    assert fetch.status is ToolResultStatus.SUCCESS
    assert (runtime.workspaces.workspace_for("research") / "received.md").read_text(
        encoding="utf-8"
    ) == "coding only"


async def test_delegation_with_tools(make_runtime):
    runtime = make_runtime(provider=DelegatingToolProvider())
    workflow = await runtime.submit_goal("Delegated tool workflow")

    assert workflow.status is WorkflowStatus.COMPLETED, workflow.error
    assert workflow.metrics.delegation_count == 1
    assert workflow.metrics.max_depth == 2
    assert workflow.metrics.tool_calls >= 2
    records = runtime.list_tool_calls(workflow.workflow_id)
    names = {record.tool_name for record in records}
    assert {"web_search", "write_file"}.issubset(names)
    assert (runtime.workspaces.workspace_for("coding") / "notes.md").exists()

    delegated = [
        task
        for task in runtime.list_tasks(workflow.workflow_id)
        if task.metadata.get("origin") == "delegation"
    ]
    assert len(delegated) == 1
    assert delegated[0].assigned_agent == "research"


async def test_no_host_fallback_when_docker_missing(make_runtime, tmp_path):
    if DOCKER_AVAILABLE:
        pytest.skip("this test verifies the Docker-missing failure path")
    sentinel = tmp_path / "host_sentinel.txt"
    runtime = make_runtime(provider=HostEscapeProvider(sentinel))
    workflow = await runtime.submit_goal("Attempt to execute Python")

    assert workflow.status is WorkflowStatus.FAILED
    assert not sentinel.exists()
    records = runtime.list_tool_calls(workflow.workflow_id)
    assert records[0].tool_name == "python_execute"
    assert records[0].status is ToolResultStatus.ERROR
    assert "Docker sandbox unavailable" in (records[0].error or "")
    assert workflow.metrics.tool_failures == 1


@pytest.mark.docker
@requires_docker
async def test_phase2_primes_end_to_end(make_runtime):
    provider = MockProvider()
    runtime = make_runtime(provider=provider, mock_latency_ms=0)
    workflow = await runtime.submit_goal(PHASE2_GOAL)

    assert workflow.status is WorkflowStatus.COMPLETED, workflow.error
    records = runtime.list_tool_calls(workflow.workflow_id)
    assert [record.tool_name for record in records] == [
        "python_syntax_check",
        "python_execute",
    ]
    assert all(record.status is ToolResultStatus.SUCCESS for record in records)
    execute = records[-1]
    expected_stdout = "Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]"
    assert execute.output["exit_code"] == 0
    assert expected_stdout in execute.output["stdout"]
    assert execute.output["stderr"] == ""
    assert execute.output["duration_seconds"] >= 0
    assert execute.metadata["container"].startswith("agentforge-sandbox-coding-")

    script = runtime.workspaces.workspace_for("coding") / "scripts" / "primes.py"
    assert script.exists()
    assert "def primes" in script.read_text(encoding="utf-8")

    tasks = runtime.list_tasks(workflow.workflow_id)
    coding = next(task for task in tasks if task.metadata.get("kind") == "coding")
    synthesis = next(
        task for task in tasks if task.metadata.get("kind") == "synthesis"
    )
    review = next(task for task in tasks if task.metadata.get("kind") == "review")
    assert len(coding.metadata["tool_call_ids"]) == 2
    assert expected_stdout in (synthesis.result or "")
    assert expected_stdout in (review.result or "")
    assert "Exit code: `0`" in (review.result or "")
    assert "requires a working Docker daemon" not in (review.result or "")
    assert "Docker sandbox" in (review.result or "")

    assert workflow.final_result is not None
    assert expected_stdout in workflow.final_result
    assert "Exit code: `0`" in workflow.final_result
    assert "Tool calls recorded: 2" in workflow.final_result
    assert "agentforge-sandbox-coding-" in workflow.final_result
    assert "stderr:\n```text\n(empty)" in workflow.final_result
    assert "APPROVED_WITH_ISSUES" in workflow.final_result
    assert "(not available)" not in workflow.final_result
    assert "Exit code: unknown" not in workflow.final_result
