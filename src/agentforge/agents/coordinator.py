"""Coordinator agent: dynamic planning plus synthesis/finalisation roles."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, ValidationError

from agentforge.agents.base import (
    BaseAgent,
    extract_json_payload,
)
from agentforge.llm.base import ChatMessage, LLMResponse
from agentforge.models.agent import AgentCard
from agentforge.models.common import utc_now
from agentforge.models.task import ExecutionPlan, SubtaskSpec
from agentforge.orchestration.errors import (
    AgentOutputError,
    PlanValidationError,
    PlanningError,
)
from agentforge.orchestration.planner import validate_plan

PLANNER_PROTOCOL = """You are the planning module of the CoordinatorAgent.

Produce a JSON execution plan for a team of agents. The plan is validated and
executed by a deterministic orchestrator, so it must be mechanically sound.

JSON schema:
{
  "goal_summary": "<one sentence>",
  "subtasks": [
    {
      "key": "<short unique key>",
      "description": "<concrete, self-contained work item>",
      "required_capabilities": ["<capability token from the catalog>"],
      "depends_on": ["<key of an earlier subtask>"],
      "kind": "research|coding|analysis|writing|generic"
    }
  ]
}

Rules:
- Use only capability tokens that appear in the available-agent catalog.
- Every subtask must be fully covered by at least one listed agent. Split work
  if no single agent has all required capabilities.
- Dependencies must reference other subtask keys and form an acyclic graph.
- Keep the plan small: at most the stated maximum number of subtasks.
- Plan substantive work only. Do NOT add a final review task: the platform
  always runs an independent review phase after synthesis.
- Descriptions must be specific enough for an agent that cannot see this prompt.
- Return exactly one JSON object. No Markdown fences, no commentary."""


class PlanningAttempt(BaseModel):
    response: LLMResponse
    started_at: datetime
    finished_at: datetime
    error: str | None = None


class PlanningResult(BaseModel):
    plan: ExecutionPlan
    ordered_subtasks: list[SubtaskSpec]
    attempts: list[PlanningAttempt]


class CoordinatorAgent(BaseAgent):
    ROLE_INSTRUCTIONS = (
        "You coordinate a team. For planning, decompose goals into a valid DAG "
        "and respect the live capability catalog. For synthesis and finalisation "
        "tasks, integrate the dependency results into a coherent deliverable, "
        "preserve important details, and never invent work that was not done."
    )

    async def plan_goal(
        self,
        goal: str,
        *,
        agent_cards: Sequence[AgentCard],
        max_subtasks: int,
    ) -> PlanningResult:
        """Ask the LLM for a structured plan, validate it, and retry once."""
        if not agent_cards:
            raise PlanningError("no agents are registered; cannot plan")

        catalog_lines = []
        for card in agent_cards:
            catalog_lines.append(
                f"- {card.agent_id} ({card.name}, role={card.role.value}): "
                f"capabilities={', '.join(card.capabilities)}. {card.description}"
            )
        catalog = "\n".join(catalog_lines)

        base_messages = [
            ChatMessage.system(PLANNER_PROTOCOL),
            ChatMessage.user(
                f"[GOAL]\n{goal}\n\n"
                f"[AVAILABLE_AGENTS]\n{catalog}\n\n"
                f"[MAX_SUBTASKS]\n{max_subtasks}\n\n"
                "Return the JSON execution plan now."
            ),
        ]

        attempts: list[PlanningAttempt] = []
        last_error: Exception | None = None
        messages = base_messages

        for attempt_index in range(2):
            started = utc_now()
            response = await self.provider.complete(messages, purpose="plan")
            finished = utc_now()

            try:
                payload = extract_json_payload(response.content)
                if not isinstance(payload, dict):
                    raise AgentOutputError("plan response must be a JSON object")
                plan = ExecutionPlan.model_validate(payload)
                ordered = validate_plan(plan, max_subtasks=max_subtasks)
            except (AgentOutputError, ValidationError, PlanValidationError) as exc:
                last_error = exc
                attempts.append(
                    PlanningAttempt(
                        response=response,
                        started_at=started,
                        finished_at=finished,
                        error=str(exc),
                    )
                )
                if attempt_index == 0:
                    messages = [
                        *base_messages,
                        ChatMessage.assistant(response.content),
                        ChatMessage.user(
                            "[REPAIR]\n"
                            f"Your previous response was invalid: {exc}\n"
                            "Return a corrected JSON plan only. Ensure unique keys, "
                            "known dependency keys, no cycles, capabilities from "
                            f"the catalog, and at most {max_subtasks} subtasks."
                        ),
                    ]
                continue

            attempts.append(
                PlanningAttempt(
                    response=response,
                    started_at=started,
                    finished_at=finished,
                    error=None,
                )
            )
            return PlanningResult(
                plan=plan, ordered_subtasks=ordered, attempts=attempts
            )

        message = (
            "Coordinator failed to produce a valid execution plan after 2 "
            f"attempts. Last error: {last_error}"
        )
        raise PlanningError(message, attempts=attempts)
