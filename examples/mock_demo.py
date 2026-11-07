"""Programmatic deterministic demo.

Run from the project root:
    python examples/mock_demo.py

No API key is required. All LLM content is deterministic mock data.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentforge.config import Settings
from agentforge.runtime import AgentForgeRuntime

GOAL = (
    "Research the benefits of multi-agent systems, propose a Python "
    "implementation plan, and review the plan for potential issues."
)


async def main() -> int:
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=Path("data/example_demo"),
        log_level="WARNING",
    )
    async with AgentForgeRuntime(settings) as runtime:
        print(f"Provider: {runtime.provider_name} (deterministic mock)")
        workflow = await runtime.submit_goal(GOAL)

        print(f"\nWorkflow: {workflow.workflow_id} -> {workflow.status.value}")
        for task in runtime.list_tasks(workflow.workflow_id):
            if task.metadata.get("role") == "root":
                continue
            duration = task.duration_seconds() or 0.0
            print(
                f"  {task.metadata.get('kind'):9s} {task.assigned_agent:12s} "
                f"{task.status.value:10s} {duration:.3f}s"
            )

        metrics = workflow.metrics
        print(
            f"\nCompletion rate: {metrics.task_completion_rate:.0%} "
            f"({metrics.completed_tasks}/{metrics.total_tasks})"
        )
        print(f"LLM calls: {metrics.llm_calls}")
        print(f"Messages persisted: {runtime.count_messages()}")
        print(f"Database: {runtime.store.path}")

        print("\n--- Final report ---\n")
        print(workflow.final_result or workflow.error)
        return 0 if workflow.final_result else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
