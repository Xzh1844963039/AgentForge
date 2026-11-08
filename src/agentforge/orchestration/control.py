"""Workflow pause/resume/cancel control and event-backed metrics."""

from __future__ import annotations

import logging
from typing import Any

from agentforge.interaction.repository import InteractionRepository
from agentforge.models.workflow import (
    WorkflowEvent,
    WorkflowStatus,
)
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger

logger = logging.getLogger("agentforge.orchestration.control")


class WorkflowControlError(RuntimeError):
    pass


class WorkflowControlService:
    def __init__(self, store: SQLiteStore, events: EventLogger) -> None:
        self.store = store
        self.events = events
        self.repo = InteractionRepository(store)

    def get_status(self, workflow_id: str) -> WorkflowStatus:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowControlError(f"unknown workflow {workflow_id!r}")
        return workflow.status

    def pause(self, workflow_id: str, *, reason: str = "human_pause") -> WorkflowStatus:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowControlError(f"unknown workflow {workflow_id!r}")
        if workflow.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            raise WorkflowControlError(
                f"cannot pause workflow in status {workflow.status.value}"
            )
        if workflow.status == WorkflowStatus.PAUSED:
            return workflow.status
        workflow.status = WorkflowStatus.PAUSED
        workflow.metadata["pause_count"] = int(
            workflow.metadata.get("pause_count", 0)
        ) + 1
        self.store.save_workflow(workflow)
        self._record(workflow_id, "workflow_paused", reason=reason)
        return workflow.status

    def resume(self, workflow_id: str) -> WorkflowStatus:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowControlError(f"unknown workflow {workflow_id!r}")
        if workflow.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            raise WorkflowControlError(
                f"cannot resume workflow in status {workflow.status.value}"
            )
        if workflow.status == WorkflowStatus.RUNNING:
            return workflow.status
        workflow.status = WorkflowStatus.RUNNING
        workflow.metadata["resume_count"] = int(
            workflow.metadata.get("resume_count", 0)
        ) + 1
        self.store.save_workflow(workflow)
        self._record(workflow_id, "workflow_resumed")
        return workflow.status

    def cancel(
        self, workflow_id: str, *, reason: str = "human_cancel"
    ) -> WorkflowStatus:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowControlError(f"unknown workflow {workflow_id!r}")
        if workflow.status == WorkflowStatus.CANCELLED:
            return workflow.status
        if workflow.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
            raise WorkflowControlError(
                f"cannot cancel workflow in status {workflow.status.value}"
            )
        workflow.status = WorkflowStatus.CANCELLED
        workflow.metadata["cancel_count"] = int(
            workflow.metadata.get("cancel_count", 0)
        ) + 1
        self.store.save_workflow(workflow)
        self._record(workflow_id, "workflow_cancelled", reason=reason)
        return workflow.status

    def metrics(self, workflow_id: str) -> dict[str, Any]:
        events = self.repo.list_events(workflow_id)
        pauses = sum(1 for item in events if item.event_type == "workflow_paused")
        resumes = sum(1 for item in events if item.event_type == "workflow_resumed")
        cancellations = sum(
            1 for item in events if item.event_type == "workflow_cancelled"
        )
        return {
            "workflow_pauses": pauses,
            "workflow_resumes": resumes,
            "workflow_cancellations": cancellations,
        }

    def _record(self, workflow_id: str, event_type: str, **metadata: Any) -> None:
        self.events.event(event_type, workflow_id=workflow_id, **metadata)
        self.repo.append_event(
            WorkflowEvent(
                workflow_id=workflow_id,
                event_type=event_type,
                metadata=metadata,
            )
        )
