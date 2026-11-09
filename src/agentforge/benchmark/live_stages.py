"""Staged real-LLM acceptance with explicit budget gates.

Stages:
1. connectivity
2. structured output
3. tool execution
4. multi-agent collaboration
5. HITL + live memory reuse
6. full live benchmark

No paid API is called unless an API key is configured AND the caller explicitly
sets allow_live_api. Each stage must pass before the next is attempted.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentforge.benchmark.live_memory import (
    LiveMemoryExperimentRunner,
    LiveMemoryResult,
)
from agentforge.benchmark.metrics import aggregate_runs
from agentforge.benchmark.models import (
    BenchmarkConfig,
    ProviderKind,
    RunStatus,
)
from agentforge.benchmark.provider import LiveBudgetProvider
from agentforge.benchmark.runner import BenchmarkRunner
from agentforge.config.settings import Settings
from agentforge.llm.base import BaseLLMProvider
from agentforge.llm.factory import build_llm_provider
from agentforge.models.agent import AgentRole
from agentforge.models.common import utc_now
from agentforge.models.task import AgentActionType, Task
from agentforge.runtime import AgentForgeRuntime


STAGES = [
    (1, "connectivity", "Connectivity smoke test"),
    (2, "structured_output", "Structured plan and action"),
    (3, "tool_execution", "Real file and Docker tool execution"),
    (4, "multi_agent", "Planning, delegation and independent review"),
    (5, "hitl_memory", "Clarification, approval and memory reuse"),
    (6, "live_benchmark", "Full live benchmark"),
]


class LiveStageResult(BaseModel):
    stage: int
    stage_id: str
    name: str
    status: Literal["PASSED", "FAILED", "NOT_VERIFIED", "SKIPPED"] = "NOT_VERIFIED"
    detail: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    output_dir: str | None = None


class LiveStageReport(BaseModel):
    provider: str
    model: str
    api_key_configured: bool = False
    live_api_allowed: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    overall_status: Literal[
        "PASSED", "FAILED", "NOT_VERIFIED", "PARTIAL"
    ] = "NOT_VERIFIED"
    stages: list[LiveStageResult] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    memory_experiment: LiveMemoryResult | None = None
    notes: list[str] = Field(default_factory=list)


def _key_configured(settings: Settings) -> bool:
    if settings.llm_provider == "deepseek":
        key = settings.deepseek_api_key
    elif settings.llm_provider == "hkgai":
        key = settings.hkgai_api_key
    else:
        return False
    return key is not None and bool(key.get_secret_value().strip())


class LiveStageRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        allow_live_api: bool = False,
        output_dir: str | Path = "benchmarks/runs/live_stages",
        max_requests: int = 120,
        max_tokens: int = 250_000,
        start_stage: int = 1,
        end_stage: int = 6,
        run_memory_experiment: bool = True,
    ) -> None:
        self.settings = settings
        self.allow_live_api = allow_live_api
        self.output_dir = Path(output_dir)
        self.max_requests = max_requests
        self.max_tokens = max_tokens
        self.start_stage = start_stage
        self.end_stage = end_stage
        self.run_memory_experiment = run_memory_experiment

    async def run(self) -> LiveStageReport:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = self.output_dir / f"live_stages_{stamp}"
        root.mkdir(parents=True, exist_ok=True)
        report = LiveStageReport(
            provider=self.settings.llm_provider,
            model=(
                self.settings.deepseek_model
                if self.settings.llm_provider == "deepseek"
                else self.settings.hkgai_model or "unknown"
            ),
            api_key_configured=_key_configured(self.settings),
            live_api_allowed=self.allow_live_api,
            notes=[],
        )
        selected = [
            item for item in STAGES if self.start_stage <= item[0] <= self.end_stage
        ]
        if not report.api_key_configured or not self.allow_live_api:
            reason = (
                "no API key configured"
                if not report.api_key_configured
                else "live API calls were not explicitly authorized"
            )
            report.notes.append(
                "All stages NOT_VERIFIED: " + reason + ". No paid API call was made."
            )
            report.notes.append(
                "Required configuration: AGENTFORGE_LLM_PROVIDER=deepseek|hkgai, "
                "the matching API key, and for HKGAI also BASE_URL and MODEL. "
                "Do not paste keys into chat or logs."
            )
            report.stages = [
                LiveStageResult(
                    stage=stage,
                    stage_id=stage_id,
                    name=name,
                    status="NOT_VERIFIED",
                    detail=reason,
                )
                for stage, stage_id, name in selected
            ]
            report.overall_status = "NOT_VERIFIED"
            self._write(root, report)
            return report

        base_provider: BaseLLMProvider
        try:
            base_provider = build_llm_provider(self.settings)
        except Exception as exc:
            report.notes.append(
                f"Provider configuration failed: {type(exc).__name__}: {exc}"
            )
            report.stages = [
                LiveStageResult(
                    stage=stage,
                    stage_id=stage_id,
                    name=name,
                    status="NOT_VERIFIED",
                    detail=report.notes[-1],
                )
                for stage, stage_id, name in selected
            ]
            report.overall_status = "NOT_VERIFIED"
            self._write(root, report)
            return report

        budget = LiveBudgetProvider(
            base_provider,
            max_requests=self.max_requests,
            max_tokens=self.max_tokens,
        )
        stopped = False
        try:
            for stage, stage_id, name in selected:
                result = LiveStageResult(
                    stage=stage, stage_id=stage_id, name=name
                )
                if stopped:
                    result.status = "SKIPPED"
                    result.detail = "previous stage failed or budget was exhausted"
                    result.finished_at = utc_now()
                    report.stages.append(result)
                    continue
                stage_dir = root / f"stage_{stage}_{stage_id}"
                stage_dir.mkdir(parents=True, exist_ok=True)
                result.output_dir = str(stage_dir)
                try:
                    result = await self._run_stage(
                        stage, stage_id, name, budget, stage_dir, result, report
                    )
                except Exception as exc:
                    result.status = "FAILED"
                    result.detail = f"{type(exc).__name__}: {exc}"
                    result.finished_at = utc_now()
                result.metrics["budget_after_stage"] = budget.snapshot()
                report.stages.append(result)
                if result.status != "PASSED":
                    stopped = True
                if budget.request_budget_exceeded or budget.token_budget_exceeded:
                    report.notes.append(
                        "Live budget exceeded; remaining stages were stopped."
                    )
                    stopped = True
        finally:
            await budget.close_underlying()
        report.budget = budget.snapshot()
        if any(stage.status == "FAILED" for stage in report.stages):
            report.overall_status = "FAILED"
        elif all(stage.status == "PASSED" for stage in report.stages):
            report.overall_status = "PASSED"
        elif any(stage.status == "PASSED" for stage in report.stages):
            report.overall_status = "PARTIAL"
        else:
            report.overall_status = "NOT_VERIFIED"
        self._write(root, report)
        return report

    async def _run_stage(
        self,
        stage: int,
        stage_id: str,
        name: str,
        budget: LiveBudgetProvider,
        stage_dir: Path,
        result: LiveStageResult,
        report: LiveStageReport,
    ) -> LiveStageResult:
        if stage == 1:
            return await self._stage1(result, budget)
        if stage == 2:
            return await self._stage2(result, budget)
        if stage == 3:
            return await self._stage_tasks(
                result,
                budget,
                ["file_exact_create", "execute_compute_sum"],
                stage_dir,
            )
        if stage == 4:
            return await self._stage4(result, budget, stage_dir)
        if stage == 5:
            return await self._stage5(result, budget, stage_dir, report)
        if stage == 6:
            return await self._stage6(result, budget, stage_dir)
        result.status = "FAILED"
        result.detail = f"unknown stage {stage}"
        result.finished_at = utc_now()
        return result

    async def _stage1(
        self, result: LiveStageResult, budget: LiveBudgetProvider
    ) -> LiveStageResult:
        try:
            response = await budget.complete(
                [
                    _user("Reply with exactly OK."),
                ],
                purpose="live_stage1_connectivity",
                max_tokens=32,
            )
        except Exception as exc:
            result.status = "FAILED"
            result.detail = f"connectivity call failed: {type(exc).__name__}: {exc}"
            result.finished_at = utc_now()
            return result
        usage = response.usage
        result.metrics = {
            "content_chars": len(response.content),
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "provider": response.provider,
            "model": response.model,
        }
        passed = bool(response.content.strip()) and usage.total_tokens > 0
        result.status = "PASSED" if passed else "FAILED"
        result.detail = (
            f"non-empty={bool(response.content.strip())}, "
            f"usage_total={usage.total_tokens}"
        )
        result.finished_at = utc_now()
        return result

    async def _stage2(
        self, result: LiveStageResult, budget: LiveBudgetProvider
    ) -> LiveStageResult:
        try:
            async with AgentForgeRuntime(self.settings, provider=budget) as runtime:
                coordinator = runtime.agents.require_role(AgentRole.COORDINATOR)
                planning = await coordinator.plan_goal(
                    "Create a short Markdown note and have it reviewed.",
                    agent_cards=runtime.agents.cards(),
                    max_subtasks=3,
                )
                task = Task(
                    workflow_id="live_stage2",
                    description=(
                        "Return one complete JSON action whose content is exactly "
                        "the string structured-ok. Do not call tools."
                    ),
                    required_capabilities=[],
                    assigned_agent=coordinator.agent_id,
                    metadata={"kind": "structured"},
                )
                run = await coordinator.execute_task(
                    task,
                    goal="Return one valid complete action with content structured-ok.",
                    tool_context=None,
                )
        except Exception as exc:
            result.status = "FAILED"
            result.detail = f"structured output failed: {type(exc).__name__}: {exc}"
            result.finished_at = utc_now()
            return result
        valid_plan = bool(planning.plan.subtasks) and bool(planning.ordered_subtasks)
        valid_action = (
            run.action.action == AgentActionType.COMPLETE
            and "structured-ok" in run.action.content.lower()
        )
        result.metrics = {
            "plan_subtasks": len(planning.plan.subtasks),
            "plan_attempts": len(planning.attempts),
            "plan_errors": sum(
                1 for attempt in planning.attempts if attempt.error
            ),
            "action": run.action.action.value,
            "action_repairs": run.repair_count,
            "action_parse_warning": run.parse_warning,
        }
        result.status = "PASSED" if valid_plan and valid_action else "FAILED"
        result.detail = (
            f"valid_plan={valid_plan}, valid_action={valid_action}, "
            f"repairs={run.repair_count}"
        )
        result.finished_at = utc_now()
        return result

    async def _stage4(
        self,
        result: LiveStageResult,
        budget: LiveBudgetProvider,
        stage_dir: Path,
    ) -> LiveStageResult:
        outcome = await self._run_live_tasks(
            budget,
            ["plan_research_to_coding", "delegation_single_level"],
            stage_dir,
        )
        runs = outcome.runs
        passed = [
            run for run in runs if run.run_status == RunStatus.PASSED
        ]
        delegations = sum(run.metrics.delegation_count for run in runs)
        agent_completion = min(
            (run.metrics.agent_completion_rate for run in runs), default=0.0
        )
        result.metrics = {
            "runs": len(runs),
            "passed": len(passed),
            "delegations": delegations,
            "min_agent_completion_rate": agent_completion,
            "success_rate": len(passed) / len(runs) if runs else 0.0,
        }
        result.status = (
            "PASSED"
            if len(passed) == len(runs) and delegations > 0 and agent_completion >= 1.0
            else "FAILED"
        )
        result.detail = (
            f"passed={len(passed)}/{len(runs)}, delegations={delegations}, "
            f"agent_completion={agent_completion:.2f}"
        )
        result.finished_at = utc_now()
        return result

    async def _stage5(
        self,
        result: LiveStageResult,
        budget: LiveBudgetProvider,
        stage_dir: Path,
        report: LiveStageReport,
    ) -> LiveStageResult:
        outcome = await self._run_live_tasks(
            budget,
            ["clarification_retention", "approval_delete_rejected"],
            stage_dir / "hitl",
        )
        runs = outcome.runs
        hitl_passed = all(
            run.run_status == RunStatus.PASSED for run in runs
        ) and len(runs) == 2
        memory_result: LiveMemoryResult | None = None
        memory_ok = True
        if self.run_memory_experiment:
            memory_result = await LiveMemoryExperimentRunner(
                self.settings,
                budget,
                output_dir=stage_dir / "memory",
            ).run()
            report.memory_experiment = memory_result
            memory_ok = (
                memory_result.on_run is not None
                and memory_result.off_run is not None
                and (
                    memory_result.on_run.metrics.memory_retrieval_count > 0
                    or memory_result.off_run.metrics.memory_retrieval_count == 0
                )
            )
            if memory_result.on_run is None or memory_result.off_run is None:
                memory_ok = False
            if memory_result.on_run is not None and (
                memory_result.on_run.metrics.memory_retrieval_count == 0
            ):
                memory_ok = False
        result.metrics = {
            "hitl_runs": len(runs),
            "hitl_passed": sum(
                1 for run in runs if run.run_status == RunStatus.PASSED
            ),
            "memory_status": memory_result.status if memory_result else "NOT_RUN",
            "memory_retrieved": (
                len(memory_result.retrieved_memory_ids)
                if memory_result
                else 0
            ),
            "memory_applied": (
                len(memory_result.applied_memory_ids)
                if memory_result
                else 0
            ),
            "memory_action_changed": (
                memory_result.action_changed if memory_result else False
            ),
        }
        result.status = "PASSED" if hitl_passed and memory_ok else "FAILED"
        result.detail = (
            f"hitl_passed={hitl_passed}, memory_status="
            f"{memory_result.status if memory_result else 'NOT_RUN'}, "
            f"retrieved={result.metrics['memory_retrieved']}, "
            f"applied={result.metrics['memory_applied']}"
        )
        result.finished_at = utc_now()
        return result

    async def _stage6(
        self,
        result: LiveStageResult,
        budget: LiveBudgetProvider,
        stage_dir: Path,
    ) -> LiveStageResult:
        outcome = await self._run_live_tasks(
            budget, None, stage_dir / "full"
        )
        aggregate = aggregate_runs(outcome.runs)
        errors = sum(
            1 for run in outcome.runs if run.run_status == RunStatus.ERROR
        )
        skipped = sum(
            1 for run in outcome.runs if run.run_status == RunStatus.SKIPPED
        )
        result.metrics = {
            "runs": aggregate.total_runs,
            "passed": aggregate.passed,
            "failed": aggregate.failed,
            "needs_review": aggregate.needs_review,
            "skipped": aggregate.skipped,
            "completion_rate": aggregate.goal_completion_rate,
            "average_e2e_seconds": aggregate.average_end_to_end_seconds,
            "total_tokens": aggregate.total_tokens,
            "tool_failure_rate": aggregate.tool_failure_rate,
        }
        passed = (
            aggregate.total_runs > 0
            and errors == 0
            and skipped == 0
            and aggregate.passed > 0
        )
        result.status = "PASSED" if passed else "FAILED"
        result.detail = (
            f"verified={aggregate.passed}/{aggregate.total_runs} "
            f"({aggregate.goal_completion_rate:.1%}), errors={errors}, "
            f"skipped={skipped}"
        )
        result.finished_at = utc_now()
        return result

    async def _stage_tasks(
        self,
        result: LiveStageResult,
        budget: LiveBudgetProvider,
        task_ids: list[str],
        stage_dir: Path,
    ) -> LiveStageResult:
        outcome = await self._run_live_tasks(budget, task_ids, stage_dir)
        runs = outcome.runs
        passed = [run for run in runs if run.run_status == RunStatus.PASSED]
        tool_calls = sum(run.metrics.tool_calls for run in runs)
        tool_success = any(
            run.metrics.tool_calling_success_rate > 0 for run in runs
        )
        result.metrics = {
            "runs": len(runs),
            "passed": len(passed),
            "tool_calls": tool_calls,
            "tool_success_observed": tool_success,
        }
        result.status = (
            "PASSED"
            if len(passed) == len(runs) and tool_calls > 0 and tool_success
            else "FAILED"
        )
        result.detail = (
            f"passed={len(passed)}/{len(runs)}, tool_calls={tool_calls}"
        )
        result.finished_at = utc_now()
        return result

    async def _run_live_tasks(
        self,
        budget: LiveBudgetProvider,
        task_ids: list[str] | None,
        output_dir: Path,
    ):
        config = BenchmarkConfig(
            dataset_path="benchmarks/live/tasks.jsonl",
            output_dir=str(output_dir),
            provider_kind=ProviderKind.LIVE,
            provider_name=self.settings.llm_provider,
            task_ids=task_ids or [],
            repeats=1,
            live_api_allowed=True,
        )
        return await BenchmarkRunner(
            config, provider_override=budget
        ).run_batch()

    def _write(self, root: Path, report: LiveStageReport) -> None:
        (root / "live_stages.json").write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        lines = [
            "# Staged Live-LLM Acceptance",
            "",
            f"- Provider: `{report.provider}`",
            f"- Model: `{report.model}`",
            f"- API key configured: {report.api_key_configured}",
            f"- Live API authorized: {report.live_api_allowed}",
            f"- Overall: **{report.overall_status}**",
            "",
            "| Stage | Name | Status | Detail |",
            "|---:|---|---|---|",
        ]
        for stage in report.stages:
            lines.append(
                f"| {stage.stage} | {stage.name} | {stage.status} | "
                f"{stage.detail} |"
            )
        if report.memory_experiment is not None:
            memory = report.memory_experiment
            lines.append("")
            lines.append("## Memory experiment")
            lines.append("")
            lines.append(f"- Status: `{memory.status}`")
            lines.append(f"- Initial memory entries: {memory.initial_memory_entries}")
            lines.append(f"- Retrieved IDs: {memory.retrieved_memory_ids}")
            lines.append(f"- Applied IDs: {memory.applied_memory_ids}")
            lines.append(f"- Action changed: {memory.action_changed}")
            lines.append(f"- OFF success: {memory.off_goal_success}")
            lines.append(f"- ON success: {memory.on_goal_success}")
        if report.notes:
            lines.append("")
            lines.append("## Notes")
            lines.append("")
            for note in report.notes:
                lines.append(f"- {note}")
        (root / "LIVE_STAGES_REPORT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def _user(content: str):
    from agentforge.llm.base import ChatMessage

    return ChatMessage.user(content)
