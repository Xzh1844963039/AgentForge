"""Failure classification for benchmark records."""

from __future__ import annotations

from agentforge.benchmark.models import (
    BenchmarkRun,
    BenchmarkTask,
    FailureAnalysis,
    RunStatus,
    VerificationResult,
    VerificationStatus,
)
from agentforge.models.task import Task
from agentforge.models.tool import ToolCallRecord, ToolResultStatus


def analyze_failure(
    task: BenchmarkTask,
    run_status: RunStatus,
    verification: VerificationResult | None,
    workflow_error: str | None,
    tool_calls: list[ToolCallRecord],
    tasks: list[Task],
) -> FailureAnalysis | None:
    if run_status == RunStatus.PASSED:
        return None
    if run_status == RunStatus.NEEDS_REVIEW:
        return FailureAnalysis(
            category="manual_review_required",
            reason=(verification.manual_review_reason if verification else None)
            or "objective verification requires human review",
            retryable=False,
        )
    if run_status == RunStatus.SKIPPED:
        return FailureAnalysis(
            category="verification_skipped",
            reason=(verification.failure_reason if verification else None)
            or "verification could not run",
            retryable=True,
        )
    if run_status == RunStatus.TIMEOUT:
        return FailureAnalysis(
            category="time_budget_exceeded",
            reason=f"task exceeded {task.time_budget_seconds:g}s",
            evidence={"time_budget_seconds": task.time_budget_seconds},
            retryable=True,
        )
    if run_status == RunStatus.ERROR:
        return FailureAnalysis(
            category="runtime_error",
            reason=workflow_error or "runtime exception",
            evidence={"workflow_error": workflow_error},
            retryable=True,
        )

    error = (workflow_error or "").lower()
    if "reconciliation" in error:
        return FailureAnalysis(
            category="needs_reconciliation",
            reason=workflow_error or "unresolved high-risk side effect",
            evidence={"workflow_error": workflow_error},
            retryable=False,
        )
    if "planning failed" in error or "plan validation" in error:
        return FailureAnalysis(
            category="planning_failure",
            reason=workflow_error or "planning failed",
            retryable=True,
        )
    if "permission denied" in error or "lacks required permissions" in error:
        return FailureAnalysis(
            category="permission_denied",
            reason=workflow_error or "permission denied",
            retryable=False,
        )
    if "maximum delegation depth" in error:
        return FailureAnalysis(
            category="delegation_limit",
            reason=workflow_error or "delegation depth limit",
            retryable=False,
        )
    if "dependency failed" in error:
        return FailureAnalysis(
            category="dependency_failure",
            reason=workflow_error or "dependency failed",
            retryable=False,
        )
    schema_errors = [
        call
        for call in tool_calls
        if call.metadata.get("reason") == "schema_validation"
    ]
    if schema_errors:
        return FailureAnalysis(
            category="tool_schema_error",
            reason=schema_errors[0].error or "tool schema validation failed",
            evidence={"tool_call_id": schema_errors[0].tool_call_id},
            retryable=True,
        )
    timeouts = [
        call for call in tool_calls if call.status == ToolResultStatus.TIMEOUT
    ]
    if timeouts:
        return FailureAnalysis(
            category="tool_timeout",
            reason=timeouts[0].error or "tool timed out",
            evidence={"tool_name": timeouts[0].tool_name},
            retryable=True,
        )
    failed_tasks = [task for task in tasks if task.status.value == "failed"]
    if failed_tasks:
        return FailureAnalysis(
            category="task_failed",
            reason=failed_tasks[0].error or "task failed",
            evidence={"task_id": failed_tasks[0].task_id},
            retryable=True,
        )
    if verification is not None and verification.status == VerificationStatus.FAILED:
        return FailureAnalysis(
            category="verification_failure",
            reason=verification.failure_reason or "objective verification failed",
            evidence={
                "checks": [
                    check.model_dump(mode="json")
                    for check in verification.checks
                    if check.passed is False
                ]
            },
            retryable=False,
        )
    return FailureAnalysis(
        category="unknown_failure",
        reason=workflow_error or "run failed without a classified cause",
        retryable=False,
    )


def attach_failure(
    run: BenchmarkRun,
    task: BenchmarkTask,
    tool_calls: list[ToolCallRecord],
    tasks: list[Task],
) -> None:
    run.failure = analyze_failure(
        task,
        run.run_status,
        run.verification,
        run.workflow_error,
        tool_calls,
        tasks,
    )
