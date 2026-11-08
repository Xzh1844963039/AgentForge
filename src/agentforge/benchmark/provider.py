"""Deterministic fixture policy and token-budget wrapper for benchmarks.

The fixture provider is ONLY a harness driver. It does not measure LLM quality;
success is always decided by external artifact/audit verifiers. Live-provider
acceptance never uses these fixtures.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from agentforge.benchmark.models import BenchmarkTask, MockAction
from agentforge.llm.base import BaseLLMProvider, ChatMessage, LLMResponse, TokenUsage

logger = logging.getLogger("agentforge.benchmark.provider")


class BenchmarkBudgetExceeded(RuntimeError):
    def __init__(self, budget_type: str, limit: int, used: int) -> None:
        super().__init__(
            f"{budget_type} budget exceeded (limit={limit}, used={used})"
        )
        self.budget_type = budget_type
        self.limit = limit
        self.used = used


def _tool_result_count(prompt: str) -> int:
    return prompt.count("[TOOL_RESULTS]")


def _clarification_answered(prompt: str) -> bool:
    return "[CLARIFICATION_ANSWER]" in prompt


def _delegated_results(prompt: str) -> bool:
    marker = "[DELEGATED_WORK_RESULTS]\n"
    if marker not in prompt:
        return False
    start = prompt.index(marker) + len(marker)
    tail = prompt[start:].strip()
    return not tail.startswith("(none)")


class FixturePolicyProvider(BaseLLMProvider):
    """Task-local deterministic policy used to validate the benchmark harness."""

    name = "benchmark-fixture"
    model = "agentforge-fixture-policy-v1"

    def __init__(self, task: BenchmarkTask) -> None:
        super().__init__()
        self.task = task
        self.runtime: Any | None = None
        self.calls: list[dict[str, Any]] = []
        self._paused = False
        self._cancelled = False

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
        self.calls.append({"purpose": purpose, "prompt_chars": len(prompt)})

        if purpose == "plan":
            content = self._plan_content()
        elif purpose.startswith("execute:"):
            parts = purpose.split(":")
            kind = parts[1] if len(parts) > 1 else "generic"
            await self._maybe_fault(kind)
            content = self._execute_content(kind, prompt)
        elif purpose.startswith("chat:"):
            content = "Benchmark fixture chat response."
        else:
            content = json.dumps(
                {"action": "complete", "content": "Fixture response."}
            )

        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(content) // 4)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            raw={"fixture": True, "benchmark_task": self.task.task_id},
        )

    def _plan_content(self) -> str:
        if self.task.plan:
            subtasks = [task.model_dump(mode="json") for task in self.task.plan]
        else:
            is_research = self.task.category.value == "research"
            subtasks = [
                {
                    "key": "main",
                    "description": self.task.goal,
                    "required_capabilities": [
                        "research" if is_research else "coding"
                    ],
                    "depends_on": [],
                    "kind": "research" if is_research else "coding",
                }
            ]
        return json.dumps(
            {
                "goal_summary": self.task.title,
                "subtasks": subtasks,
            },
            ensure_ascii=False,
        )

    def _execute_content(self, kind: str, prompt: str) -> str:
        action = self._select_action(kind, prompt)
        if action is not None:
            return json.dumps(action.action, ensure_ascii=False)
        if kind == "review":
            return json.dumps(
                {
                    "action": "complete",
                    "content": "APPROVED\n\nFixture review used persisted task and "
                    "tool audit evidence.",
                }
            )
        if kind == "synthesis":
            return json.dumps(
                {
                    "action": "complete",
                    "content": "Synthesis of completed benchmark subtasks.",
                }
            )
        if kind == "finalize":
            return json.dumps(
                {
                    "action": "complete",
                    "content": "# Benchmark final report\n\nWorkflow finalized.",
                }
            )
        return json.dumps(
            {
                "action": "complete",
                "content": f"Fixture completion for {kind}.",
            }
        )

    def _select_action(self, kind: str, prompt: str) -> MockAction | None:
        tool_results = _tool_result_count(prompt)
        clarified = _clarification_answered(prompt)
        delegated = _delegated_results(prompt)
        candidates: list[tuple[int, int, MockAction]] = []
        for index, action in enumerate(self.task.mock_actions):
            if action.kind not in {kind, "*"}:
                continue
            if action.prompt_contains and action.prompt_contains not in prompt:
                continue
            if (
                action.after_tool_results is not None
                and action.after_tool_results != tool_results
            ):
                continue
            if (
                action.clarification_answered is not None
                and action.clarification_answered != clarified
            ):
                continue
            if (
                action.delegated_results is not None
                and action.delegated_results != delegated
            ):
                continue
            specificity = 2 if action.prompt_contains else 1
            conditions = sum(
                value is not None
                for value in (
                    action.after_tool_results,
                    action.clarification_answered,
                    action.delegated_results,
                )
            )
            candidates.append((specificity, conditions, action))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    async def _maybe_fault(self, kind: str) -> None:
        behavior = self.task.behavior
        if behavior is None or self.runtime is None:
            return
        if behavior.pause_on_kind == kind and not self._paused:
            self._paused = True
            workflows = self.runtime.list_workflows(limit=1)
            if workflows:
                self.runtime.pause_workflow(workflows[0].workflow_id)
        elif behavior.cancel_on_kind == kind and not self._cancelled:
            self._cancelled = True
            workflows = self.runtime.list_workflows(limit=1)
            if workflows:
                await self.runtime.cancel_workflow(workflows[0].workflow_id)


class BudgetedProvider(BaseLLMProvider):
    """Enforce a task token budget and expose actual usage/call counts."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        *,
        token_budget: int,
        name: str | None = None,
    ) -> None:
        super().__init__(
            default_temperature=provider.default_temperature,
            default_max_tokens=provider.default_max_tokens,
        )
        self.provider = provider
        self.token_budget = max(1, token_budget)
        self.name = name or provider.name
        self.model = provider.model
        self.total_tokens = 0
        self.calls = 0
        self.budget_exceeded = False

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if self.total_tokens >= self.token_budget:
            self.budget_exceeded = True
            raise BenchmarkBudgetExceeded(
                "token", self.token_budget, self.total_tokens
            )
        response = await self.provider.complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage = response.usage
        used = (
            usage.total_tokens
            or usage.prompt_tokens + usage.completion_tokens
        )
        self.total_tokens += max(0, used)
        self.calls += 1
        if self.total_tokens > self.token_budget:
            self.budget_exceeded = True
        return response

    async def aclose(self) -> None:
        await self.provider.aclose()

    def set_fixture_runtime(self, runtime: Any) -> None:
        if isinstance(self.provider, FixturePolicyProvider):
            self.provider.runtime = runtime
