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
from agentforge.interaction.approval_service import ApprovalService
from agentforge.interaction.clarification_service import ClarificationService
from agentforge.llm.base import BaseLLMProvider, LLMResponse
from agentforge.llm.factory import build_llm_provider
from agentforge.logging_config import setup_logging
from agentforge.memory.embeddings import HashingEmbeddingProvider
from agentforge.memory.engine import MemoryEvolutionEngine
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import MemoryRetrievalConfig
from agentforge.messaging.bus import MessageBus
from agentforge.messaging.conversation import ConversationManager
from agentforge.models.agent import AgentCard
from agentforge.models.clarification import ClarificationRequest
from agentforge.models.common import HUMAN_SENDER, utc_now
from agentforge.models.execution import AgentExecution, ExecutionStatus
from agentforge.models.message import (
    Conversation,
    Message,
    MessageType,
)
from agentforge.models.task import Task
from agentforge.models.tool import ApprovalRequest, ToolCallRecord
from agentforge.models.workflow import Workflow, WorkflowEvent, WorkflowStatus
from agentforge.orchestration.control import WorkflowControlService
from agentforge.orchestration.orchestrator import TaskOrchestrator
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.sandbox.base import SandboxConfig
from agentforge.sandbox.manager import SandboxManager
from agentforge.tools.approval import ApprovalGate
from agentforge.tools.artifact_store import ArtifactStore
from agentforge.tools.base import (
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
)
from agentforge.tools.builtins import build_default_tools
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.policy import ToolPermissionPolicy
from agentforge.tools.workspace import WorkspaceManager

logger = logging.getLogger("agentforge.runtime")


class ChatExchange(BaseModel):
    conversation: Conversation
    incoming: Message
    outgoing: Message
    response: LLMResponse


