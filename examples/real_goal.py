"""Run a real-provider workflow.

Configure .env first:
    AGENTFORGE_LLM_PROVIDER=deepseek   # or hkgai
    AGENTFORGE_DEEPSEEK_API_KEY=...

Then run from the project root:
    python examples/real_goal.py "Your complex goal"

The runtime never silently falls back to mock. If the configured provider is mock,
the script prints a warning because the output would not be real LLM output.
"""

from __future__ import annotations

import asyncio
import sys

from agentforge.config import Settings
from agentforge.runtime import AgentForgeRuntime

DEFAULT_GOAL = (
    "Research the benefits of multi-agent systems, propose a Python "
    "implementation plan, and review the plan for potential issues."
)


async def main(goal: str) -> int:
    settings = Settings()
    async with AgentForgeRuntime(settings) as runtime:
        if runtime.provider_name == "mock":
            print(
                "WARNING: the configured provider is mock. Output is deterministic "
                "fixture data, not real LLM output. Set AGENTFORGE_LLM_PROVIDER to "
                "deepseek or hkgai for a real run.\n"
            )
        else:
            print(f"Provider: {runtime.provider_name} / {runtime.provider_model}\n")

        workflow = await runtime.submit_goal(goal)
        if workflow.final_result:
            print(workflow.final_result)
        else:
            print(f"Workflow failed: {workflow.error}")

        metrics = workflow.metrics
        print("\n--- Metrics ---")
        print(
            f"Status: {workflow.status.value} | "
            f"Completion: {metrics.task_completion_rate:.0%} "
            f"({metrics.completed_tasks}/{metrics.total_tasks}) | "
            f"LLM calls: {metrics.llm_calls} | "
            f"Tokens: {metrics.prompt_tokens}/{metrics.completion_tokens}"
        )
        print(f"Workflow duration: {workflow.duration_seconds():.2f}s")
        return 0 if workflow.status.value == "completed" else 1


if __name__ == "__main__":
    goal_arg = " ".join(sys.argv[1:]).strip() or DEFAULT_GOAL
    raise SystemExit(asyncio.run(main(goal_arg)))
