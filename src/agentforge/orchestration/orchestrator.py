"""Task orchestration: planning, capability assignment, DAG execution.

The decomposition is produced by the Coordinator's LLM call and validated as a
DAG. The platform then adds three governance tasks (synthesis, independent
review, finalisation) so every workflow ends with a checked deliverable. Limits
on plan size, delegation depth, delegation rounds, total tasks and execution
steps prevent runaway loops.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from agentforge.agents.base import AgentRunResult, AgentSuspension
from agentforge.agents.coordinator import PlanningAttempt
from agentforge.agents.registry import AgentRegistry
from agentforge.config.settings import Settings
from agentforge.interaction.approval_service import ApprovalService
from agentforge.interaction.clarification_service import ClarificationService
from agentforge.evaluation.metrics import compute_workflow_metrics
from agentforge.messaging.bus import MessageBus
from agentforge.messaging.conversation import ConversationManager
from agentforge.models.agent import AgentRole
from agentforge.models.common import SYSTEM_SENDER, new_id, utc_now
from agentforge.models.execution import AgentExecution, ExecutionStatus
from agentforge.models.message import MessageType
from agentforge.models.task import (
    AgentActionType,
    ExecutionPlan,
    SubtaskSpec,
    Task,
    TaskStatus,
)
from agentforge.models.clarification import ClarificationStatus
from agentforge.models.tool import ApprovalStatus, ApprovalToken, ToolEvidence
from agentforge.models.workflow import Workflow, WorkflowEvent, WorkflowStatus
from agentforge.orchestration.assignment import (
    AssignmentCandidate,
    CapabilityAssigner,
)
from agentforge.orchestration.control import WorkflowControlService
from agentforge.orchestration.errors import (
    OrchestrationError,
    PlanningError,
    TaskAssignmentError,
    WorkflowCancelledRequested,
    WorkflowPauseRequested,
)
from agentforge.orchestration.state_machine import transition_task
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.tools.base import ToolExecutionContext

logger = logging.getLogger("agentforge.orchestration")

SYNTHESIS_DESCRIPTION = (
    "Synthesize the completed subtask results into a single coherent draft that "
    "directly answers the user's goal. Preserve important details, attribute which "
    "subtask contributed them, and explicitly note gaps."
)
REVIEW_DESCRIPTION = (
    "Independently review the synthesized draft against the original goal and the "
    "underlying subtask results. Return a verdict (APPROVED, APPROVED_WITH_ISSUES, "
    "or REJECTED), a concise summary, numbered issues, and actionable suggestions."
)
FINALIZE_DESCRIPTION = (
    "Produce the final user-facing deliverable. Incorporate the reviewer's findings, "
    "resolve or explicitly note open issues, and format the answer with clear "
    "Markdown headings. Do not mention internal orchestration mechanics unless they "
    "materially affect the answer."
)


@dataclass
class _RunContext:
    workflow: Workflow
    task_map: dict[str, Task] = field(default_factory=dict)
    steps: int = 0
    final_task_id: str | None = None


class TaskOrchestrator:
    def __init__(
        self,
        *,
        agents: AgentRegistry,
        bus: MessageBus,
        conversations: ConversationManager,
        store: SQLiteStore,
        events: EventLogger,
        settings: Settings,
        tool_context_factory: Callable[[Task], ToolExecutionContext] | None = None,
        approval_service: ApprovalService | None = None,
        clarification_service: ClarificationService | None = None,
        workflow_control: WorkflowControlService | None = None,
    ) -> None:
        self.agents = agents
        self.bus = bus
        self.conversations = conversations
        self.store = store
        self.events = events
        self.settings = settings
        self.tool_context_factory = tool_context_factory
        self.approval_service = approval_service or ApprovalService(store, events)
        self.clarification_service = clarification_service or ClarificationService(
            store, events
        )
        self.workflow_control = workflow_control or WorkflowControlService(
            store, events
        )
        self.coordinator = agents.require_role(AgentRole.COORDINATOR)
        self.assigner = CapabilityAssigner(agents.cards())
        self._review_enabled = (
            settings.enable_final_review
            and agents.by_role(AgentRole.REVIEWER) is not None
        )

    async def run_goal(self, goal: str, conversation_id: str) -> Workflow:
        """Plan and execute a goal, returning a completed or failed workflow."""
        workflow = Workflow(
            goal=goal,
            conversation_id=conversation_id,
            status=WorkflowStatus.RUNNING,
            metadata={
                "phase": 4,
                "provider": self.coordinator.provider.name,
                "review_enabled": self._review_enabled,
            },
        )
        self.store.save_workflow(workflow)
        self.events.event(
            "workflow_started",
            workflow_id=workflow.workflow_id,
            conversation_id=conversation_id,
            goal=goal,
            provider=self.coordinator.provider.name,
        )

        root = Task(
            workflow_id=workflow.workflow_id,
            description=goal,
            required_capabilities=[],
            assigned_agent=self.coordinator.agent_id,
            depth=0,
            metadata={"role": "root"},
        )
        transition_task(root, TaskStatus.RUNNING)
        self.store.save_task(root)
        ctx = _RunContext(workflow=workflow, task_map={root.task_id: root})

        try:
            planning = await self.coordinator.plan_goal(
                goal,
                agent_cards=self.agents.cards(),
                max_subtasks=self.settings.max_plan_subtasks,
            )
            self._record_planning_attempts(planning.attempts, ctx, root)
            self._materialize_plan(planning.plan, planning.ordered_subtasks, ctx, root)
            self._post_plan(ctx, planning.ordered_subtasks)
            await self._run_graph(ctx)
        except WorkflowPauseRequested as exc:
            return self._pause_workflow(ctx, root, exc)
        except WorkflowCancelledRequested as exc:
            return self._cancel_workflow(ctx, root, exc.reason)
        except PlanningError as exc:
            self._record_planning_attempts(exc.attempts, ctx, root)
            self._fail_workflow(ctx, root, f"Planning failed: {exc}")
        except Exception as exc:  # domain and unexpected failures are both audited
            logger.exception("workflow %s aborted", workflow.workflow_id)
            self._fail_workflow(
                ctx, root, f"{type(exc).__name__}: {exc}"
            )

        return self._finish_workflow(ctx, root)

    async def resume_goal(self, workflow_id: str) -> Workflow:
        """Resume a paused workflow from persisted tasks and human decisions."""
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise OrchestrationError(f"unknown workflow {workflow_id!r}")
        if workflow.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            return workflow

        self.approval_service.expire_overdue()
        self.clarification_service.expire_overdue()
        tasks = self.store.list_tasks(workflow_id)
        for task in tasks:
            if task.status == TaskStatus.RUNNING and task.metadata.get("role") != "root":
                # The process may have stopped mid-task. Replay from persisted
                # audit; successful tool effects are served from the replay
                # cache instead of being executed a second time.
                task.metadata["replay_mode"] = True
                task.metadata["recovered_from_running"] = True
                transition_task(task, TaskStatus.PENDING)
                self.store.save_task(task)
        for task in tasks:
            approval_id = task.metadata.get("pending_approval_id")
            if approval_id:
                approval = self.approval_service.get(approval_id)
                if approval is None:
                    raise OrchestrationError(
                        f"pending approval {approval_id!r} is missing"
                    )
                if approval.status == ApprovalStatus.PENDING:
                    raise OrchestrationError(
                        f"approval {approval_id!r} is still pending; decide it first"
                    )
                if approval.status == ApprovalStatus.APPROVED and approval.consumed_at:
                    task.metadata["replay_mode"] = True
                elif approval.status in {
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.REJECTED,
                    ApprovalStatus.EXPIRED,
                    ApprovalStatus.CANCELLED,
                }:
                    task.metadata["replay_mode"] = True
                task.metadata["approval_decision"] = approval.status.value
            clarification_id = task.metadata.get("clarification_id")
            if clarification_id:
                clarification = self.clarification_service.get(clarification_id)
                if clarification is None:
                    raise OrchestrationError(
                        f"pending clarification {clarification_id!r} is missing"
                    )
                if clarification.status == ClarificationStatus.OPEN:
                    raise OrchestrationError(
                        f"clarification {clarification_id!r} is still open; "
                        "answer it first"
                    )
                if clarification.status == ClarificationStatus.ANSWERED:
                    task.metadata["clarification_answer"] = clarification.answer
                    task.metadata["replay_mode"] = True
                else:
                    raise OrchestrationError(
                        f"clarification {clarification_id!r} is "
                        f"{clarification.status.value}; it cannot be resumed"
                    )
            self.store.save_task(task)

        if workflow.status == WorkflowStatus.PAUSED:
            self.workflow_control.resume(workflow_id)
            workflow = self.store.get_workflow(workflow_id) or workflow
        workflow.status = WorkflowStatus.RUNNING
        workflow.error = None
        workflow.finished_at = None
        self.store.save_workflow(workflow)
        self.events.event("workflow_resume_started", workflow_id=workflow_id)

        tasks = self.store.list_tasks(workflow_id)
        root = next(
            (task for task in tasks if task.metadata.get("role") == "root"),
            None,
        )
        if root is None:
            raise OrchestrationError("workflow has no root task")
        ctx = _RunContext(
            workflow=workflow,
            task_map={task.task_id: task for task in tasks},
            final_task_id=workflow.metadata.get("final_task_id"),
        )
        try:
            await self._run_graph(ctx)
        except WorkflowPauseRequested as exc:
            return self._pause_workflow(ctx, root, exc)
        except WorkflowCancelledRequested as exc:
            return self._cancel_workflow(ctx, root, exc.reason)
        except Exception as exc:
            logger.exception("workflow %s resume failed", workflow_id)
            self._fail_workflow(ctx, root, f"{type(exc).__name__}: {exc}")
        return self._finish_workflow(ctx, root)

    def pause_workflow(
        self, workflow_id: str, *, reason: str = "human_pause"
    ) -> Workflow:
        """Request a running workflow to pause at its next control point."""
        self.workflow_control.pause(workflow_id, reason=reason)
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise OrchestrationError(f"unknown workflow {workflow_id!r}")
        return workflow

    def cancel_workflow(
        self, workflow_id: str, *, reason: str = "human_cancel"
    ) -> Workflow:
        """Cancel a workflow and its unfinished tasks, approvals and questions."""
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise OrchestrationError(f"unknown workflow {workflow_id!r}")
        if workflow.status == WorkflowStatus.CANCELLED:
            return workflow
        if workflow.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
            raise OrchestrationError(
                f"cannot cancel workflow in status {workflow.status.value}"
            )
        self.workflow_control.cancel(workflow_id, reason=reason)
        workflow = self.store.get_workflow(workflow_id) or workflow
        workflow.error = reason
        workflow.finished_at = utc_now()
        self.approval_service.cancel_for_workflow(workflow_id)
        self.clarification_service.cancel_for_workflow(workflow_id)
        tasks = self.store.list_tasks(workflow_id)
        for task in tasks:
            if task.is_terminal:
                continue
            try:
                transition_task(task, TaskStatus.CANCELLED, error=reason)
                self.store.save_task(task)
                self._sync_agent_task_state(task)
            except Exception:
                logger.exception("failed to cancel task %s", task.task_id)
        ctx = _RunContext(
            workflow=workflow,
            task_map={task.task_id: task for task in tasks},
            final_task_id=workflow.metadata.get("final_task_id"),
        )
        self._save_metrics(ctx)
        self.store.save_workflow(workflow)
        self._notify_group(
            ctx,
            "system",
            MessageType.ERROR,
            f"Workflow cancelled: {reason}",
        )
        return workflow

    def get_workflow_status(self, workflow_id: str) -> WorkflowStatus | None:
        workflow = self.store.get_workflow(workflow_id)
        return workflow.status if workflow is not None else None

    def _control_state(self, workflow_id: str) -> str | None:
        current = self.store.get_workflow(workflow_id)
        if current is None:
            return "cancelled"
        if current.status == WorkflowStatus.PAUSED:
            return "paused"
        if current.status == WorkflowStatus.CANCELLED:
            return "cancelled"
        return None

    # ------------------------------------------------------------------ planning
    def _record_planning_attempts(
        self,
        attempts: list[PlanningAttempt],
        ctx: _RunContext,
        root: Task,
    ) -> None:
        for index, attempt in enumerate(attempts):
            execution = AgentExecution(
                workflow_id=ctx.workflow.workflow_id,
                task_id=root.task_id,
                agent_id=self.coordinator.agent_id,
                purpose="plan",
                provider=attempt.response.provider,
                model=attempt.response.model,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                status=(
                    ExecutionStatus.FAILED
                    if attempt.error
                    else ExecutionStatus.COMPLETED
                ),
                error=attempt.error,
                prompt_tokens=attempt.response.usage.prompt_tokens,
                completion_tokens=attempt.response.usage.completion_tokens,
                latency_ms=attempt.response.latency_ms,
                metadata={"attempt": index + 1},
            )
            self.store.save_execution(execution)
            self.events.event(
                "planning_attempt",
                workflow_id=ctx.workflow.workflow_id,
                execution_id=execution.execution_id,
                attempt=index + 1,
                status=execution.status.value,
                error=attempt.error,
            )

    def _materialize_plan(
        self,
        plan: ExecutionPlan,
        ordered: list[SubtaskSpec],
        ctx: _RunContext,
        root: Task,
    ) -> None:
        workflow = ctx.workflow
        policy_count = 2 + (1 if self._review_enabled else 0)
        projected = 1 + len(ordered) + policy_count
        if projected > self.settings.max_tasks_per_workflow:
            raise OrchestrationError(
                f"plan would create {projected} tasks, exceeding the configured "
                f"maximum of {self.settings.max_tasks_per_workflow}"
            )

        assignments: list[tuple[SubtaskSpec, AssignmentCandidate]] = []
        for spec in ordered:
            assignments.append((spec, self.assigner.assign(spec.required_capabilities)))

        key_to_task: dict[str, Task] = {}
        for spec, assignment in assignments:
            dependency_ids = [key_to_task[key].task_id for key in spec.depends_on]
            task = Task(
                workflow_id=workflow.workflow_id,
                parent_task_id=root.task_id,
                description=spec.description,
                required_capabilities=list(spec.required_capabilities),
                assigned_agent=assignment.card.agent_id,
                dependencies=dependency_ids,
                depth=1,
                metadata={
                    "kind": spec.kind,
                    "plan_key": spec.key,
                    "origin": "plan",
                    "assignment_score": assignment.score,
                    "matched_capabilities": list(assignment.matched),
                },
            )
            self.store.save_task(task)
            ctx.task_map[task.task_id] = task
            key_to_task[spec.key] = task
            self.events.event(
                "task_assigned",
                workflow_id=workflow.workflow_id,
                task_id=task.task_id,
                plan_key=spec.key,
                agent_id=assignment.card.agent_id,
                required_capabilities=task.required_capabilities,
                dependencies=task.dependencies,
            )

        plan_task_ids = [task.task_id for task in key_to_task.values()]
        synthesis = self._create_policy_task(
            ctx=ctx,
            root=root,
            description=SYNTHESIS_DESCRIPTION,
            required_capabilities=["synthesis"],
            dependencies=plan_task_ids,
            kind="synthesis",
        )
        final_dependencies = [synthesis.task_id]
        if self._review_enabled:
            review = self._create_policy_task(
                ctx=ctx,
                root=root,
                description=REVIEW_DESCRIPTION,
                required_capabilities=["review"],
                dependencies=[synthesis.task_id, *plan_task_ids],
                kind="review",
            )
            final_dependencies.append(review.task_id)

        finalize = self._create_policy_task(
            ctx=ctx,
            root=root,
            description=FINALIZE_DESCRIPTION,
            required_capabilities=["synthesis"],
            dependencies=final_dependencies,
            kind="finalize",
        )
        ctx.final_task_id = finalize.task_id
        workflow.metadata["plan_summary"] = plan.goal_summary
        workflow.metadata["final_task_id"] = finalize.task_id
        workflow.metadata["plan_task_count"] = len(plan_task_ids)
        self.store.save_workflow(workflow)

    def _create_policy_task(
        self,
        *,
        ctx: _RunContext,
        root: Task,
        description: str,
        required_capabilities: list[str],
        dependencies: list[str],
        kind: str,
    ) -> Task:
        assignment = self.assigner.assign(required_capabilities)
        task = Task(
            workflow_id=ctx.workflow.workflow_id,
            parent_task_id=root.task_id,
            description=description,
            required_capabilities=required_capabilities,
            assigned_agent=assignment.card.agent_id,
            dependencies=list(dict.fromkeys(dependencies)),
            depth=1,
            metadata={
                "kind": kind,
                "origin": "policy",
                "assignment_score": assignment.score,
                "matched_capabilities": list(assignment.matched),
            },
        )
        self.store.save_task(task)
        ctx.task_map[task.task_id] = task
        self.events.event(
            "task_assigned",
            workflow_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            plan_key=kind,
            agent_id=assignment.card.agent_id,
            required_capabilities=required_capabilities,
            dependencies=task.dependencies,
        )
        return task

    def _post_plan(self, ctx: _RunContext, ordered: list[SubtaskSpec]) -> None:
        lines = ["## Execution plan", f"Goal: {ctx.workflow.goal}", ""]
        for task in self._work_tasks(ctx):
            kind = task.metadata.get("kind", "generic")
            deps = ", ".join(
                _task_label(ctx.task_map.get(dep), dep) for dep in task.dependencies
            )
            lines.append(
                f"- `{kind}` -> **{task.assigned_agent}** "
                f"(capabilities: {', '.join(task.required_capabilities) or 'none'}; "
                f"depends on: {deps or 'none'})"
            )
        lines.append("")
        lines.append(
            "The platform will execute this DAG, synthesise results, run an "
            "independent review, and return a final report."
        )
        self.bus.send(
            sender=self.coordinator.agent_id,
            conversation_id=ctx.workflow.conversation_id,
            message_type=MessageType.PLAN,
            content="\n".join(lines),
            correlation_id=ctx.workflow.workflow_id,
            metadata={"workflow_id": ctx.workflow.workflow_id},
        )

    # ------------------------------------------------------------------ execution
    async def _run_graph(self, ctx: _RunContext) -> None:
        while True:
            control = self._control_state(ctx.workflow.workflow_id)
            if control == "paused":
                raise WorkflowPauseRequested(
                    "workflow pause requested by human"
                )
            if control == "cancelled":
                raise WorkflowCancelledRequested(
                    "workflow cancelled by human"
                )
            progressed = False

            for task in self._snapshot_work_tasks(ctx):
                if task.status != TaskStatus.BLOCKED:
                    continue
                blocked_by = list(task.metadata.get("blocked_by") or [])
                if not blocked_by:
                    continue
                children = [ctx.task_map.get(child_id) for child_id in blocked_by]
                children = [child for child in children if child is not None]
                if len(children) != len(blocked_by) or not all(
                    child.is_terminal for child in children
                ):
                    continue

                if all(child.status == TaskStatus.COMPLETED for child in children):
                    task.metadata["resume_reason"] = "delegated_work_completed"
                    await self._execute_task(ctx, task, resumed=True)
                else:
                    failed = [
                        child
                        for child in children
                        if child.status != TaskStatus.COMPLETED
                    ]
                    self._fail_task_without_execution(
                        ctx,
                        task,
                        "delegated work failed: "
                        + "; ".join(
                            f"{child.task_id[:8]}: {child.error or child.status.value}"
                            for child in failed
                        ),
                    )
                progressed = True

            for task in self._snapshot_work_tasks(ctx):
                if task.status != TaskStatus.PENDING:
                    continue
                dependencies = [ctx.task_map.get(dep) for dep in task.dependencies]
                if any(dep is None for dep in dependencies):
                    self._fail_task_without_execution(
                        ctx, task, "unknown task dependency"
                    )
                    progressed = True
                    continue
                typed = [dep for dep in dependencies if dep is not None]
                if any(dep.status == TaskStatus.FAILED for dep in typed):
                    failed_deps = [dep for dep in typed if dep.status == TaskStatus.FAILED]
                    self._fail_task_without_execution(
                        ctx,
                        task,
                        "dependency failed: "
                        + ", ".join(dep.task_id[:8] for dep in failed_deps),
                    )
                    progressed = True
                    continue
                if all(dep.status == TaskStatus.COMPLETED for dep in typed):
                    await self._execute_task(ctx, task)
                    progressed = True

            if all(task.is_terminal for task in self._work_tasks(ctx)):
                return
            if not progressed:
                waiting = [
                    f"{task.task_id[:8]}({task.status.value})"
                    for task in self._work_tasks(ctx)
                    if not task.is_terminal
                ]
                raise OrchestrationError(
                    "task graph made no progress; possible dependency deadlock: "
                    + ", ".join(waiting)
                )

    async def _execute_task(
        self, ctx: _RunContext, task: Task, *, resumed: bool = False
    ) -> None:
        if ctx.steps >= self.settings.max_execution_steps:
            raise OrchestrationError(
                f"workflow exceeded max_execution_steps="
                f"{self.settings.max_execution_steps}"
            )
        ctx.steps += 1

        if not task.assigned_agent:
            self._fail_task_without_execution(ctx, task, "task has no assigned agent")
            return

        agent = self.agents.get(task.assigned_agent)
        if agent is None:
            self._fail_task_without_execution(
                ctx, task, f"assigned agent {task.assigned_agent!r} is not registered"
            )
            return

        transition_task(task, TaskStatus.RUNNING)
        agent.begin_task(task)
        task.metadata["resumed"] = resumed
        task.metadata["current_execution_id"] = new_id("exec")
        execution_ids = list(task.metadata.get("execution_ids") or [])
        execution_ids.append(task.metadata["current_execution_id"])
        task.metadata["execution_ids"] = execution_ids
        self.store.save_task(task)
        self.events.event(
            "task_started",
            workflow_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            agent_id=task.assigned_agent,
            kind=task.metadata.get("kind", "generic"),
            depth=task.depth,
            resumed=resumed,
            step=ctx.steps,
        )

        dependency_results = self._result_pairs(ctx, task.dependencies)
        child_results = self._result_pairs(
            ctx, list(task.metadata.get("blocked_by") or [])
        )
        kind = str(task.metadata.get("kind", "generic"))
        purpose = f"execute:{kind}:{agent.role.value}"
        started_at = utc_now()
        request_message_id: str | None = None
        approval_tokens = self._approval_tokens_for_task(task)
        clarification_answer = task.metadata.get("clarification_answer")
        replay_mode = bool(task.metadata.get("replay_mode"))

        try:
            request = self._send_task_request(ctx, task)
            request_message_id = request.message_id
            inbox_message = agent.take_message(task.task_id)
            if inbox_message is None:
                raise OrchestrationError(
                    f"task request for {task.task_id} was not delivered to "
                    f"{agent.agent_id}"
                )
            result = await agent.execute_task(
                task,
                goal=ctx.workflow.goal,
                dependency_results=dependency_results,
                delegated_results=child_results,
                tool_evidence=self._collect_tool_evidence(ctx, task),
                tool_context=(
                    self.tool_context_factory(task)
                    if self.tool_context_factory is not None
                    else None
                ),
                approval_tokens=approval_tokens,
                clarification_answer=clarification_answer,
                control_check=lambda: self._control_state(
                    ctx.workflow.workflow_id
                ),
                replay_mode=replay_mode,
            )
        except AgentSuspension as exc:
            self._handle_agent_suspension(
                ctx, task, agent, exc, purpose, started_at
            )
            return
        except Exception as exc:
            finished_at = utc_now()
            self._save_execution(
                ctx,
                task,
                agent.agent_id,
                purpose,
                started_at,
                finished_at,
                ExecutionStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                response=None,
                provider_name=agent.provider.name,
                model_name=agent.provider.model,
            )
            self._fail_task_without_execution(
                ctx, task, f"agent execution failed: {type(exc).__name__}: {exc}"
            )
            return

        tool_call_ids = [
            str(item.metadata["tool_call_id"])
            for item in result.tool_results
            if item.metadata.get("tool_call_id")
        ]
        task.metadata["tool_call_ids"] = tool_call_ids
        task.metadata["tool_call_count"] = len(result.tool_results)

        finished_at = utc_now()
        self._save_agent_calls(
            ctx,
            task,
            agent.agent_id,
            purpose,
            result,
            started_at,
            finished_at,
        )

        if result.action.action == AgentActionType.REJECT_ASSIGNMENT:
            self._handle_rejected_assignment(ctx, task, agent, result)
            return

        try:
            agent.remember_task_outcome(task, result)
        except Exception:  # memory must not invalidate completed work
            logger.exception("failed to record memory for task %s", task.task_id)

        if result.action.action == AgentActionType.COMPLETE:
            self._clear_hitl_metadata(task)
            task.metadata.pop("blocked_by", None)
            transition_task(
                task,
                TaskStatus.COMPLETED,
                result=result.action.content,
            )
            self.store.save_task(task)
            self.events.event(
                "task_completed",
                workflow_id=ctx.workflow.workflow_id,
                task_id=task.task_id,
                agent_id=agent.agent_id,
                duration_seconds=task.duration_seconds(),
            )
            self._send_task_result(
                ctx,
                task,
                agent.agent_id,
                MessageType.TASK_RESULT,
                result.action.content,
                request_message_id,
            )
            self._notify_group(
                ctx,
                "system",
                MessageType.SYSTEM,
                f"Task completed: {kind} by {agent.agent_id} "
                f"({(task.duration_seconds() or 0.0):.3f}s).",
            )
            agent.sync_task_state(task)
            return

        if result.action.action == AgentActionType.FAIL:
            self._clear_hitl_metadata(task)
            error = result.action.error or result.action.content or "agent failed"
            transition_task(task, TaskStatus.FAILED, error=error)
            self.store.save_task(task)
            self.events.event(
                "task_failed",
                workflow_id=ctx.workflow.workflow_id,
                task_id=task.task_id,
                agent_id=agent.agent_id,
                error=error,
            )
            self._send_task_result(
                ctx,
                task,
                agent.agent_id,
                MessageType.ERROR,
                f"Task failed: {error}",
                request_message_id,
            )
            self._notify_group(
                ctx,
                "system",
                MessageType.ERROR,
                f"Task failed: {kind} by {agent.agent_id}: {error}",
            )
            agent.sync_task_state(task)
            return

        # Delegation
        await self._handle_delegation(ctx, task, agent.agent_id, result)
        agent.sync_task_state(task)

    def _approval_tokens_for_task(self, task: Task) -> list[ApprovalToken]:
        approval_id = task.metadata.get("pending_approval_id")
        if not approval_id:
            return []
        request = self.approval_service.get(str(approval_id))
        if request is None:
            return []
        return [
            ApprovalToken(
                approval_id=request.approval_id,
                tool_name=request.tool_name or request.action,
                argument_digest=request.argument_digest,
            )
        ]

    def _clear_hitl_metadata(self, task: Task) -> None:
        for key in (
            "pending_approval_id",
            "pending_tool_name",
            "pending_argument_digest",
            "clarification_id",
            "clarification_answer",
            "replay_mode",
        ):
            if key in task.metadata:
                task.metadata[f"last_{key}"] = task.metadata.pop(key)

    def _handle_agent_suspension(
        self,
        ctx: _RunContext,
        task: Task,
        agent,
        exc: AgentSuspension,
        purpose: str,
        started_at,
    ) -> None:
        finished_at = utc_now()
        self._save_execution(
            ctx,
            task,
            agent.agent_id,
            purpose,
            started_at,
            finished_at,
            ExecutionStatus.COMPLETED,
            error=None,
            response=None,
            provider_name=agent.provider.name,
            model_name=agent.provider.model,
            extra_metadata={"suspended": exc.kind, "reason": exc.reason},
        )
        if exc.kind == "approval":
            request = exc.approval_request
            if request is None:
                raise OrchestrationError(
                    "approval suspension did not include an approval request"
                )
            task.metadata["pending_approval_id"] = request.approval_id
            task.metadata["pending_tool_name"] = request.tool_name
            task.metadata["pending_argument_digest"] = request.argument_digest
            task.metadata["replay_mode"] = True
            task.metadata["approval_decision"] = "pending"
            transition_task(task, TaskStatus.PENDING)
            self.store.save_task(task)
            agent.clear_active_task()
            agent.sync_task_state(task)
            self.workflow_control.pause(
                ctx.workflow.workflow_id, reason="approval_pending"
            )
            ctx.workflow.status = WorkflowStatus.PAUSED
            self.store.save_workflow(ctx.workflow)
            self._notify_group(
                ctx,
                "system",
                MessageType.SYSTEM,
                "Workflow paused for approval. "
                f"Approval `{request.approval_id}`: {request.summary}\n"
                f"Use `agentforge approvals show {request.approval_id}`, then "
                "approve/reject and `agentforge workflows resume "
                f"{ctx.workflow.workflow_id}`.",
                task_id=task.task_id,
            )
            self._workflow_event(
                ctx.workflow.workflow_id,
                "approval_pending",
                approval_id=request.approval_id,
                task_id=task.task_id,
            )
            raise WorkflowPauseRequested(
                "approval_pending",
                task_id=task.task_id,
                approval_id=request.approval_id,
            )
        if exc.kind == "clarification":
            request = self.clarification_service.create(
                workflow_id=ctx.workflow.workflow_id,
                task_id=task.task_id,
                agent_id=agent.agent_id,
                question=exc.question or "Clarification required",
                context=exc.clarification_context,
                possible_interpretations=exc.possible_interpretations,
            )
            task.metadata["clarification_id"] = request.clarification_id
            task.metadata["replay_mode"] = True
            transition_task(task, TaskStatus.PENDING)
            self.store.save_task(task)
            agent.clear_active_task()
            agent.sync_task_state(task)
            self.workflow_control.pause(
                ctx.workflow.workflow_id, reason="clarification_pending"
            )
            ctx.workflow.status = WorkflowStatus.PAUSED
            self.store.save_workflow(ctx.workflow)
            self._notify_group(
                ctx,
                "system",
                MessageType.SYSTEM,
                f"Workflow paused for clarification `{request.clarification_id}`: "
                f"{request.question}\nUse `agentforge clarification answer "
                f"{request.clarification_id} \"<answer>\"`, then resume.",
                task_id=task.task_id,
            )
            self._workflow_event(
                ctx.workflow.workflow_id,
                "clarification_pending",
                clarification_id=request.clarification_id,
                task_id=task.task_id,
            )
            raise WorkflowPauseRequested(
                "clarification_pending",
                task_id=task.task_id,
                clarification_id=request.clarification_id,
            )
        if exc.kind == "pause":
            transition_task(task, TaskStatus.PENDING)
            self.store.save_task(task)
            agent.clear_active_task()
            agent.sync_task_state(task)
            self.workflow_control.pause(
                ctx.workflow.workflow_id, reason="human_pause"
            )
            ctx.workflow.status = WorkflowStatus.PAUSED
            self.store.save_workflow(ctx.workflow)
            self._workflow_event(
                ctx.workflow.workflow_id, "task_paused", task_id=task.task_id
            )
            raise WorkflowPauseRequested("human_pause", task_id=task.task_id)
        if exc.kind == "cancel":
            raise WorkflowCancelledRequested(
                "workflow cancelled by human", task_id=task.task_id
            )
        raise OrchestrationError(f"unknown suspension kind {exc.kind!r}")

    def _pause_workflow(
        self, ctx: _RunContext, root: Task, exc: WorkflowPauseRequested
    ) -> Workflow:
        workflow = ctx.workflow
        stored = self.store.get_workflow(workflow.workflow_id)
        already_paused = stored is not None and stored.status == WorkflowStatus.PAUSED
        workflow.status = WorkflowStatus.PAUSED
        workflow.error = None
        workflow.finished_at = None
        self._save_metrics(ctx, utc_now())
        self.store.save_workflow(workflow)
        if not already_paused:
            self._workflow_event(
                workflow.workflow_id,
                "workflow_paused",
                reason=exc.reason,
                approval_id=exc.approval_id,
                clarification_id=exc.clarification_id,
            )
            self.events.event(
                "workflow_paused",
                workflow_id=workflow.workflow_id,
                reason=exc.reason,
                approval_id=exc.approval_id,
                clarification_id=exc.clarification_id,
            )
        return workflow

    def _cancel_workflow(
        self, ctx: _RunContext, root: Task, reason: str
    ) -> Workflow:
        workflow = ctx.workflow
        stored = self.store.get_workflow(workflow.workflow_id)
        already_cancelled = (
            stored is not None and stored.status == WorkflowStatus.CANCELLED
        )
        workflow.status = WorkflowStatus.CANCELLED
        workflow.error = reason
        workflow.finished_at = utc_now()
        self.approval_service.cancel_for_workflow(workflow.workflow_id)
        self.clarification_service.cancel_for_workflow(workflow.workflow_id)
        if not root.is_terminal:
            transition_task(root, TaskStatus.CANCELLED, error=reason)
            self.store.save_task(root)
        for task in self._work_tasks(ctx):
            if not task.is_terminal:
                try:
                    transition_task(task, TaskStatus.CANCELLED, error=reason)
                    self.store.save_task(task)
                    self._sync_agent_task_state(task)
                except Exception:
                    logger.exception("failed to cancel task %s", task.task_id)
        self._save_metrics(ctx)
        self.store.save_workflow(workflow)
        if not already_cancelled:
            self._workflow_event(
                workflow.workflow_id, "workflow_cancelled", reason=reason
            )
            self.events.event(
                "workflow_cancelled",
                workflow_id=workflow.workflow_id,
                reason=reason,
            )
        self._notify_group(
            ctx,
            "system",
            MessageType.ERROR,
            f"Workflow cancelled: {reason}",
        )
        return workflow

    def _workflow_event(
        self, workflow_id: str, event_type: str, **metadata
    ) -> None:
        self.workflow_control.repo.append_event(
            WorkflowEvent(
                workflow_id=workflow_id,
                event_type=event_type,
                metadata=metadata,
            )
        )

    def _handle_rejected_assignment(
        self,
        ctx: _RunContext,
        task: Task,
        agent,
        result: AgentRunResult,
    ) -> None:
        reason = (
            result.action.reason
            or result.action.content
            or "agent rejected the delegated assignment"
        )
        if task.metadata.get("origin") != "delegation":
            self._fail_task_without_execution(
                ctx, task, f"assignment rejected: {reason}"
            )
            return
        count = int(task.metadata.get("reassignment_count", 0))
        if count >= self.settings.max_reassignments:
            self._fail_task_without_execution(
                ctx,
                task,
                f"reassignment limit reached after {count} reassignment(s): {reason}",
            )
            return
        candidates = [
            candidate
            for candidate in self.assigner.candidates(task.required_capabilities)
            if candidate.score >= 1.0
            and candidate.card.agent_id != task.assigned_agent
        ]
        if not candidates:
            self._fail_task_without_execution(
                ctx,
                task,
                f"no alternative agent can cover {task.required_capabilities}: {reason}",
            )
            return
        replacement_card = candidates[0].card
        parent = ctx.task_map.get(task.parent_task_id or "")
        if parent is None:
            self._fail_task_without_execution(
                ctx, task, f"cannot reassign a task without a parent: {reason}"
            )
            return

        replacement = Task(
            workflow_id=task.workflow_id,
            parent_task_id=task.parent_task_id,
            description=task.description,
            required_capabilities=list(task.required_capabilities),
            assigned_agent=replacement_card.agent_id,
            dependencies=[],
            depth=task.depth,
            metadata={
                **task.metadata,
                "origin": "delegation",
                "reassigned_from": task.task_id,
                "reassignment_count": count + 1,
                "reassignment_reason": reason,
            },
        )
        self.store.save_task(replacement)
        ctx.task_map[replacement.task_id] = replacement

        blocked = list(parent.metadata.get("blocked_by") or [])
        blocked = [
            replacement.task_id if child_id == task.task_id else child_id
            for child_id in blocked
        ]
        delegated = list(parent.metadata.get("delegated_task_ids") or [])
        delegated = [
            replacement.task_id if child_id == task.task_id else child_id
            for child_id in delegated
        ]
        parent.metadata["blocked_by"] = list(dict.fromkeys(blocked))
        parent.metadata["delegated_task_ids"] = list(dict.fromkeys(delegated))
        self.store.save_task(parent)

        transition_task(
            task,
            TaskStatus.CANCELLED,
            error=f"reassigned to {replacement_card.agent_id}: {reason}",
        )
        task.metadata["reassigned_to"] = replacement_card.agent_id
        self.store.save_task(task)
        agent.sync_task_state(task)
        self.events.event(
            "task_reassigned",
            workflow_id=task.workflow_id,
            task_id=task.task_id,
            replacement_task_id=replacement.task_id,
            from_agent=task.assigned_agent,
            to_agent=replacement_card.agent_id,
            reason=reason,
        )
        self._workflow_event(
            ctx.workflow.workflow_id,
            "task_reassigned",
            task_id=task.task_id,
            replacement_task_id=replacement.task_id,
            from_agent=task.assigned_agent,
            to_agent=replacement_card.agent_id,
        )
        self._notify_group(
            ctx,
            "system",
            MessageType.DELEGATION,
            f"Task rejected by `{task.assigned_agent}`: {reason}. "
            f"Reassigned to `{replacement_card.agent_id}`.",
            task_id=replacement.task_id,
        )

    async def _handle_delegation(
        self,
        ctx: _RunContext,
        task: Task,
        agent_id: str,
        result: AgentRunResult,
    ) -> None:
        delegation = result.action.delegation
        if delegation is None or not delegation.subtasks:
            self._fail_task_without_execution(
                ctx, task, "delegation action did not contain subtasks"
            )
            return

        next_depth = task.depth + 1
        if next_depth > self.settings.max_delegation_depth:
            self._fail_task_without_execution(
                ctx,
                task,
                f"maximum delegation depth exceeded "
                f"(max={self.settings.max_delegation_depth})",
            )
            return

        rounds = int(task.metadata.get("delegation_rounds", 0)) + 1
        if rounds > self.settings.max_delegation_rounds:
            self._fail_task_without_execution(
                ctx,
                task,
                f"maximum delegation rounds exceeded "
                f"(max={self.settings.max_delegation_rounds})",
            )
            return

        if (
            len(ctx.task_map) + len(delegation.subtasks)
            > self.settings.max_tasks_per_workflow
        ):
            self._fail_task_without_execution(
                ctx,
                task,
                f"maximum tasks per workflow exceeded "
                f"(max={self.settings.max_tasks_per_workflow})",
            )
            return

        try:
            assigned: list[tuple] = [
                (subtask, self.assigner.assign(subtask.required_capabilities))
                for subtask in delegation.subtasks
            ]
        except TaskAssignmentError as exc:
            self._fail_task_without_execution(
                ctx, task, f"delegation assignment failed: {exc}"
            )
            return

        children: list[Task] = []
        for subtask, assignment in assigned:
            child = Task(
                workflow_id=ctx.workflow.workflow_id,
                parent_task_id=task.task_id,
                description=subtask.description,
                required_capabilities=list(subtask.required_capabilities),
                assigned_agent=assignment.card.agent_id,
                dependencies=[],
                depth=next_depth,
                metadata={
                    "kind": subtask.kind,
                    "origin": "delegation",
                    "delegated_by": agent_id,
                    "assignment_score": assignment.score,
                    "matched_capabilities": list(assignment.matched),
                },
            )
            self.store.save_task(child)
            ctx.task_map[child.task_id] = child
            children.append(child)
            self.events.event(
                "task_assigned",
                workflow_id=ctx.workflow.workflow_id,
                task_id=child.task_id,
                parent_task_id=task.task_id,
                agent_id=assignment.card.agent_id,
                required_capabilities=child.required_capabilities,
                origin="delegation",
            )

        child_ids = [child.task_id for child in children]
        task.metadata["delegation_rounds"] = rounds
        task.metadata["blocked_by"] = child_ids
        task.metadata["delegated_task_ids"] = list(
            dict.fromkeys(
                list(task.metadata.get("delegated_task_ids") or []) + child_ids
            )
        )
        transition_task(task, TaskStatus.BLOCKED)
        self.store.save_task(task)

        summary = "\n".join(
            f"- {child.required_capabilities} -> {child.assigned_agent}: "
            f"{child.description[:180]}"
            for child in children
        )
        self._notify_group(
            ctx,
            agent_id,
            MessageType.DELEGATION,
            f"**{agent_id}** delegated {len(children)} subtask(s) from "
            f"task `{task.metadata.get('kind', 'generic')}` "
            f"(depth {next_depth}/{self.settings.max_delegation_depth}):\n{summary}",
            task_id=task.task_id,
        )
        self.events.event(
            "task_delegated",
            workflow_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            agent_id=agent_id,
            children=[child.task_id for child in children],
            depth=next_depth,
            rounds=rounds,
        )

    # ------------------------------------------------------------------ helpers
    def _send_task_request(self, ctx: _RunContext, task: Task):
        assert task.assigned_agent is not None
        if task.assigned_agent == self.coordinator.agent_id:
            sender = SYSTEM_SENDER
        else:
            sender = self.coordinator.agent_id
        return self.bus.send_direct(
            sender=sender,
            receiver=task.assigned_agent,
            message_type=MessageType.TASK_REQUEST,
            content=task.description,
            correlation_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            metadata={
                "workflow_id": ctx.workflow.workflow_id,
                "kind": task.metadata.get("kind", "generic"),
                "depth": task.depth,
                "dependencies": task.dependencies,
            },
        )

    def _send_task_result(
        self,
        ctx: _RunContext,
        task: Task,
        agent_id: str,
        message_type: MessageType,
        content: str,
        in_reply_to: str | None,
    ) -> None:
        if agent_id == self.coordinator.agent_id:
            return
        self.bus.send_direct(
            sender=agent_id,
            receiver=self.coordinator.agent_id,
            message_type=message_type,
            content=content,
            correlation_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            in_reply_to=in_reply_to,
            metadata={"workflow_id": ctx.workflow.workflow_id},
        )

    def _notify_group(
        self,
        ctx: _RunContext,
        sender: str,
        message_type: MessageType,
        content: str,
        *,
        task_id: str | None = None,
    ) -> None:
        self.bus.send(
            sender=sender,
            conversation_id=ctx.workflow.conversation_id,
            message_type=message_type,
            content=content,
            correlation_id=ctx.workflow.workflow_id,
            task_id=task_id,
            metadata={"workflow_id": ctx.workflow.workflow_id},
        )

    def _save_agent_calls(
        self,
        ctx: _RunContext,
        task: Task,
        agent_id: str,
        purpose: str,
        result: AgentRunResult,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        """Record every LLM turn, including intermediate tool-call turns."""
        if not result.calls:
            self._save_execution(
                ctx,
                task,
                agent_id,
                purpose,
                started_at,
                finished_at,
                (
                    ExecutionStatus.FAILED
                    if result.action.action == AgentActionType.FAIL
                    else ExecutionStatus.COMPLETED
                ),
                error=result.action.error,
                response=result,
            )
            return

        for index, call in enumerate(result.calls):
            is_last = index == len(result.calls) - 1
            status = (
                ExecutionStatus.FAILED
                if is_last and result.action.action == AgentActionType.FAIL
                else ExecutionStatus.COMPLETED
            )
            execution = AgentExecution(
                workflow_id=ctx.workflow.workflow_id,
                task_id=task.task_id,
                agent_id=agent_id,
                purpose=purpose,
                provider=call.response.provider,
                model=call.response.model,
                started_at=call.started_at,
                finished_at=call.finished_at,
                status=status,
                error=result.action.error if status == ExecutionStatus.FAILED else None,
                prompt_tokens=call.response.usage.prompt_tokens,
                completion_tokens=call.response.usage.completion_tokens,
                latency_ms=call.response.latency_ms,
                metadata={
                    "step": index + 1,
                    "final": is_last,
                    "action": (
                        result.action.action.value if is_last else "tool_call"
                    ),
                    "tool_results": len(result.tool_results),
                    "parse_warning": result.parse_warning if is_last else None,
                },
            )
            self.store.save_execution(execution)
            self.events.event(
                "agent_execution",
                workflow_id=ctx.workflow.workflow_id,
                execution_id=execution.execution_id,
                task_id=task.task_id,
                agent_id=agent_id,
                purpose=purpose,
                step=index + 1,
                status=status.value,
                error=execution.error,
            )

    def _save_execution(
        self,
        ctx: _RunContext,
        task: Task,
        agent_id: str,
        purpose: str,
        started_at: datetime,
        finished_at: datetime,
        status: ExecutionStatus,
        *,
        error: str | None,
        response: AgentRunResult | None,
        extra_metadata: dict | None = None,
        provider_name: str = "",
        model_name: str = "",
    ) -> None:
        provider = provider_name
        model = model_name
        prompt_tokens = 0
        completion_tokens = 0
        latency_ms: float | None = None
        if response is not None:
            provider = response.response.provider
            model = response.response.model
            prompt_tokens = response.response.usage.prompt_tokens
            completion_tokens = response.response.usage.completion_tokens
            latency_ms = response.response.latency_ms

        execution = AgentExecution(
            workflow_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            agent_id=agent_id,
            purpose=purpose,
            provider=provider,
            model=model,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            error=error,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            metadata=extra_metadata or {},
        )
        self.store.save_execution(execution)
        self.events.event(
            "agent_execution",
            workflow_id=ctx.workflow.workflow_id,
            execution_id=execution.execution_id,
            task_id=task.task_id,
            agent_id=agent_id,
            purpose=purpose,
            status=status.value,
            error=error,
        )

    def _collect_tool_evidence(
        self, ctx: _RunContext, task: Task
    ) -> list[ToolEvidence]:
        """Collect authoritative tool audit evidence from upstream tasks.

        The dependency closure includes normal dependencies and tasks created by
        agent-initiated delegation. Evidence is read from SQLite ``tool_calls``,
        never reconstructed from LLM prose.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        queue = list(task.dependencies) + list(
            task.metadata.get("delegated_task_ids") or []
        )
        while queue:
            task_id = queue.pop(0)
            if task_id in seen:
                continue
            seen.add(task_id)
            dependency = ctx.task_map.get(task_id)
            if dependency is None:
                continue
            ordered.append(task_id)
            queue.extend(dependency.dependencies)
            queue.extend(dependency.metadata.get("delegated_task_ids") or [])

        known = [task_id for task_id in ordered if task_id in ctx.task_map]
        known.sort(
            key=lambda task_id: (
                ctx.task_map[task_id].created_at,
                ctx.task_map[task_id].task_id,
            )
        )
        evidence: list[ToolEvidence] = []
        evidence_ids: set[str] = set()
        for task_id in known:
            for record in self.store.list_tool_calls(task_id=task_id):
                if record.tool_call_id in evidence_ids:
                    continue
                evidence_ids.add(record.tool_call_id)
                evidence.append(ToolEvidence.from_record(record))
        evidence.sort(key=lambda item: (item.started_at, item.tool_call_id))
        return evidence

    def _result_pairs(
        self, ctx: _RunContext, task_ids: list[str]
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for task_id in task_ids:
            dependency = ctx.task_map.get(task_id)
            if dependency is not None and dependency.result:
                pairs.append((dependency.description, dependency.result))
        return pairs

    def _snapshot_work_tasks(self, ctx: _RunContext) -> list[Task]:
        tasks = self._work_tasks(ctx)
        return sorted(tasks, key=lambda task: (task.created_at, task.task_id))

    def _work_tasks(self, ctx: _RunContext) -> list[Task]:
        return [
            task
            for task in ctx.task_map.values()
            if task.metadata.get("role") != "root"
        ]

    def _fail_task_without_execution(
        self, ctx: _RunContext, task: Task, error: str
    ) -> None:
        if task.is_terminal:
            return
        transition_task(task, TaskStatus.FAILED, error=error)
        self.store.save_task(task)
        self.events.event(
            "task_failed",
            workflow_id=ctx.workflow.workflow_id,
            task_id=task.task_id,
            agent_id=task.assigned_agent,
            error=error,
        )
        self._notify_group(
            ctx,
            "system",
            MessageType.ERROR,
            f"Task failed ({task.metadata.get('kind', 'generic')}): {error}",
            task_id=task.task_id,
        )
        self._sync_agent_task_state(task)

    def _sync_agent_task_state(self, task: Task) -> None:
        if task.metadata.get("role") == "root" or not task.assigned_agent:
            return
        agent = self.agents.get(task.assigned_agent)
        if agent is not None:
            agent.sync_task_state(task)

    def _fail_workflow(self, ctx: _RunContext, root: Task, error: str) -> None:
        ctx.workflow.status = WorkflowStatus.FAILED
        ctx.workflow.error = error
        if not root.is_terminal:
            transition_task(root, TaskStatus.FAILED, error=error)
            self.store.save_task(root)
        for task in self._work_tasks(ctx):
            if not task.is_terminal:
                try:
                    transition_task(task, TaskStatus.FAILED, error=error)
                    self.store.save_task(task)
                    self._sync_agent_task_state(task)
                except Exception:
                    logger.exception("failed to abort task %s", task.task_id)

    def _save_metrics(self, ctx: _RunContext, finished_at=None) -> None:
        workflow = ctx.workflow
        end = finished_at or workflow.finished_at or utc_now()
        workflow.metrics = compute_workflow_metrics(
            self.store.list_tasks(workflow.workflow_id),
            self.store.list_executions(workflow.workflow_id),
            started_at=workflow.created_at,
            finished_at=end,
            tool_calls=self.store.list_tool_calls(workflow.workflow_id),
            approvals=self.approval_service.repo.list_approvals(
                workflow.workflow_id
            ),
            clarifications=self.clarification_service.repo.list_clarifications(
                workflow.workflow_id
            ),
            workflow_events=self.workflow_control.repo.list_events(
                workflow.workflow_id
            ),
        )

    def _finish_workflow(self, ctx: _RunContext, root: Task) -> Workflow:
        workflow = ctx.workflow
        current = self.store.get_workflow(workflow.workflow_id)
        if current is not None and current.status in {
            WorkflowStatus.PAUSED,
            WorkflowStatus.CANCELLED,
        }:
            return current
        if workflow.status in {
            WorkflowStatus.PAUSED,
            WorkflowStatus.CANCELLED,
        }:
            return workflow
        final_task = (
            ctx.task_map.get(ctx.final_task_id) if ctx.final_task_id else None
        )

        if (
            workflow.status == WorkflowStatus.RUNNING
            and final_task is not None
            and final_task.status == TaskStatus.COMPLETED
            and final_task.result
        ):
            workflow.status = WorkflowStatus.COMPLETED
            workflow.final_result = final_task.result
            transition_task(
                root, TaskStatus.COMPLETED, result=workflow.final_result
            )
            self.store.save_task(root)
        else:
            if workflow.status != WorkflowStatus.FAILED:
                workflow.status = WorkflowStatus.FAILED
                workflow.error = self._first_failure(ctx) or (
                    "workflow finished without a completed final task"
                )
            if not root.is_terminal:
                transition_task(
                    root, TaskStatus.FAILED, error=workflow.error or "workflow failed"
                )
                self.store.save_task(root)
            for task in self._work_tasks(ctx):
                if not task.is_terminal:
                    try:
                        transition_task(
                            task, TaskStatus.FAILED, error=workflow.error or "workflow failed"
                        )
                        self.store.save_task(task)
                        self._sync_agent_task_state(task)
                    except Exception:
                        logger.exception("failed to abort task %s", task.task_id)

        workflow.finished_at = utc_now()
        self._save_metrics(ctx, workflow.finished_at)
        self.store.save_workflow(workflow)
        self.events.event(
            "workflow_completed" if workflow.status == WorkflowStatus.COMPLETED else "workflow_failed",
            workflow_id=workflow.workflow_id,
            status=workflow.status.value,
            error=workflow.error,
            metrics=workflow.metrics.model_dump(mode="json"),
            duration_seconds=workflow.duration_seconds(),
        )
        self._post_final_message(ctx)
        return workflow

    def _first_failure(self, ctx: _RunContext) -> str | None:
        failed = sorted(
            [task for task in self._work_tasks(ctx) if task.status == TaskStatus.FAILED],
            key=lambda task: (task.created_at, task.task_id),
        )
        if not failed:
            return None

        failed_ids = {task.task_id for task in failed}
        root_causes = []
        for task in failed:
            has_failed_dependency = any(dep in failed_ids for dep in task.dependencies)
            has_failed_child = any(
                child_id in failed_ids
                for child_id in (task.metadata.get("blocked_by") or [])
            )
            if not has_failed_dependency and not has_failed_child:
                root_causes.append(task)

        first = (root_causes or failed)[0]
        return f"{first.metadata.get('kind', 'task')} task failed: {first.error}"

    def _post_final_message(self, ctx: _RunContext) -> None:
        workflow = ctx.workflow
        if workflow.status == WorkflowStatus.COMPLETED:
            content = workflow.final_result or "(no final result)"
            message_type = MessageType.TASK_RESULT
        elif workflow.status == WorkflowStatus.CANCELLED:
            content = f"Workflow cancelled: {workflow.error or 'cancelled'}"
            message_type = MessageType.ERROR
        else:
            content = f"Workflow failed: {workflow.error or 'unknown error'}"
            message_type = MessageType.ERROR
        try:
            self.bus.send(
                sender=self.coordinator.agent_id,
                conversation_id=workflow.conversation_id,
                message_type=message_type,
                content=content,
                correlation_id=workflow.workflow_id,
                metadata={
                    "workflow_id": workflow.workflow_id,
                    "final": True,
                    "status": workflow.status.value,
                },
            )
        except Exception:
            logger.exception(
                "failed to post final message for workflow %s", workflow.workflow_id
            )


def _task_label(task: Task | None, fallback_id: str) -> str:
    if task is None:
        return fallback_id[:8]
    return str(
        task.metadata.get("plan_key")
        or task.metadata.get("kind")
        or fallback_id[:8]
    )
