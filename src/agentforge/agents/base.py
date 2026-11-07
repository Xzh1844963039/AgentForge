"""Extensible agent framework.

Agents are deliberately thin: they build a role-specific prompt, call the
configured LLM, and return a structured action (complete / delegate / fail).
All routing, state transitions, budgets and persistence are owned by the
runtime and orchestrator, never by the LLM.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Sequence

from pydantic import BaseModel, Field, ValidationError

from agentforge.llm.base import (
    BaseLLMProvider,
    ChatMessage,
    LLMResponse,
)
from agentforge.memory.base import MemoryStore
from agentforge.models.agent import AgentCard, AgentProfile, AgentRole
from agentforge.models.message import Message
from agentforge.models.task import AgentAction, Task
from agentforge.orchestration.errors import AgentOutputError
from agentforge.tools.base import ToolRegistry

ACTION_PROTOCOL = """Execution protocol (mandatory):
Return exactly one JSON object and nothing else. Do not wrap it in Markdown.

Complete the task:
{"action": "complete", "content": "<the full deliverable>", "reasoning": "<brief>"}

Delegate part of the work to other agents:
{"action": "delegate", "content": "<what you have done and what you still need>",
 "delegation": {"reason": "<why>", "subtasks": [
   {"description": "<delegated work>", "required_capabilities": ["<capability>"],
    "kind": "generic"}]},
 "reasoning": "<brief>"}

Fail honestly when the task cannot be completed:
{"action": "fail", "error": "<specific reason>", "content": "<partial result or explanation>"}

Rules:
- Never fabricate tool output, API responses, citations, test results, or file contents.
- Use only the capabilities and tools that are actually available to you.
- If you delegate, keep the subtasks concrete and no larger than necessary.
- The platform enforces delegation depth and execution-step budgets."""


class AgentRunResult(BaseModel):
    action: AgentAction
    response: LLMResponse
    purpose: str
    memory_hits: list[str] = Field(default_factory=list)
    parse_warning: str | None = None

    @property
    def content(self) -> str:
        return self.action.content


class AgentChatResult(BaseModel):
    response: LLMResponse
    memory_hits: list[str] = Field(default_factory=list)


class BaseAgent:
    """Base implementation shared by every agent role."""

    ROLE_INSTRUCTIONS = (
        "Complete the assigned task directly and honestly. Keep your output "
        "self-contained so another agent can use it without seeing your prompt."
    )

    def __init__(
        self,
        *,
        profile: AgentProfile,
        provider: BaseLLMProvider,
        memory: MemoryStore,
        tools: ToolRegistry,
        memory_recall_limit: int = 4,
    ) -> None:
        self.profile = profile
        self.provider = provider
        self.memory = memory
        self.tools = tools
        self.memory_recall_limit = memory_recall_limit
        self._inbox: list[Message] = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- identity
    @property
    def agent_id(self) -> str:
        return self.profile.agent_id

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def role(self) -> AgentRole:
        return self.profile.role

    @property
    def capabilities(self) -> list[str]:
        return list(self.profile.capabilities)

    @property
    def card(self) -> AgentCard:
        return AgentCard.from_profile(self.profile)

    # ------------------------------------------------------------------ inbox
    def deliver(self, message: Message) -> None:
        """Place a routed message in the agent's inbox."""
        with self._lock:
            if any(item.message_id == message.message_id for item in self._inbox):
                return
            self._inbox.append(message)

    def take_message(self, task_id: str | None = None) -> Message | None:
        """Remove and return the first inbox message (optionally for a task)."""
        with self._lock:
            for index, message in enumerate(self._inbox):
                if task_id is None or message.task_id == task_id:
                    return self._inbox.pop(index)
        return None

    def pending_messages(self) -> list[Message]:
        with self._lock:
            return list(self._inbox)

    @property
    def inbox_size(self) -> int:
        with self._lock:
            return len(self._inbox)

    # -------------------------------------------------------------- execution
    async def execute_task(
        self,
        task: Task,
        *,
        goal: str,
        dependency_results: Sequence[tuple[str, str]] = (),
        delegated_results: Sequence[tuple[str, str]] = (),
    ) -> AgentRunResult:
        """Run one task and parse the model's structured action."""
        memory_hits = self._recall_for(task.description)
        messages = self._build_task_messages(
            task=task,
            goal=goal,
            dependency_results=dependency_results,
            delegated_results=delegated_results,
            memory_hits=memory_hits,
        )
        kind = str(task.metadata.get("kind", "generic"))
        purpose = f"execute:{kind}:{self.role.value}"

        response = await self.provider.complete(messages, purpose=purpose)
        action, warning = parse_agent_action(response.content)
        return AgentRunResult(
            action=action,
            response=response,
            purpose=purpose,
            memory_hits=memory_hits,
            parse_warning=warning,
        )

    async def respond_to_chat(
        self,
        text: str,
        *,
        history: Sequence[Message] = (),
        conversation_title: str = "",
    ) -> AgentChatResult:
        """Respond to a human private message (no task action envelope)."""
        memory_hits = self._recall_for(text)
        system = self._system_prompt()
        system += (
            "\n\nPrivate chat mode: you are replying directly to the human. "
            "Be concise, honest, and conversational. Do not return JSON and do "
            "not claim work was executed unless it actually was."
        )

        transcript_lines = []
        for message in list(history)[-12:]:
            transcript_lines.append(f"{message.sender}: {message.content}")
        transcript = "\n".join(transcript_lines) if transcript_lines else "(none)"

        memory_text = (
            "\n".join(f"- {hit}" for hit in memory_hits) if memory_hits else "(none)"
        )
        user = (
            f"[CONVERSATION]\n{conversation_title or 'Direct conversation'}\n\n"
            f"[RECENT_MESSAGES]\n{transcript}\n\n"
            f"[RELEVANT_MEMORY]\n{memory_text}\n\n"
            f"[HUMAN_MESSAGE]\n{text}"
        )
        response = await self.provider.complete(
            [ChatMessage.system(system), ChatMessage.user(user)],
            purpose=f"chat:{self.role.value}",
        )
        return AgentChatResult(response=response, memory_hits=memory_hits)

    def remember_task_outcome(self, task: Task, result: AgentRunResult) -> None:
        """Record a compact episodic memory in this agent's private namespace."""
        status = result.action.action.value
        outcome = (
            result.action.content
            if result.action.action.value == "complete"
            else (result.action.error or result.action.content)
        )
        content = (
            f"Task ({task.metadata.get('kind', 'generic')}): {task.description}\n"
            f"Action: {status}\nOutcome: {(outcome or '')[:1200]}"
        )
        self.memory.remember(
            self.agent_id,
            content,
            kind="task_outcome",
            namespace="experience",
            importance=0.6,
            metadata={
                "task_id": task.task_id,
                "workflow_id": task.workflow_id,
                "action": status,
            },
        )

    def remember_chat(self, human_text: str, reply: str) -> None:
        self.memory.remember(
            self.agent_id,
            f"Human: {human_text}\nReply: {reply[:1200]}",
            kind="conversation",
            namespace="conversation",
            importance=0.4,
        )

    # ---------------------------------------------------------------- prompts
    def _recall_for(self, query: str) -> list[str]:
        entries = self.memory.recall(
            self.agent_id, query, limit=self.memory_recall_limit
        )
        return [entry.content for entry in entries]

    def _system_prompt(self) -> str:
        instructions = "\n".join(
            part
            for part in (
                self.profile.description,
                self.profile.system_instructions,
                self.ROLE_INSTRUCTIONS,
            )
            if part
        )
        return (
            f"You are {self.name} (agent_id={self.agent_id}), a {self.role.value} "
            f"agent in AgentForge, a multi-agent collaborative platform.\n\n"
            f"{instructions}\n\n"
            f"Your capabilities: {', '.join(self.capabilities)}.\n\n"
            f"Available tools:\n{self.tools.prompt_catalog()}\n\n"
            f"{ACTION_PROTOCOL}"
        )

    def _build_task_messages(
        self,
        *,
        task: Task,
        goal: str,
        dependency_results: Sequence[tuple[str, str]],
        delegated_results: Sequence[tuple[str, str]],
        memory_hits: Sequence[str],
    ) -> list[ChatMessage]:
        dependency_text = _format_results(dependency_results)
        delegated_text = _format_results(delegated_results)
        memory_text = (
            "\n".join(f"- {hit}" for hit in memory_hits) if memory_hits else "(none)"
        )
        user = (
            "[MODE]\nExecute one delegated task and return a single JSON action.\n\n"
            f"[GOAL]\n{goal}\n\n"
            f"[TASK]\n{task.description}\n\n"
            f"[TASK_ID]\n{task.task_id}\n\n"
            f"[TASK_KIND]\n{task.metadata.get('kind', 'generic')}\n\n"
            f"[REQUIRED_CAPABILITIES]\n{', '.join(task.required_capabilities)}\n\n"
            f"[DEPENDENCY_RESULTS]\n{dependency_text}\n\n"
            f"[DELEGATED_WORK_RESULTS]\n{delegated_text}\n\n"
            f"[RELEVANT_MEMORY]\n{memory_text}\n\n"
            "[CONSTRAINTS]\n"
            "Return only the JSON action object. Do not invent results. "
            "If you delegate, the platform will resume this task after the "
            "delegated work completes."
        )
        return [ChatMessage.system(self._system_prompt()), ChatMessage.user(user)]


