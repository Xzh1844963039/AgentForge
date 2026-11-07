"""Non-Docker tool integration demo.

This runs the real Runtime, MessageBus, agent loop and ToolExecutor with real
private-workspace file operations. Only the LLM decisions are deterministic mock
fixtures. It demonstrates tool-call plumbing without claiming Docker execution.

For the Docker/Python end-to-end demo, install Docker and run:
    agentforge demo-tools
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agentforge.config import Settings
from agentforge.llm.base import LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider
from agentforge.runtime import AgentForgeRuntime


class FileToolProvider(MockProvider):
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
            return self._resp(
                json.dumps(
                    {
                        "goal_summary": "Real file-tool demo",
                        "subtasks": [
                            {
                                "key": "write_and_verify",
                                "description": (
                                    "Create a Markdown note and read it back."
                                ),
                                "required_capabilities": ["coding"],
                                "depends_on": [],
                                "kind": "coding",
                            }
                        ],
                    }
                )
            )
        if purpose and purpose.startswith("execute:coding:coding"):
            count = prompt.count("[TOOL_RESULTS]")
            if count == 0:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": "notes/demo.md",
                                        "overwrite": True,
                                        "content": (
                                            "# AgentForge tool demo\n\n"
                                            "This file was written by the real "
                                            "ToolExecutor, not by the LLM."
                                        ),
                                    },
                                }
                            ],
                            "reasoning": "Write a real note.",
                        }
                    )
                )
            if count == 1:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "read_file",
                                    "arguments": {"path": "notes/demo.md"},
                                }
                            ],
                            "reasoning": "Read the note back.",
                        }
                    )
                )
            return self._resp(
                json.dumps(
                    {
                        "action": "complete",
                        "content": (
                            "Created and read back notes/demo.md using real file "
                            "tools. The content matches the write request."
                        ),
                        "reasoning": "Verified.",
                    }
                )
            )
        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


async def main() -> int:
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=Path("data/examples/tool_file_demo"),
        log_level="WARNING",
    )
    async with AgentForgeRuntime(settings, provider=FileToolProvider()) as runtime:
        workflow = await runtime.submit_goal("Create and verify a Markdown note")
        print(f"Workflow: {workflow.workflow_id} -> {workflow.status.value}")
        print(f"Error: {workflow.error}")
        print("\nTool calls:")
        for record in runtime.list_tool_calls(workflow.workflow_id):
            print(
                f"  {record.tool_name:12s} {record.status.value:10s} "
                f"{record.duration_seconds:.4f}s"
            )
        workspace = runtime.workspaces.workspace_for("coding")
        note = workspace / "notes" / "demo.md"
        print(f"\nFile exists: {note.exists()} ({note})")
        if note.exists():
            print("--- file content ---")
            print(note.read_text(encoding="utf-8"))
        print("\nMetrics:")
        print(workflow.metrics.model_dump_json(indent=2))
        return 0 if workflow.status.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
