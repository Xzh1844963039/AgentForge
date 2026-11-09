"""Benchmark batch runner with real runtime execution and resumable records."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from uuid import uuid4
from datetime import datetime
from pathlib import Path

from agentforge.benchmark.dataset import load_dataset, select_tasks
from agentforge.benchmark.execution import (
    BenchmarkRuntimeHandle,
    create_benchmark_runtime,
)
from agentforge.benchmark.failures import attach_failure
from agentforge.benchmark.metrics import aggregate_runs, collect_run_metrics
from agentforge.benchmark.models import (
    AggregateMetrics,
    BenchmarkBatch,
    BenchmarkConfig,
    BenchmarkRun,
    BenchmarkTask,
    HumanInteractionRecord,
    RunMetrics,
    RunStatus,
    VerificationResult,
    VerificationStatus,
)
from agentforge.benchmark.report import (
    append_jsonl,
    read_batch_manifest,
    read_jsonl,
    render_benchmark_report,
    write_batch_manifest,
    write_csv,
    write_jsonl,
)
from agentforge.benchmark.verification import BenchmarkVerifier
from agentforge.llm.base import BaseLLMProvider
from agentforge.models.common import utc_now
from agentforge.models.tool import ApprovalStatus
from agentforge.models.workflow import Workflow, WorkflowStatus

logger = logging.getLogger("agentforge.benchmark.runner")


@dataclass
class BatchOutcome:
    batch: BenchmarkBatch
    runs: list[BenchmarkRun]
    aggregate: AggregateMetrics
    batch_dir: Path
    report_path: Path
    jsonl_path: Path
    json_path: Path
    csv_path: Path
    failures_path: Path


def git_commit_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


class BenchmarkRunner:
    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        provider_override: BaseLLMProvider | None = None,
    ) -> None:
        self.config = config
        self.provider_override = provider_override
        self.dataset = load_dataset(config.dataset_path)
        self.git_commit = git_commit_hash()

    def _tasks(self) -> list[BenchmarkTask]:
        return select_tasks(
            self.dataset,
            task_ids=self.config.task_ids or None,
            categories=self.config.categories or None,
            max_tasks=self.config.max_tasks,
        )

    async def run_batch(self) -> BatchOutcome:
        tasks = self._tasks()
        batch_dir = self._prepare_batch_dir()
        manifest_path = batch_dir / "batch.json"
        runs_path = batch_dir / "runs.jsonl"
        existing = read_jsonl(runs_path) if self.config.resume else []
        if manifest_path.exists() and self.config.resume:
            batch = read_batch_manifest(manifest_path)
            batch.status = "running"
        else:
            batch = BenchmarkBatch(
                dataset_id=self.dataset.dataset_id,
                dataset_path=str(Path(self.config.dataset_path).resolve()),
                git_commit=self.git_commit,
                config=self.config,
                task_ids=[task.task_id for task in tasks],
            )
            write_batch_manifest(manifest_path, batch)
        completed_keys = {(run.task_id, run.repeat_index) for run in existing}
        runs = list(existing)
        for task in tasks:
            for repeat_index in range(self.config.repeats):
                key = (task.task_id, repeat_index)
                if key in completed_keys:
                    continue
                run = await self.run_task(
                    task,
                    batch_id=batch.batch_id,
                    repeat_index=repeat_index,
                    batch_dir=batch_dir,
                )
                runs.append(run)
                completed_keys.add(key)
                append_jsonl(runs_path, run)
                batch.run_count = len(runs)
                write_batch_manifest(manifest_path, batch)
                if self.config.fail_fast and run.run_status not in {
                    RunStatus.PASSED,
                    RunStatus.NEEDS_REVIEW,
                }:
                    batch.status = "failed"
                    batch.finished_at = utc_now()
                    write_batch_manifest(manifest_path, batch)
                    return self._finalize(batch_dir, batch, runs, manifest_path)
        batch.status = "completed"
        batch.finished_at = utc_now()
        batch.run_count = len(runs)
        write_batch_manifest(manifest_path, batch)
        return self._finalize(batch_dir, batch, runs, manifest_path)

    def _prepare_batch_dir(self) -> Path:
        if self.config.batch_dir:
            path = Path(self.config.batch_dir)
            if path.exists() and not self.config.resume:
                raise FileExistsError(
                    f"batch directory exists; pass resume=true or a new path: {path}"
                )
            path.mkdir(parents=True, exist_ok=True)
            return path
        root = Path(self.config.output_dir)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        path = root / f"batch_{stamp}_{time.time_ns() % 10000:04d}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _finalize(
        self,
        batch_dir: Path,
        batch: BenchmarkBatch,
        runs: list[BenchmarkRun],
        manifest_path: Path,
    ) -> BatchOutcome:
        write_jsonl(batch_dir / "runs.jsonl", runs)
        (batch_dir / "runs.json").write_text(
            json.dumps(
                [run.model_dump(mode="json") for run in runs],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        write_csv(batch_dir / "runs.csv", runs)
        aggregate = aggregate_runs(runs)
        failures = [
            run.model_dump(mode="json")
            for run in runs
            if run.failure is not None
        ]
        failures_path = batch_dir / "failures.json"
        failures_path.write_text(
            json.dumps(failures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_path = batch_dir / "report.md"
        report_path.write_text(
            render_benchmark_report(
                batch,
                runs,
                aggregate=aggregate,
                extra_notes=[
                    "Fixture-provider runs validate the harness only; they are "
                    "not evidence of real-LLM task performance.",
                    "Human responses in benchmark tasks are scripted simulations.",
                ],
            ),
            encoding="utf-8",
        )
        write_batch_manifest(manifest_path, batch)
        return BatchOutcome(
            batch=batch,
            runs=runs,
            aggregate=aggregate,
            batch_dir=batch_dir,
            report_path=report_path,
            jsonl_path=batch_dir / "runs.jsonl",
            json_path=batch_dir / "runs.json",
            csv_path=batch_dir / "runs.csv",
            failures_path=failures_path,
        )

    async def run_task(
        self,
        task: BenchmarkTask,
        *,
        batch_id: str,
        repeat_index: int,
        batch_dir: Path,
    ) -> BenchmarkRun:
        run_started = utc_now()
        started_monotonic = time.monotonic()
        run_id = f"r_{uuid4().hex[:16]}"
        data_dir = batch_dir / "runs" / run_id
        data_dir.mkdir(parents=True, exist_ok=True)
        handle: BenchmarkRuntimeHandle | None = None
        workflow: Workflow | None = None
        human_interactions: list[HumanInteractionRecord] = []
        notes: list[str] = []
        active_seconds = 0.0
        human_wait_seconds = 0.0
        timed_out = False
        run_error: str | None = None
        try:
            handle = await create_benchmark_runtime(
                task,
                self.config,
                data_dir,
                provider_override=self.provider_override,
            )
            active_seconds, human_wait_seconds, timed_out, workflow = (
                await self._drive_workflow(task, handle, human_interactions)
            )
            if timed_out:
                notes.append("task time budget exceeded; workflow cancellation attempted")
        except Exception as exc:
            run_error = f"{type(exc).__name__}: {exc}"
            notes.append(run_error)
            if handle is not None:
                workflows = handle.runtime.list_workflows(limit=1)
                if workflows and workflows[0].status not in {
                    WorkflowStatus.COMPLETED,
                    WorkflowStatus.FAILED,
                    WorkflowStatus.CANCELLED,
                }:
                    try:
                        await asyncio.wait_for(
                            handle.runtime.cancel_workflow(
                                workflows[0].workflow_id, reason="benchmark_error"
                            ),
                            timeout=15,
                        )
                    except Exception:
                        logger.exception("benchmark cleanup failed")
                    workflow = handle.runtime.get_workflow(workflows[0].workflow_id)
        if (
            self.config.memory_feedback
            and handle is not None
            and workflow is not None
            and workflow.status == WorkflowStatus.COMPLETED
        ):
            try:
                handle.runtime.memory_engine.process_workflow(
                    workflow.workflow_id, learn=False
                )
            except Exception as exc:
                notes.append(
                    f"memory feedback failed: {type(exc).__name__}: {exc}"
                )
        end_to_end = time.monotonic() - started_monotonic
        verification: VerificationResult | None = None
        if handle is not None and workflow is not None and not timed_out:
            try:
                verification = await BenchmarkVerifier(handle, task).verify(workflow)
            except Exception as exc:
                verification = VerificationResult(
                    status=VerificationStatus.SKIPPED,
                    failure_reason=f"verifier error: {type(exc).__name__}: {exc}",
                )
                notes.append(verification.failure_reason or "verifier error")
        if timed_out:
            verification = VerificationResult(
                status=VerificationStatus.SKIPPED,
                failure_reason="task exceeded its time budget",
            )
        if verification is None:
            verification = VerificationResult(
                status=VerificationStatus.SKIPPED,
                failure_reason=run_error or "no workflow was produced",
            )
        run_status = self._run_status(verification, timed_out=timed_out, error=run_error)
        metrics = RunMetrics()
        if handle is not None and workflow is not None:
            try:
                metrics = collect_run_metrics(
                    handle,
                    task,
                    workflow,
                    verification,
                    end_to_end_seconds=end_to_end,
                    active_seconds=active_seconds,
                    human_wait_seconds=human_wait_seconds,
                    run_status=run_status,
                )
            except Exception as exc:
                notes.append(f"metric collection error: {type(exc).__name__}: {exc}")
            if metrics.tool_budget_exceeded:
                run_status = RunStatus.FAILED
                metrics.goal_success = False
                notes.append(
                    f"tool budget exceeded: {metrics.tool_calls} > {task.tool_budget}"
                )
            if metrics.time_budget_exceeded:
                run_status = RunStatus.FAILED
                metrics.goal_success = False
                notes.append("task exceeded its time budget")
            if metrics.token_budget_exceeded:
                run_status = RunStatus.FAILED
                metrics.goal_success = False
                notes.append("task exceeded its token budget")
        workflow_status = workflow.status if workflow is not None else None
        workflow_error = workflow.error if workflow is not None else run_error
        final_preview = (workflow.final_result or "")[:1000] if workflow else ""
        run = BenchmarkRun(
            run_id=run_id,
            batch_id=batch_id,
            task_id=task.task_id,
            title=task.title,
            category=task.category,
            difficulty=task.difficulty,
            repeat_index=repeat_index,
            mode=self.config.mode,
            provider_kind=self.config.provider_kind,
            provider_name=(
                handle.provider.name if handle else self.config.provider_name
            ),
            model=handle.provider.model if handle else (self.config.model or "unknown"),
            git_commit=self.git_commit,
            started_at=run_started,
            finished_at=utc_now(),
            run_status=run_status,
            verification=verification,
            workflow_id=workflow.workflow_id if workflow else None,
            workflow_status=workflow_status,
            workflow_error=workflow_error,
            final_result_preview=final_preview,
            run_data_dir=str(data_dir),
            metrics=metrics,
            human_interactions=human_interactions,
            config_snapshot={
                "mode": self.config.mode.value,
                "provider_kind": self.config.provider_kind.value,
                "memory_evolution": self.config.memory_evolution,
                "memory_recall": self.config.memory_recall,
                "memory_feedback": self.config.memory_feedback,
                "time_budget_seconds": task.time_budget_seconds,
                "token_budget": task.token_budget,
                "tool_budget": task.tool_budget,
                "max_delegation_depth": task.max_delegation_depth,
                "allowed_tools": task.allowed_tools,
            },
            artifacts=list(task.expected_artifacts),
            notes=notes,
        )
        if handle is not None and workflow is not None:
            try:
                attach_failure(
                    run,
                    task,
                    handle.runtime.list_tool_calls(workflow.workflow_id),
                    handle.runtime.list_tasks(workflow.workflow_id),
                )
            except Exception:
                logger.exception("failure analysis failed")
        elif run.failure is None:
            attach_failure(run, task, [], [])
        if handle is not None:
            try:
                await handle.close()
            except Exception:
                logger.exception("failed to close benchmark runtime")
        return run

    async def _drive_workflow(
        self,
        task: BenchmarkTask,
        handle: BenchmarkRuntimeHandle,
        records: list[HumanInteractionRecord],
    ) -> tuple[float, float, bool, Workflow | None]:
        runtime = handle.runtime
        active_seconds = 0.0
        human_wait = 0.0
        timed_out = False
        workflow: Workflow | None = None

        async def active_call(awaitable):
            nonlocal active_seconds, timed_out
            remaining = max(
                0.1, task.time_budget_seconds - active_seconds
            )
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(awaitable, timeout=remaining)
                return result
            except asyncio.TimeoutError:
                timed_out = True
                return None
            finally:
                active_seconds += time.monotonic() - started

        workflow = await active_call(runtime.submit_goal(task.goal))
        if workflow is None:
            workflows = runtime.list_workflows(limit=1)
            workflow = workflows[0] if workflows else None
            if workflow is not None:
                try:
                    await asyncio.wait_for(
                        runtime.cancel_workflow(
                            workflow.workflow_id, reason="time_budget_exceeded"
                        ),
                        timeout=15,
                    )
                except Exception:
                    logger.exception("failed to cancel timed-out benchmark task")
                workflow = runtime.get_workflow(workflow.workflow_id)
            return active_seconds, human_wait, True, workflow

        # A human pause has no approval/clarification row. Resume it once as the
        # simulated operator; approval/clarification pauses are handled below.
        if workflow.status == WorkflowStatus.PAUSED:
            pending_approvals = [
                item
                for item in runtime.list_approvals(workflow.workflow_id)
                if item.status == ApprovalStatus.PENDING
            ]
            open_clarifications = [
                item
                for item in runtime.list_clarifications(workflow.workflow_id)
                if item.status.value == "open"
            ]
            if not pending_approvals and not open_clarifications:
                records.append(
                    HumanInteractionRecord(
                        kind="resume",
                        target_id=workflow.workflow_id,
                        decision="resume",
                        simulated=True,
                    )
                )
                workflow = await active_call(
                    runtime.resume_workflow(workflow.workflow_id)
                )
                if workflow is None:
                    workflows = runtime.list_workflows(limit=1)
                    workflow = workflows[0] if workflows else None
                    return active_seconds, human_wait, True, workflow

        script = task.human_script
        for _ in range(script.max_interactions if script else 0):
            if workflow.status != WorkflowStatus.PAUSED:
                break
            approvals = [
                item
                for item in runtime.list_approvals(workflow.workflow_id)
                if item.status == ApprovalStatus.PENDING
            ]
            clarifications = [
                item
                for item in runtime.list_clarifications(workflow.workflow_id)
                if item.status.value == "open"
            ]
            if not approvals and not clarifications:
                break
            if approvals:
                decision = script.approval_decision if script else "leave_pending"
                if decision == "leave_pending":
                    records.append(
                        HumanInteractionRecord(
                            kind="approval",
                            target_id=approvals[0].approval_id,
                            decision="leave_pending",
                            wait_seconds=0.0,
                            simulated=True,
                        )
                    )
                    break
                timer = time.monotonic()
                if decision == "wait_expire":
                    wait_seconds = 0.0
                    if approvals[0].expires_at is not None:
                        wait_seconds = max(
                            0.0,
                            (
                                approvals[0].expires_at - utc_now()
                            ).total_seconds()
                            + 0.2,
                        )
                    await asyncio.sleep(wait_seconds)
                    human_wait += time.monotonic() - timer
                    records.append(
                        HumanInteractionRecord(
                            kind="approval",
                            target_id=approvals[0].approval_id,
                            decision="wait_expire",
                            wait_seconds=wait_seconds,
                            simulated=True,
                        )
                    )
                else:
                    for approval in approvals:
                        if decision == "approve":
                            runtime.approve_tool(
                                approval.approval_id,
                                comment=script.approval_comment if script else "",
                            )
                        else:
                            runtime.reject_tool(
                                approval.approval_id,
                                comment=script.approval_comment if script else "",
                            )
                        records.append(
                            HumanInteractionRecord(
                                kind="approval",
                                target_id=approval.approval_id,
                                decision=decision,
                                wait_seconds=0.0,
                                simulated=True,
                            )
                        )
                    human_wait += time.monotonic() - timer
            if clarifications:
                answer = script.clarification_answer if script else None
                if not answer:
                    for clarification in clarifications:
                        records.append(
                            HumanInteractionRecord(
                                kind="clarification",
                                target_id=clarification.clarification_id,
                                decision="leave_open",
                                simulated=True,
                            )
                        )
                    break
                timer = time.monotonic()
                for clarification in clarifications:
                    runtime.answer_clarification(
                        clarification.clarification_id, answer
                    )
                    records.append(
                        HumanInteractionRecord(
                            kind="clarification",
                            target_id=clarification.clarification_id,
                            decision=answer,
                            simulated=True,
                        )
                    )
                human_wait += time.monotonic() - timer
            workflow = await active_call(runtime.resume_workflow(workflow.workflow_id))
            if workflow is None:
                workflows = runtime.list_workflows(limit=1)
                workflow = workflows[0] if workflows else None
                return active_seconds, human_wait, True, workflow
        if workflow is None:
            return active_seconds, human_wait, timed_out, workflow
        return active_seconds, human_wait, timed_out, workflow

    @staticmethod
    def _run_status(
        verification: VerificationResult,
        *,
        timed_out: bool,
        error: str | None,
    ) -> RunStatus:
        if timed_out:
            return RunStatus.TIMEOUT
        if error:
            return RunStatus.ERROR
        return {
            VerificationStatus.PASSED: RunStatus.PASSED,
            VerificationStatus.FAILED: RunStatus.FAILED,
            VerificationStatus.NEEDS_REVIEW: RunStatus.NEEDS_REVIEW,
            VerificationStatus.SKIPPED: RunStatus.SKIPPED,
        }[verification.status]
