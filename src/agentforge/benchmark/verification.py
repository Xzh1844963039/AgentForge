"""Objective task verification for the benchmark harness."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from agentforge.benchmark.execution import BenchmarkRuntimeHandle
from agentforge.benchmark.models import (
    ApprovalCheck,
    BenchmarkTask,
    CheckResult,
    ClarificationCheck,
    DuplicateEffectCheck,
    FileCheck,
    PythonCheck,
    TaskGraphCheck,
    ToolCallCheck,
    VerificationResult,
    VerificationStatus,
    WorkflowCheck,
)
from agentforge.models.clarification import ClarificationRequest
from agentforge.models.task import Task
from agentforge.models.tool import (
    ApprovalRequest,
    ToolCallRecord,
    ToolResultStatus,
)
from agentforge.models.workflow import Workflow
from agentforge.sandbox.base import SandboxError, SandboxUnavailableError
from agentforge.tools.approval import arguments_digest


class BenchmarkVerifier:
    def __init__(self, handle: BenchmarkRuntimeHandle, task: BenchmarkTask) -> None:
        self.handle = handle
        self.task = task

    async def verify(self, workflow: Workflow) -> VerificationResult:
        runtime = self.handle.runtime
        workflow_id = workflow.workflow_id
        fresh = runtime.get_workflow(workflow_id) or workflow
        tasks = runtime.list_tasks(workflow_id)
        tool_calls = runtime.list_tool_calls(workflow_id)
        approvals = runtime.list_approvals(workflow_id)
        clarifications = runtime.list_clarifications(workflow_id)
        events = runtime.list_workflow_events(workflow_id)
        spec = self.task.verification
        checks: list[CheckResult] = []
        skipped: list[str] = []

        for file_check in spec.files:
            checks.append(self._check_file(file_check))
        for tool_check in spec.tool_calls:
            checks.append(self._check_tool_calls(tool_check, tool_calls))
        if spec.workflow is not None:
            checks.append(self._check_workflow(spec.workflow, fresh, events))
        for approval_check in spec.approvals:
            checks.append(self._check_approval(approval_check, approvals))
        for clarification_check in spec.clarifications:
            checks.append(
                self._check_clarification(clarification_check, clarifications)
            )
        if spec.task_graph is not None:
            checks.append(self._check_task_graph(spec.task_graph, tasks))
        if spec.duplicate_effects is not None:
            checks.append(
                self._check_duplicate_effects(spec.duplicate_effects, tool_calls)
            )
        for python_check in spec.python:
            check, unavailable = await self._check_python(python_check)
            checks.append(check)
            if unavailable:
                skipped.append(check.detail)

        failures = [check for check in checks if check.passed is False]
        if failures:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                checks=checks,
                failure_reason=failures[0].detail,
                manual_review_reason=spec.manual_review_reason,
            )
        if skipped:
            return VerificationResult(
                status=VerificationStatus.SKIPPED,
                checks=checks,
                failure_reason=skipped[0],
                manual_review_reason=spec.manual_review_reason,
            )
        if spec.manual_review_reason:
            return VerificationResult(
                status=VerificationStatus.NEEDS_REVIEW,
                checks=checks,
                manual_review_reason=spec.manual_review_reason,
            )
        if not checks:
            return VerificationResult(
                status=VerificationStatus.NEEDS_REVIEW,
                checks=checks,
                failure_reason="no executable verification checks",
            )
        return VerificationResult(
            status=VerificationStatus.PASSED,
            checks=checks,
        )

    def _check_file(self, check: FileCheck) -> CheckResult:
        agent_id = self.handle.agent_for(check.agent)
        workspace = self.handle.runtime.workspaces.workspace_for(agent_id)
        target = (workspace / check.path).resolve()
        name = f"file:{agent_id}:{check.path}"
        if not target.is_relative_to(workspace.resolve()):
            return CheckResult(name=name, passed=False, detail="path escapes workspace")
        if check.must_not_exist:
            if target.exists():
                return CheckResult(
                    name=name,
                    passed=False,
                    detail="file exists but must not exist",
                )
            return CheckResult(name=name, passed=True, detail="file absent as expected")
        if not target.exists():
            if check.must_exist:
                return CheckResult(name=name, passed=False, detail="file does not exist")
            return CheckResult(name=name, passed=True, detail="file absent as expected")
        if not target.is_file():
            return CheckResult(name=name, passed=False, detail="not a regular file")
        text = target.read_text(encoding="utf-8", errors="replace")
        haystack = text if check.case_sensitive else text.lower()
        for expected in check.contains:
            needle = expected if check.case_sensitive else expected.lower()
            if needle not in haystack:
                return CheckResult(
                    name=name,
                    passed=False,
                    detail=f"missing required text: {expected!r}",
                )
        for forbidden in check.not_contains:
            needle = forbidden if check.case_sensitive else forbidden.lower()
            if needle in haystack:
                return CheckResult(
                    name=name,
                    passed=False,
                    detail=f"forbidden text present: {forbidden!r}",
                )
        size = len(text.encode("utf-8"))
        if size < check.min_bytes:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"file is {size} bytes, expected at least {check.min_bytes}",
            )
        if check.exact_content is not None and text != check.exact_content:
            return CheckResult(
                name=name,
                passed=False,
                detail="file content does not match exact expected content",
            )
        return CheckResult(name=name, passed=True, detail=f"{size} bytes")

    @staticmethod
    def _check_tool_calls(
        check: ToolCallCheck, records: list[ToolCallRecord]
    ) -> CheckResult:
        matches = []
        for record in records:
            if check.tool_name is not None and record.tool_name != check.tool_name:
                continue
            if check.status is not None and record.status != check.status:
                continue
            if (
                check.permission_decision is not None
                and record.permission_decision != check.permission_decision
            ):
                continue
            if (
                check.approval_status is not None
                and record.approval_status != check.approval_status
            ):
                continue
            if check.metadata_reason is not None and (
                record.metadata.get("reason") != check.metadata_reason
            ):
                continue
            if check.error_contains is not None and check.error_contains.lower() not in (
                record.error or ""
            ).lower():
                continue
            if check.output_contains is not None:
                compact = str(record.output).lower()
                if check.output_contains.lower() not in compact:
                    continue
            matches.append(record)
        label = check.tool_name or "*"
        name = f"tool_calls:{label}"
        if len(matches) < check.min_count:
            return CheckResult(
                name=name,
                passed=False,
                detail=(
                    f"expected at least {check.min_count} matching tool calls, "
                    f"found {len(matches)}"
                ),
            )
        if check.max_count is not None and len(matches) > check.max_count:
            return CheckResult(
                name=name,
                passed=False,
                detail=(
                    f"expected at most {check.max_count} matching tool calls, "
                    f"found {len(matches)}"
                ),
            )
        return CheckResult(name=name, passed=True, detail=f"{len(matches)} match(es)")

    @staticmethod
    def _check_workflow(
        check: WorkflowCheck, workflow: Workflow, events: list[Any]
    ) -> CheckResult:
        name = "workflow"
        if check.status is not None and workflow.status != check.status:
            return CheckResult(
                name=name,
                passed=False,
                detail=(
                    f"workflow status is {workflow.status.value}, "
                    f"expected {check.status.value}"
                ),
            )
        final = workflow.final_result or ""
        for expected in check.final_contains:
            if expected.lower() not in final.lower():
                return CheckResult(
                    name=name,
                    passed=False,
                    detail=f"final report missing {expected!r}",
                )
        for forbidden in check.final_not_contains:
            if forbidden.lower() in final.lower():
                return CheckResult(
                    name=name,
                    passed=False,
                    detail=f"final report contains forbidden {forbidden!r}",
                )
        if check.error_contains is not None and check.error_contains.lower() not in (
            workflow.error or ""
        ).lower():
            return CheckResult(
                name=name,
                passed=False,
                detail=f"workflow error missing {check.error_contains!r}",
            )
        event_types = [event.event_type for event in events]
        if event_types.count("workflow_paused") < check.min_pauses:
            return CheckResult(
                name=name,
                passed=False,
                detail="workflow did not pause as expected",
            )
        if event_types.count("workflow_resumed") < check.min_resumes:
            return CheckResult(
                name=name,
                passed=False,
                detail="workflow did not resume as expected",
            )
        if event_types.count("workflow_cancelled") < check.min_cancellations:
            return CheckResult(
                name=name,
                passed=False,
                detail="workflow was not cancelled",
            )
        return CheckResult(name=name, passed=True, detail=workflow.status.value)

    @staticmethod
    def _check_approval(
        check: ApprovalCheck, approvals: list[ApprovalRequest]
    ) -> CheckResult:
        matches = []
        for approval in approvals:
            if check.tool_name is not None and approval.tool_name != check.tool_name:
                continue
            if check.status is not None and approval.status != check.status:
                continue
            if check.consumed is not None and (
                approval.consumed_at is not None
            ) != check.consumed:
                continue
            matches.append(approval)
        name = f"approval:{check.tool_name or '*'}"
        if len(matches) < check.min_count:
            return CheckResult(
                name=name,
                passed=False,
                detail=(
                    f"expected at least {check.min_count} matching approvals, "
                    f"found {len(matches)}"
                ),
            )
        if check.max_count is not None and len(matches) > check.max_count:
            return CheckResult(
                name=name,
                passed=False,
                detail=(
                    f"expected at most {check.max_count} matching approvals, "
                    f"found {len(matches)}"
                ),
            )
        return CheckResult(name=name, passed=True, detail=f"{len(matches)} match(es)")

    @staticmethod
    def _check_clarification(
        check: ClarificationCheck, clarifications: list[ClarificationRequest]
    ) -> CheckResult:
        matches = []
        for clarification in clarifications:
            if check.status is not None and clarification.status.value != check.status:
                continue
            if check.answer_contains is not None and (
                check.answer_contains.lower() not in (clarification.answer or "").lower()
            ):
                continue
            matches.append(clarification)
        name = f"clarification:{check.status or '*'}"
        if len(matches) < check.min_count:
            return CheckResult(
                name=name,
                passed=False,
                detail=(
                    f"expected at least {check.min_count} matching clarifications, "
                    f"found {len(matches)}"
                ),
            )
        return CheckResult(name=name, passed=True, detail=f"{len(matches)} match(es)")

    @staticmethod
    def _check_task_graph(check: TaskGraphCheck, tasks: list[Task]) -> CheckResult:
        work = [task for task in tasks if task.metadata.get("role") != "root"]
        delegated = [
            task for task in work if task.metadata.get("origin") == "delegation"
        ]
        completed = [task for task in work if task.status.value == "completed"]
        failed = [task for task in work if task.status.value == "failed"]
        max_depth = max((task.depth for task in work), default=0)
        reassignments = sum(
            1 for task in work if task.metadata.get("reassigned_from")
        )
        name = "task_graph"
        if len(work) < check.min_tasks:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"expected at least {check.min_tasks} work tasks",
            )
        if len(completed) < check.min_completed:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"expected at least {check.min_completed} completed tasks",
            )
        if len(failed) < check.min_failed:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"expected at least {check.min_failed} failed tasks",
            )
        if len(delegated) < check.min_delegated_tasks:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"expected at least {check.min_delegated_tasks} delegated tasks",
            )
        if max_depth < check.min_max_depth:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"delegation depth {max_depth} < {check.min_max_depth}",
            )
        if reassignments < check.min_reassignments:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"reassignments {reassignments} < {check.min_reassignments}",
            )
        if check.all_completed and len(completed) != len(work):
            return CheckResult(
                name=name,
                passed=False,
                detail="not all work tasks completed",
            )
        return CheckResult(
            name=name,
            passed=True,
            detail=(
                f"tasks={len(work)} completed={len(completed)} "
                f"delegated={len(delegated)} max_depth={max_depth}"
            ),
        )

    @staticmethod
    def _check_duplicate_effects(
        check: DuplicateEffectCheck, records: list[ToolCallRecord]
    ) -> CheckResult:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for record in records:
            if record.status != ToolResultStatus.SUCCESS:
                continue
            if check.tools and record.tool_name not in check.tools:
                continue
            counts[
                (record.tool_name, arguments_digest(record.arguments))
            ] += 1
        offenders = {
            key: count
            for key, count in counts.items()
            if count > check.max_per_signature
        }
        name = "duplicate_effects"
        if offenders:
            preview = ", ".join(
                f"{tool}:{digest[:8]}={count}"
                for (tool, digest), count in offenders.items()
            )
            return CheckResult(
                name=name,
                passed=False,
                detail=f"duplicate successful effects: {preview}",
            )
        return CheckResult(name=name, passed=True, detail="no duplicate effects")

    async def _check_python(
        self, check: PythonCheck
    ) -> tuple[CheckResult, bool]:
        agent_id = self.handle.agent_for(check.agent)
        workspace = self.handle.runtime.workspaces.workspace_for(agent_id)
        target = (workspace / check.source_path).resolve()
        name = f"python:{agent_id}:{check.source_path}"
        if not target.is_relative_to(workspace.resolve()) or not target.is_file():
            return (
                CheckResult(
                    name=name,
                    passed=False,
                    detail=f"source file missing: {check.source_path}",
                ),
                False,
            )
        sandbox = self.handle.runtime.sandboxes.get(agent_id)
        try:
            result = await sandbox.execute_python(
                check.test_code,
                filename=f"verify_{self.task.task_id}.py",
                timeout_seconds=check.timeout_seconds,
            )
        except SandboxUnavailableError as exc:
            return (
                CheckResult(
                    name=name,
                    passed=None,
                    detail=f"Docker unavailable, objective check SKIPPED: {exc}",
                ),
                True,
            )
        except SandboxError as exc:
            return (
                CheckResult(
                    name=name,
                    passed=None,
                    detail=f"sandbox error, objective check SKIPPED: {exc}",
                ),
                True,
            )
        except Exception as exc:
            return (
                CheckResult(
                    name=name,
                    passed=False,
                    detail=f"verifier execution error: {type(exc).__name__}: {exc}",
                ),
                False,
            )
        if not result.success:
            detail = (
                f"exit={result.exit_code} timed_out={result.timed_out}; "
                f"stdout={result.stdout[-800:]!r}; stderr={result.stderr[-800:]!r}"
            )
            return CheckResult(name=name, passed=False, detail=detail), False
        return (
            CheckResult(
                name=name,
                passed=True,
                detail=f"exit=0 stdout={result.stdout[-400:]!r}",
            ),
            False,
        )
