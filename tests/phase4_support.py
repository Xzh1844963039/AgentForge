"""Shared deterministic providers and tools for Phase 4 tests.

These helpers are test-only. They drive real runtime, tool, persistence and
orchestration code paths; only the LLM decisions and tool side effects are
fixtures.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable, Sequence

from pydantic import BaseModel

from agentforge.llm.base import BaseLLMProvider, ChatMessage, LLMResponse, TokenUsage
from agentforge.tools.base import BaseTool, ToolPermission, ToolResult


def _content(value: str | dict[str, Any]) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class Phase4Provider(BaseLLMProvider):
    """Provider whose coding decision is supplied by the test."""

    name = "mock"
    model = "phase4-deterministic"

    def __init__(
        self,
        coding_handler: Callable[[str], str | dict[str, Any] | Awaitable[str | dict[str, Any]]]
        | None = None,
        *,
        handlers: dict[
            str,
            Callable[[str, str], str | dict[str, Any] | Awaitable[str | dict[str, Any]]],
        ]
        | None = None,
        plan_subtasks: list[dict[str, Any]] | None = None,
        review_content: str = "APPROVED\n\nPhase 4 workflow verified.",
    ) -> None:
        super().__init__()
        self.coding_handler = coding_handler
        self.handlers = handlers or {}
        self.plan_subtasks = plan_subtasks or [
            {
                "key": "coding_work",
                "description": "Perform the requested coding work safely.",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            }
        ]
        self.review_content = review_content
        self.calls: list[tuple[str, str]] = []

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
        self.calls.append((purpose, prompt))
        if purpose == "plan":
            content = _content(
                {
                    "goal_summary": "Phase 4 deterministic workflow",
                    "subtasks": self.plan_subtasks,
                }
            )
        elif purpose.startswith("execute:"):
            parts = purpose.split(":")
            kind = parts[1] if len(parts) > 1 else "generic"
            role = parts[2] if len(parts) > 2 else "coordinator"
            handler = self.handlers.get(kind)
            if handler is not None:
                value = handler(prompt, role)
                if inspect.isawaitable(value):
                    value = await value
                content = _content(value)
            elif kind == "coding" and self.coding_handler is not None:
                value = self.coding_handler(prompt)
                if inspect.isawaitable(value):
                    value = await value
                content = _content(value)
            elif kind == "review":
                content = _content({"action": "complete", "content": self.review_content})
            elif kind == "synthesis":
                content = _content(
                    {
                        "action": "complete",
                        "content": "Synthesis of the completed work and evidence.",
                    }
                )
            elif kind == "finalize":
                content = _content(
                    {
                        "action": "complete",
                        "content": "# Final report\n\nPhase 4 workflow completed.",
                    }
                )
            else:
                content = _content(
                    {
                        "action": "complete",
                        "content": f"Completed {kind} work for the workflow.",
                    }
                )
        else:
            content = _content({"action": "complete", "content": "Phase 4 response."})
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class EffectArgs(BaseModel):
    label: str = "effect"


class EffectOutput(BaseModel):
    effect_count: int


class EffectTool(BaseTool):
    """Observable side effect used to count duplicate executions."""

    name = "effect_write"
    description = "Record one deterministic side effect (test only)."
    args_model = EffectArgs
    output_model = EffectOutput
    permissions = [ToolPermission.FILE_WRITE]
    requires_approval = False
    timeout_seconds = 5

    def __init__(self, effects: list[str]) -> None:
        super().__init__()
        self.effects = effects

    async def run(self, arguments: EffectArgs, context) -> ToolResult:
        self.effects.append(arguments.label)
        return ToolResult.ok({"effect_count": len(self.effects)})


class ApprovedEffectTool(EffectTool):
    name = "effect_approved"
    description = "Record one approval-gated side effect (test only)."
    requires_approval = True


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self.now = self.now + timedelta(**kwargs)
