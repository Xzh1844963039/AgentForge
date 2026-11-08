"""Phase 4 end-to-end demos: approvals, clarification and delegation.

Run with:

    python examples/phase4_demos.py

The LLM decisions are deterministic fixtures, but every state transition,
message, approval, clarification, tool call and persistence write goes through
the real AgentForge runtime. No sandboxed code execution is needed, so Docker is
not required for these demos.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

from agentforge.config.settings import Settings
from agentforge.llm.base import BaseLLMProvider, ChatMessage, LLMResponse, TokenUsage
from agentforge.models.workflow import WorkflowStatus
from agentforge.runtime import AgentForgeRuntime


class DemoProvider(BaseLLMProvider):
    name = "mock"
    model = "phase4-demo-fixture"

    def __init__(
        self,
        coding: Callable[[str], dict[str, Any]],
        *,
        delegated: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.coding = coding
        self.delegated = delegated

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        purpose = purpose or "generic"
        prompt = "\n\n".join(message.content for message in messages)
        if purpose == "plan":
            payload: dict[str, Any] = {
                "goal_summary": "Phase 4 deterministic end-to-end demo",
                "subtasks": [
                    {
                        "key": "demo_work",
                        "description": "Perform the Phase 4 demo work safely.",
                        "required_capabilities": ["coding"],
                        "depends_on": [],
                        "kind": "coding",
                    }
                ],
            }
        elif purpose.startswith("execute:"):
            parts = purpose.split(":")
            kind = parts[1] if len(parts) > 1 else "generic"
            role = parts[2] if len(parts) > 2 else "coordinator"
            if kind == "coding":
                payload = self.coding(prompt)
            elif kind == "delegated" and self.delegated is not None:
                payload = self.delegated(prompt, role)
            elif kind == "synthesis":
                payload = {
                    "action": "complete",
                    "content": "Synthesis completed from recorded task results.",
                }
            elif kind == "review":
                payload = {
                    "action": "complete",
                    "content": "APPROVED\n\nIndependent review used the recorded "
                    "workflow state and tool audit.",
                }
            elif kind == "finalize":
                payload = {
                    "action": "complete",
                    "content": "# Phase 4 demo final report\n\nThe workflow "
                    "completed through the real orchestration, HITL and "
                    "persistence path.",
                }
            else:
                payload = {
                    "action": "complete",
                    "content": f"Completed {kind} work.",
                }
        else:
            payload = {"action": "complete", "content": "Demo response."}
        content = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )


def _settings(base: Path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=base / "data",
        log_level="WARNING",
        mock_latency_ms=0,
    )


async def demo_a_approval_rejection(base: Path) -> None:
    print("\n=== Demo A: human rejects a destructive approval ===")

    def coding(prompt: str) -> dict[str, Any]:
        if "[TOOL_RESULTS]" in prompt:
            return {
                "action": "complete",
                "content": "Delete was denied; the file was preserved and the task closed.",
            }
        return {
            "action": "tool_call",
            "tool_calls": [
                {"name": "delete_file", "arguments": {"path": "stale.md"}}
            ],
        }

    async with AgentForgeRuntime(_settings(base), provider=DemoProvider(coding)) as runtime:
        workspace = runtime.workspaces.workspace_for("coding")
        (workspace / "stale.md").write_text("keep me", encoding="utf-8")
        workflow = await runtime.submit_goal(
            "Delete the stale note only after explicit human approval."
        )
        print(f"workflow after request : {workflow.status.value}")
        approval = runtime.list_approvals(workflow.workflow_id)[0]
        print(f"approval               : {approval.tool_name} {approval.arguments}")
        print(f"argument digest        : {approval.argument_digest}")
        runtime.reject_tool(approval.approval_id, comment="demo: do not delete")
        resumed = await runtime.resume_workflow(workflow.workflow_id)
        stored = runtime.get_approval(approval.approval_id)
        audit = runtime.list_tool_calls(workflow.workflow_id)
        print(f"workflow after resume  : {resumed.status.value}")
        print(f"approval status        : {stored.status.value if stored else 'missing'}")
        print(f"file preserved         : {(workspace / 'stale.md').exists()}")
        print(
            "tool audit             : "
            + " -> ".join(record.status.value for record in audit)
        )
        print(
            f"approval metrics       : requests={resumed.metrics.approval_requests} "
            f"rejected={resumed.metrics.approval_rejected}"
        )


async def demo_b_clarification(base: Path) -> None:
    print("\n=== Demo B: workflow pauses for a human clarification ===")

    def coding(prompt: str) -> dict[str, Any]:
        if "[CLARIFICATION_ANSWER]" in prompt:
            return {
                "action": "complete",
                "content": "Retention policy written using the human's 90-day answer.",
            }
        return {
            "action": "clarify",
            "question": "Should data retention be 30 or 90 days?",
            "clarification_context": "The goal does not specify a retention period.",
            "possible_interpretations": ["30 days", "90 days"],
        }

    async with AgentForgeRuntime(_settings(base), provider=DemoProvider(coding)) as runtime:
        workflow = await runtime.submit_goal("Write the data retention policy note.")
        print(f"workflow after request : {workflow.status.value}")
        request = runtime.list_clarifications(workflow.workflow_id)[0]
        print(f"question               : {request.question}")
        print(f"interpretations        : {request.possible_interpretations}")
        runtime.answer_clarification(request.clarification_id, "90 days")
        resumed = await runtime.resume_workflow(workflow.workflow_id)
        print(f"workflow after answer  : {resumed.status.value}")
        print(
            f"clarification metrics  : requests={resumed.metrics.clarifications} "
            f"answered={resumed.metrics.clarification_answered}"
        )
        print(
            f"lifecycle events       : "
            + ", ".join(
                event.event_type
                for event in runtime.list_workflow_events(workflow.workflow_id)
            )
        )


async def demo_c_delegation(base: Path) -> None:
    print("\n=== Demo C: two-level agent-to-agent delegation ===")

    def delegate(capability: str, description: str) -> dict[str, Any]:
        return {
            "action": "delegate",
            "delegation": {
                "reason": "specialised work is required",
                "subtasks": [
                    {
                        "description": description,
                        "required_capabilities": [capability],
                        "kind": "delegated",
                    }
                ],
            },
        }

    def coding(prompt: str) -> dict[str, Any]:
        if "Research child complete" in prompt:
            return {
                "action": "complete",
                "content": "Parent completed after two levels of delegation.",
            }
        return delegate("research", "Research the implementation constraints")

    def delegated(prompt: str, role: str) -> dict[str, Any]:
        if role == "research":
            if "Review child complete" in prompt:
                return {
                    "action": "complete",
                    "content": "Research child complete after reviewer input.",
                }
            return delegate("review", "Review the proposed implementation constraints")
        if role == "reviewer":
            return {
                "action": "complete",
                "content": "Review child complete.",
            }
        return {"action": "complete", "content": f"Completed for {role}."}

    provider = DemoProvider(coding, delegated=delegated)
    async with AgentForgeRuntime(_settings(base), provider=provider) as runtime:
        workflow = await runtime.submit_goal("Run a two-level delegation demo.")
        tasks = runtime.list_tasks(workflow.workflow_id)
        delegated_tasks = [
            task
            for task in tasks
            if task.metadata.get("origin") == "delegation"
        ]
        print(f"workflow               : {workflow.status.value}")
        for task in sorted(delegated_tasks, key=lambda item: item.depth):
            print(
                f"delegated task         : depth={task.depth} "
                f"agent={task.assigned_agent} status={task.status.value} "
                f"delegated_by={task.metadata.get('delegated_by')}"
            )
        print(
            f"delegation metrics     : count={workflow.metrics.delegation_count} "
            f"max_depth={workflow.metrics.max_depth} "
            f"success={workflow.metrics.delegation_success_rate:.1%}"
        )
        print(f"final report           : {bool(workflow.final_result)}")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentforge-phase4-") as temp:
        base = Path(temp)
        await demo_a_approval_rejection(base)
        await demo_b_clarification(base)
        await demo_c_delegation(base)
        print(f"\nAll Phase 4 demos completed. Temporary data: {base}")


if __name__ == "__main__":
    asyncio.run(main())
