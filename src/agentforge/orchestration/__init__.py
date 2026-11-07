"""Task orchestration package.

``TaskOrchestrator`` is imported lazily to avoid a circular import between the
agent framework (which uses orchestration errors) and the engine (which imports
agents).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentforge.orchestration.assignment import CapabilityAssigner
from agentforge.orchestration.errors import (
    AgentOutputError,
    OrchestrationError,
    PlanningError,
    PlanValidationError,
    TaskAssignmentError,
)
from agentforge.orchestration.planner import validate_plan
from agentforge.orchestration.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTaskTransition,
    transition_task,
)

if TYPE_CHECKING:
    from agentforge.orchestration.orchestrator import TaskOrchestrator

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AgentOutputError",
    "CapabilityAssigner",
    "InvalidTaskTransition",
    "OrchestrationError",
    "PlanValidationError",
    "PlanningError",
    "TaskAssignmentError",
    "TaskOrchestrator",
    "transition_task",
    "validate_plan",
]


def __getattr__(name: str):
    if name == "TaskOrchestrator":
        from agentforge.orchestration.orchestrator import TaskOrchestrator

        return TaskOrchestrator
    raise AttributeError(name)