def _format_results(results: Sequence[tuple[str, str]]) -> str:
    if not results:
        return "(none)"
    blocks = []
    for description, result in results:
        blocks.append(f"### {description}\n{result}")
    return "\n\n".join(blocks)


def extract_json_payload(raw: str) -> Any:
    """Extract the first JSON value from an LLM response.

    Accepts plain JSON, fenced JSON, or JSON embedded in prose. Raises
    ``AgentOutputError`` when no valid JSON is present.
    """
    text = raw.strip()
    if not text:
        raise AgentOutputError("the model returned an empty response")

    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(text)

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidates.append(text[brace_start : brace_end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    raise AgentOutputError(
        "the model response did not contain valid JSON; first 300 characters: "
        f"{text[:300]!r}"
    )


def parse_agent_action(raw: str) -> tuple[AgentAction, str | None]:
    """Parse an agent response into an ``AgentAction``.

    Plain-text responses are accepted as a ``complete`` action for robustness;
    malformed JSON that claims to be an action is rejected so delegation cannot
    be silently downgraded.
    """
    text = raw.strip()
    if not text:
        raise AgentOutputError("the model returned an empty response")

    try:
        payload = extract_json_payload(text)
    except AgentOutputError:
        return (
            AgentAction(
                action="complete",
                content=text,
                reasoning="Plain-text response accepted without a JSON envelope.",
            ),
            "plain-text response accepted as completion",
        )

    if not isinstance(payload, dict) or "action" not in payload:
        return (
            AgentAction(
                action="complete",
                content=text,
                reasoning="JSON response without an action envelope.",
            ),
            "JSON response had no action field; treated as plain completion",
        )

    try:
        action = AgentAction.model_validate(payload)
    except ValidationError as exc:
        raise AgentOutputError(f"invalid agent action payload: {exc}") from exc

    return action, None
