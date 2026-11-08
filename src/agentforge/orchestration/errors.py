"""Orchestration-specific exceptions."""

from __future__ import annotations


class OrchestrationError(RuntimeError):
    """Base error for workflow orchestration failures."""


class PlanningError(OrchestrationError):
    """The Coordinator could not produce a valid execution plan."""

    def __init__(self, message: str, *, attempts: list | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []


class PlanValidationError(PlanningError):
    """The LLM plan is structurally invalid (cycles, unknown dependencies...)."""


class TaskAssignmentError(OrchestrationError):
    """No registered agent covers a task's required capabilities."""


class AgentOutputError(OrchestrationError):
    """An agent returned output that cannot be interpreted as an action."""


class WorkflowPauseRequested(OrchestrationError):
    """Raised internally when a workflow must pause for HITL."""

    def __init__(
        self,
        reason: str,
        *,
        task_id: str | None = None,
        approval_id: str | None = None,
        clarification_id: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.task_id = task_id
        self.approval_id = approval_id
        self.clarification_id = clarification_id


class WorkflowCancelledRequested(OrchestrationError):
    """Raised internally when a workflow is cancelled."""

    def __init__(self, reason: str, *, task_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.task_id = task_id