class AgentForgeRuntime:
    """Fully assembled AgentForge runtime."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: BaseLLMProvider | None = None,
        approval_gate: ApprovalGate | None = None,
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
        self.memory = EvolutionMemoryStore(
            self.settings.resolved_memory_file,
            embedding_provider=HashingEmbeddingProvider(
                self.settings.memory_embedding_dimensions
            ),
            retrieval_config=MemoryRetrievalConfig(
                enabled=self.settings.memory_recall_enabled,
                max_items=self.settings.memory_recall_limit,
                max_chars=self.settings.memory_prompt_budget_chars,
                include_mock=self.settings.memory_include_mock,
                include_unverified=self.settings.memory_include_unverified,
            ),
            retrieval_enabled=self.settings.memory_recall_enabled,
            decay_half_life_days=self.settings.memory_decay_half_life_days,
            forget_threshold=self.settings.memory_forget_threshold,
            working_ttl_seconds=self.settings.memory_working_ttl_seconds,
        )
        self.memory_engine = MemoryEvolutionEngine(
            audit=self.store, memory=self.memory, settings=self.settings
        )

        self.workspaces = WorkspaceManager(self.settings.resolved_workspace_root)
        self.sandboxes = SandboxManager(
            SandboxConfig(
                image=self.settings.sandbox_image,
                workspace_root=self.settings.resolved_workspace_root,
                cpu_limit=self.settings.sandbox_cpu_limit,
                memory_limit_mb=self.settings.sandbox_memory_mb,
                pids_limit=self.settings.sandbox_pids_limit,
                timeout_seconds=self.settings.sandbox_timeout_seconds,
                output_limit_bytes=self.settings.sandbox_output_limit_bytes,
                network_enabled=self.settings.sandbox_network_enabled,
                container_user=self.settings.sandbox_container_user,
                read_only_root=self.settings.sandbox_read_only_root,
                tmpfs_size_mb=self.settings.sandbox_tmpfs_size_mb,
                max_code_bytes=self.settings.max_file_bytes,
            ),
            workspaces=self.workspaces,
        )
        self.artifacts = ArtifactStore(
            self.settings.resolved_artifacts_root,
            self.workspaces,
            self.store,
            max_bytes=self.settings.max_file_bytes,
        )
        built_tools = build_default_tools(
            self.settings,
            workspaces=self.workspaces,
            artifacts=self.artifacts,
        )
        self.tools: ToolRegistry = built_tools.registry
        self.search_provider = built_tools.search_provider
        self.tool_policy = ToolPermissionPolicy()
        self.approval_service = (
            approval_gate
            if isinstance(approval_gate, ApprovalService)
            else ApprovalService(
                self.store,
                self.events,
                ttl_seconds=self.settings.approval_ttl_seconds,
            )
        )
        self.approval_gate = approval_gate or self.approval_service
        self.clarification_service = ClarificationService(
            self.store,
            self.events,
            ttl_seconds=self.settings.clarification_ttl_seconds,
        )
        self.workflow_control = WorkflowControlService(self.store, self.events)
        self.tool_executor = (
            ToolExecutor(
                self.tools,
                self.store,
                self.events,
                self.tool_policy,
                self.approval_gate,
                max_output_bytes=self.settings.tool_output_limit_bytes,
                raise_on_pending=isinstance(self.approval_gate, ApprovalService),
                approval_ttl_seconds=self.settings.approval_ttl_seconds,
            )
            if self.settings.tools_enabled
            else None
        )

        self.agents = AgentRegistry()
        for agent in build_default_agents(
            provider=self.provider,
            memory=self.memory,
            tools=self.tools,
            memory_recall_limit=self.settings.memory_recall_limit,
            tool_executor=self.tool_executor,
            max_tool_calls=self.settings.max_tool_calls_per_task,
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
            tool_context_factory=self._tool_context_for_task,
            approval_service=self.approval_service,
            clarification_service=self.clarification_service,
            workflow_control=self.workflow_control,
        )
        self.events.event(
            "runtime_initialized",
            provider=self.provider.name,
            model=self.provider.model,
            agent_ids=[agent.agent_id for agent in self.agents.list()],
            tool_names=self.tools.names(),
            search_provider=self.search_provider.name,
            sqlite_path=str(self.store.path),
        )
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.sandboxes.aclose()
        except Exception:
            logger.exception("failed to close sandboxes")
        try:
            await self.search_provider.aclose()
        except Exception:
            logger.exception("failed to close search provider")
        try:
            self.memory.close()
        except Exception:
            logger.exception("failed to close memory store")
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

    # -------------------------------------------------------------------- tools
    def list_tool_specs(self) -> list[ToolSpec]:
        return self.tools.list_specs()

    def list_tool_calls(
        self,
        workflow_id: str | None = None,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        limit: int | None = None,
    ) -> list[ToolCallRecord]:
        return self.store.list_tool_calls(
            workflow_id, task_id=task_id, agent_id=agent_id, limit=limit
        )

    def sandbox_status(self) -> dict[str, str]:
        return self.sandboxes.status()

    def _tool_context_for_task(self, task: Task) -> ToolExecutionContext:
        if not task.assigned_agent:
            raise ValueError("cannot build a tool context for an unassigned task")
        sandbox = (
            self.sandboxes.get(task.assigned_agent)
            if self.tool_executor is not None
            else None
        )
        return ToolExecutionContext(
            agent_id=task.assigned_agent,
            workflow_id=task.workflow_id,
            task_id=task.task_id,
            workspace=self.workspaces.workspace_for(task.assigned_agent),
            sandbox=sandbox,
            metadata={
                "sandbox_manager": "docker",
                "execution_id": task.metadata.get("current_execution_id"),
            },
        )

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
        self._evolve_memory(workflow)
        return workflow

    def _evolve_memory(self, workflow: Workflow) -> None:
        if (
            not self.settings.memory_evolution_enabled
            or workflow.status != WorkflowStatus.COMPLETED
        ):
            return
        try:
            evolution = self.memory_engine.process_workflow(
                workflow.workflow_id, learn=True
            )
            workflow.metadata["memory_evolution"] = evolution.model_dump(mode="json")
            self.store.save_workflow(workflow)
            self.events.event(
                "memory_evolution",
                workflow_id=workflow.workflow_id,
                created=len(evolution.created_memory_ids),
                promoted=len(evolution.promoted_memory_ids),
                candidates=len(evolution.candidate_ids),
                feedback=len(evolution.feedback_ids),
                skipped_reason=evolution.skipped_reason,
            )
        except Exception:
            logger.exception(
                "memory evolution failed for workflow %s", workflow.workflow_id
            )

    def pause_workflow(
        self, workflow_id: str, *, reason: str = "human_pause"
    ) -> Workflow:
        return self.orchestrator.pause_workflow(workflow_id, reason=reason)

    async def resume_workflow(self, workflow_id: str) -> Workflow:
        workflow = await self.orchestrator.resume_goal(workflow_id)
        self._evolve_memory(workflow)
        return workflow

    async def cancel_workflow(
        self, workflow_id: str, *, reason: str = "human_cancel"
    ) -> Workflow:
        tasks = self.store.list_tasks(workflow_id)
        agent_ids = {
            task.assigned_agent for task in tasks if task.assigned_agent
        }
        workflow = self.orchestrator.cancel_workflow(workflow_id, reason=reason)
        for agent_id in sorted(agent_ids):
            await self.sandboxes.stop_agent(agent_id)
        return workflow

    def list_workflow_events(self, workflow_id: str) -> list[WorkflowEvent]:
        return self.workflow_control.repo.list_events(workflow_id)

    # ------------------------------------------------------------------ approvals
    def list_approvals(
        self, workflow_id: str | None = None
    ) -> list[ApprovalRequest]:
        return self.approval_service.list(workflow_id)

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        return self.approval_service.get(approval_id)

    def approve_tool(
        self,
        approval_id: str,
        *,
        decided_by: str = HUMAN_SENDER,
        comment: str = "",
    ) -> ApprovalRequest:
        return self.approval_service.approve(
            approval_id, decided_by=decided_by, comment=comment
        )

    def reject_tool(
        self,
        approval_id: str,
        *,
        decided_by: str = HUMAN_SENDER,
        comment: str = "",
    ) -> ApprovalRequest:
        return self.approval_service.reject(
            approval_id, decided_by=decided_by, comment=comment
        )

    # -------------------------------------------------------------- clarifications
    def list_clarifications(
        self, workflow_id: str | None = None
    ) -> list[ClarificationRequest]:
        return self.clarification_service.list(workflow_id)

    def get_clarification(
        self, clarification_id: str
    ) -> ClarificationRequest | None:
        return self.clarification_service.get(clarification_id)

    def answer_clarification(
        self, clarification_id: str, answer: str
    ) -> ClarificationRequest:
        request = self.clarification_service.answer(
            clarification_id, answer, answered_by=HUMAN_SENDER
        )
        workflow = self.store.get_workflow(request.workflow_id)
        if workflow is not None:
            self.bus.send(
                sender=HUMAN_SENDER,
                conversation_id=workflow.conversation_id,
                message_type=MessageType.GROUP,
                content=answer,
                metadata={
                    "kind": "clarification_answer",
                    "clarification_id": clarification_id,
                    "workflow_id": request.workflow_id,
                    "human_published": True,
                },
            )
        return request

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
        counts = self.store.table_counts()
        counts["memory_entries"] = self.memory.total_count()
        stats = self.memory.stats()
        counts["memory_candidates"] = stats.candidates
        counts["memory_feedback"] = stats.feedback_used
        return counts

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def provider_model(self) -> str:
        return self.provider.model
