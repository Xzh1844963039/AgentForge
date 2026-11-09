"""Per-run and aggregate benchmark metrics."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Iterable

from agentforge.benchmark.execution import BenchmarkRuntimeHandle
from agentforge.benchmark.models import (
    AggregateMetrics,
    BenchmarkRun,
    BenchmarkTask,
    CategoryAggregate,
    RunMetrics,
    RunStatus,
    TaskCategory,
    VerificationResult,
    VerificationStatus,
)
from agentforge.models.task import TaskStatus
from agentforge.models.tool import ToolResultStatus
from agentforge.models.workflow import Workflow
from agentforge.tools.approval import arguments_digest


def collect_run_metrics(
    handle: BenchmarkRuntimeHandle,
    task: BenchmarkTask,
    workflow: Workflow,
    verification: VerificationResult,
    *,
    end_to_end_seconds: float,
    active_seconds: float,
    human_wait_seconds: float,
    run_status: RunStatus,
) -> RunMetrics:
    runtime = handle.runtime
    workflow_id = workflow.workflow_id
    tasks = runtime.list_tasks(workflow_id)
    work_tasks = [item for item in tasks if item.metadata.get("role") != "root"]
    completed = [
        item for item in work_tasks if item.status == TaskStatus.COMPLETED
    ]
    executions = runtime.store.list_executions(workflow_id)
    tool_calls = runtime.list_tool_calls(workflow_id)
    failures = [
        call for call in tool_calls if call.status != ToolResultStatus.SUCCESS
    ]
    signatures: dict[tuple[str, str], int] = defaultdict(int)
    for call in tool_calls:
        signatures[
            (call.tool_name, arguments_digest(call.arguments))
        ] += 1
    retry_count = sum(max(0, count - 1) for count in signatures.values())
    delegated = [
        item for item in work_tasks if item.metadata.get("origin") == "delegation"
    ]
    delegated_completed = [
        item for item in delegated if item.status == TaskStatus.COMPLETED
    ]
    try:
        retrievals = runtime.memory.list_retrieval_events(workflow_id=workflow_id)
    except Exception:
        retrievals = []
    try:
        feedback = runtime.memory.list_feedback(workflow_id=workflow_id)
    except Exception:
        feedback = []
    approvals = runtime.list_approvals(workflow_id)
    clarifications = runtime.list_clarifications(workflow_id)
    total_tokens = sum(
        execution.prompt_tokens + execution.completion_tokens
        for execution in executions
    )
    plan_attempts = [
        execution for execution in executions if execution.purpose == "plan"
    ]
    plan_failures = [
        execution for execution in plan_attempts if execution.error is not None
    ]
    invalid_json = [
        execution
        for execution in executions
        if "AgentOutputError" in (execution.error or "")
        or "invalid agent action" in (execution.error or "").lower()
        or execution.metadata.get("parse_error") is not None
    ]
    structured_repairs = sum(
        int(execution.metadata.get("repair_count", 0) or 0)
        for execution in executions
        if execution.metadata.get("final")
    )
    schema_failures = [
        call
        for call in tool_calls
        if call.metadata.get("reason") == "schema_validation"
    ]
    successful_tools = [
        call for call in tool_calls if call.status == ToolResultStatus.SUCCESS
    ]
    return RunMetrics(
        goal_success=verification.status == VerificationStatus.PASSED,
        workflow_completed=workflow.status.value == "completed",
        subtask_completion_rate=(
            len(completed) / len(work_tasks) if work_tasks else 0.0
        ),
        end_to_end_seconds=max(0.0, end_to_end_seconds),
        active_seconds=max(0.0, active_seconds),
        human_wait_seconds=max(0.0, human_wait_seconds),
        llm_calls=len(executions),
        prompt_tokens=sum(item.prompt_tokens for item in executions),
        completion_tokens=sum(item.completion_tokens for item in executions),
        total_tokens=total_tokens,
        tool_calls=len(tool_calls),
        tool_failures=len(failures),
        tool_failure_rate=(len(failures) / len(tool_calls) if tool_calls else 0.0),
        retry_count=retry_count,
        delegation_count=len(delegated),
        delegation_success_rate=(
            len(delegated_completed) / len(delegated) if delegated else 0.0
        ),
        memory_retrieval_count=len(retrievals),
        memory_usage_count=sum(1 for item in feedback if item.used),
        approval_count=len(approvals),
        clarification_count=len(clarifications),
        recovery_success=(
            task.category == TaskCategory.FAILURE_RECOVERY
            and verification.status == VerificationStatus.PASSED
        ),
        workflow_crash_or_timeout=run_status
        in {RunStatus.TIMEOUT, RunStatus.ERROR},
        tool_budget_exceeded=len(tool_calls) > task.tool_budget,
        token_budget_exceeded=handle.provider.budget_exceeded,
        time_budget_exceeded=active_seconds > task.time_budget_seconds,
        invalid_json_rate=(
            len(invalid_json) / len(executions) if executions else 0.0
        ),
        structured_repair_count=structured_repairs,
        tool_schema_error_rate=(
            len(schema_failures) / len(tool_calls) if tool_calls else 0.0
        ),
        plan_validation_failure_rate=(
            len(plan_failures) / len(plan_attempts) if plan_attempts else 0.0
        ),
        tool_calling_success_rate=(
            len(successful_tools) / len(tool_calls) if tool_calls else 0.0
        ),
        agent_completion_rate=(
            len(completed) / len(work_tasks) if work_tasks else 0.0
        ),
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def aggregate_runs(runs: Iterable[BenchmarkRun]) -> AggregateMetrics:
    records = list(runs)
    if not records:
        return AggregateMetrics(notes=["no runs"])
    passed = sum(1 for run in records if run.run_status == RunStatus.PASSED)
    failed = sum(1 for run in records if run.run_status == RunStatus.FAILED)
    review = sum(
        1 for run in records if run.run_status == RunStatus.NEEDS_REVIEW
    )
    skipped = sum(1 for run in records if run.run_status == RunStatus.SKIPPED)
    timeout = sum(1 for run in records if run.run_status == RunStatus.TIMEOUT)
    errors = sum(1 for run in records if run.run_status == RunStatus.ERROR)
    e2e = [run.metrics.end_to_end_seconds for run in records]
    active = [run.metrics.active_seconds for run in records]
    total_tokens = sum(run.metrics.total_tokens for run in records)
    tool_calls = sum(run.metrics.tool_calls for run in records)
    tool_failures = sum(run.metrics.tool_failures for run in records)
    delegations = sum(run.metrics.delegation_count for run in records)
    delegation_success = [
        run.metrics.delegation_success_rate
        for run in records
        if run.metrics.delegation_count
    ]
    recovery = [
        run
        for run in records
        if run.category == TaskCategory.FAILURE_RECOVERY
    ]
    recovery_passed = sum(
        1 for run in recovery if run.metrics.recovery_success
    )

    by_category: list[CategoryAggregate] = []
    grouped: dict[TaskCategory, list[BenchmarkRun]] = defaultdict(list)
    for run in records:
        grouped[run.category].append(run)
    for category, category_runs in sorted(
        grouped.items(), key=lambda item: item[0].value
    ):
        category_passed = sum(
            1 for run in category_runs if run.run_status == RunStatus.PASSED
        )
        by_category.append(
            CategoryAggregate(
                category=category,
                total=len(category_runs),
                passed=category_passed,
                failed=sum(
                    1
                    for run in category_runs
                    if run.run_status == RunStatus.FAILED
                ),
                needs_review=sum(
                    1
                    for run in category_runs
                    if run.run_status == RunStatus.NEEDS_REVIEW
                ),
                skipped=sum(
                    1
                    for run in category_runs
                    if run.run_status == RunStatus.SKIPPED
                ),
                success_rate=(
                    category_passed / len(category_runs)
                    if category_runs
                    else 0.0
                ),
                average_end_to_end_seconds=statistics.fmean(
                    run.metrics.end_to_end_seconds for run in category_runs
                ),
                average_active_seconds=statistics.fmean(
                    run.metrics.active_seconds for run in category_runs
                ),
                average_tool_calls=statistics.fmean(
                    run.metrics.tool_calls for run in category_runs
                ),
                average_llm_calls=statistics.fmean(
                    run.metrics.llm_calls for run in category_runs
                ),
            )
        )

    return AggregateMetrics(
        total_runs=len(records),
        passed=passed,
        failed=failed,
        needs_review=review,
        skipped=skipped,
        timeout=timeout,
        errors=errors,
        goal_completion_rate=passed / len(records),
        average_end_to_end_seconds=statistics.fmean(e2e),
        median_end_to_end_seconds=statistics.median(e2e),
        p95_end_to_end_seconds=_percentile(e2e, 0.95),
        average_active_seconds=statistics.fmean(active),
        total_human_wait_seconds=sum(
            run.metrics.human_wait_seconds for run in records
        ),
        total_llm_calls=sum(run.metrics.llm_calls for run in records),
        total_tokens=total_tokens,
        average_tokens=total_tokens / len(records),
        total_tool_calls=tool_calls,
        tool_failure_rate=(
            tool_failures / tool_calls if tool_calls else 0.0
        ),
        total_retries=sum(run.metrics.retry_count for run in records),
        total_structured_repairs=sum(
            run.metrics.structured_repair_count for run in records
        ),
        total_delegations=delegations,
        delegation_success_rate=(
            sum(delegation_success) / len(delegation_success)
            if delegation_success
            else 0.0
        ),
        total_memory_retrievals=sum(
            run.metrics.memory_retrieval_count for run in records
        ),
        total_memory_usage=sum(
            run.metrics.memory_usage_count for run in records
        ),
        total_approvals=sum(run.metrics.approval_count for run in records),
        total_clarifications=sum(
            run.metrics.clarification_count for run in records
        ),
        recovery_success_rate=(
            recovery_passed / len(recovery) if recovery else 0.0
        ),
        crash_or_timeout_rate=(
            (timeout + errors) / len(records) if records else 0.0
        ),
        by_category=by_category,
        notes=["Mock fixture decisions are harness validation, not LLM quality."],
    )
