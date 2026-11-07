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
