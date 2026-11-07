"""AgentForge runtime assembly.

The runtime is the only place where infrastructure is wired together. Agents,
messaging, persistence, memory and orchestration remain independently testable.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from agentforge.agents.base import BaseAgent
from agentforge.agents.factory import build_default_agents
from agentforge.agents.registry import AgentRegistry
from agentforge.config.settings import Settings
from agentforge.llm.base import BaseLLMProvider, LLMResponse
from agentforge.llm.factory import build_llm_provider
from agentforge.logging_config import setup_logging
from agentforge.memory.sqlite_store import SQLiteMemoryStore
from agentforge.messaging.bus import MessageBus
from agentforge.messaging.conversation import ConversationManager
from agentforge.models.agent import AgentCard
from agentforge.models.common import HUMAN_SENDER, utc_now
from agentforge.models.execution import AgentExecution, ExecutionStatus
from agentforge.models.message import (
    Conversation,
    Message,
    MessageType,
)
from agentforge.models.task import Task
from agentforge.models.workflow import Workflow
from agentforge.orchestration.orchestrator import TaskOrchestrator
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.tools.base import ToolRegistry

logger = logging.getLogger("agentforge.runtime")


class ChatExchange(BaseModel):
    conversation: Conversation
    incoming: Message
    outgoing: Message
    response: LLMResponse


class AgentForgeRuntime:
    """Fully assembled Phase 1 runtime."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: BaseLLMProvider | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_directories()
        setup_logging(
            self.settings.log_level,
            self.settings.log_file,
            force=True,
        )

        self.provider = provider or build_llm_provider(self.settings)
        self.store = SQLiteStore(self.settings.resolved_sqlite_file)
        self.events = EventLogger(self.settings.resolved_events_file)
        self.memory = SQLiteMemoryStore(self.store)
        self.tools = ToolRegistry()

        self.agents = AgentRegistry()
        for agent in build_default_agents(
            provider=self.provider,
            memory=self.memory,
            tools=self.tools,
            memory_recall_limit=4,
        ):
            self.agents.register(agent)

        self.conversations = ConversationManager(self.store)
        self.bus = MessageBus(
            self.store,
            self.conversations,
            self.events,
            human_id=HUMAN_SENDER,
        )
        for agent in self.agents.list():
            self.bus.register_agent(agent)

        self.orchestrator = TaskOrchestrator(
            agents=self.agents,
            bus=self.bus,
            conversations=self.conversations,
            store=self.store,
            events=self.events,
            settings=self.settings,
        )
        self.events.event(
            "runtime_initialized",
            provider=self.provider.name,
            model=self.provider.model,
            agent_ids=[agent.agent_id for agent in self.agents.list()],
            sqlite_path=str(self.store.path),
        )
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.provider.aclose()
        self.events.close()
        self.store.close()

    async def __aenter__(self) -> "AgentForgeRuntime":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ------------------------------------------------------------------- agents
    def list_agents(self) -> list[AgentCard]:
        return self.agents.cards()

    def get_agent(self, agent_id: str) -> BaseAgent:
        return self.agents.require(agent_id)

    # ------------------------------------------------------------------ workflows
    async def submit_goal(
        self, goal: str, *, conversation_id: str | None = None
    ) -> Workflow:
        """Publish a goal to a group conversation and execute the workflow."""
        goal = goal.strip()
        if not goal:
            raise ValueError("goal must not be empty")

        if conversation_id is None:
            conversation = self.conversations.create_group(
                title="AgentForge collaboration: " + goal[:70],
                agent_ids=[agent.agent_id for agent in self.agents.list()],
                metadata={"kind": "goal"},
            )
            conversation_id = conversation.conversation_id
        else:
            conversation = self.conversations.require(conversation_id)

        published = self.bus.send(
            sender=HUMAN_SENDER,
            conversation_id=conversation.conversation_id,
            message_type=MessageType.GROUP,
            content=goal,
            metadata={"kind": "goal", "human_published": True},
        )
        self.events.event(
            "goal_published",
            conversation_id=conversation.conversation_id,
            message_id=published.message_id,
            goal=goal,
        )

        workflow = await self.orchestrator.run_goal(goal, conversation.conversation_id)
        workflow.metadata["published_message_id"] = published.message_id
        self.store.save_workflow(workflow)
        return workflow

    # ----------------------------------------------------------------- private chat
    async def chat_with_agent(self, agent_id: str, text: str) -> ChatExchange:
        """Send a human private message and persist the agent's reply."""
        text = text.strip()
        if not text:
            raise ValueError("message must not be empty")
        agent = self.agents.require(agent_id)
        conversation = self.conversations.get_or_create_direct(
            HUMAN_SENDER, agent.agent_id, title=f"Private: {agent.name}"
        )
        history = self.store.list_messages(conversation.conversation_id)

        incoming = self.bus.send_direct(
            sender=HUMAN_SENDER,
            receiver=agent.agent_id,
            message_type=MessageType.DIRECT,
            content=text,
            metadata={"kind": "private_chat"},
        )

        started_at = utc_now()
        try:
            result = await agent.respond_to_chat(
                text,
                history=history,
                conversation_title=conversation.title,
            )
        except Exception as exc:
            self._record_chat_execution(
                agent,
                conversation,
                started_at,
                utc_now(),
                ExecutionStatus.FAILED,
                response=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        outgoing = self.bus.send_direct(
            sender=agent.agent_id,
            receiver=HUMAN_SENDER,
            message_type=MessageType.DIRECT,
            content=result.response.content,
            in_reply_to=incoming.message_id,
            metadata={"kind": "private_chat_reply"},
        )
        self._record_chat_execution(
            agent,
            conversation,
            started_at,
            utc_now(),
            ExecutionStatus.COMPLETED,
            response=result.response,
            error=None,
        )
        try:
            agent.remember_chat(text, result.response.content)
        except Exception:
            logger.exception("failed to record chat memory for %s", agent.agent_id)

        return ChatExchange(
            conversation=conversation,
            incoming=incoming,
            outgoing=outgoing,
            response=result.response,
        )

    def _record_chat_execution(
        self,
        agent: BaseAgent,
        conversation: Conversation,
        started_at,
        finished_at,
        status: ExecutionStatus,
        *,
        response: LLMResponse | None,
        error: str | None,
    ) -> None:
        execution = AgentExecution(
            agent_id=agent.agent_id,
            purpose=f"chat:{agent.role.value}",
            provider=response.provider if response else self.provider.name,
            model=response.model if response else self.provider.model,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            error=error,
            prompt_tokens=response.usage.prompt_tokens if response else 0,
            completion_tokens=response.usage.completion_tokens if response else 0,
            latency_ms=response.latency_ms if response else None,
            metadata={
                "conversation_id": conversation.conversation_id,
                "type": "private_chat",
            },
        )
        self.store.save_execution(execution)

    # -------------------------------------------------------------------- queries
    def replay(self, conversation_id: str, *, limit: int | None = None) -> list[Message]:
        return self.store.list_messages(conversation_id, limit=limit)

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        return self.conversations.list(limit)

    def list_tasks(self, workflow_id: str | None = None) -> list[Task]:
        return self.store.list_tasks(workflow_id)

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self.store.get_workflow(workflow_id)

    def list_workflows(self, limit: int = 20) -> list[Workflow]:
        return self.store.list_workflows(limit)

    def count_messages(self, conversation_id: str | None = None) -> int:
        return self.store.count_messages(conversation_id)

    def table_counts(self) -> dict[str, int]:
        return self.store.table_counts()

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def provider_model(self) -> str:
        return self.provider.model
