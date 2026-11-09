"""Live-provider acceptance checks with explicit no-key NOT_VERIFIED behavior."""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentforge.benchmark.models import (
    BenchmarkConfig,
    BenchmarkRun,
    LiveAcceptanceReport,
    LiveCheck,
    ProviderKind,
    RunStatus,
)
from agentforge.benchmark.runner import BenchmarkRunner
from agentforge.config.settings import Settings
from agentforge.models.common import utc_now


LIVE_TASK_IDS = [
    "file_exact_create",
    "research_basic_facts",
    "plan_research_to_coding",
    "codegen_add_function",
    "execute_compute_sum",
    "execute_failing_test",
    "delegation_single_level",
    "clarification_retention",
    "approval_delete_rejected",
]


def _key_configured(settings: Settings) -> bool:
    if settings.llm_provider == "deepseek":
        key = settings.deepseek_api_key
    elif settings.llm_provider == "hkgai":
        key = settings.hkgai_api_key
    else:
        return False
    return key is not None and bool(key.get_secret_value().strip())


class LiveAcceptanceRunner:
    """Runs real-provider acceptance only with explicit permission.

    Never invokes a paid API unless both an API key and ``allow_live_api`` are
    present. Without them, the harness is validated with the fixture provider and
    every live check remains NOT_VERIFIED.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        allow_live_api: bool = False,
        dataset_path: str = "benchmarks/live/tasks.jsonl",
        output_dir: str = "benchmarks/runs",
        task_ids: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.allow_live_api = allow_live_api
        self.dataset_path = dataset_path
        self.output_dir = Path(output_dir)
        self.task_ids = task_ids or list(LIVE_TASK_IDS)

    async def run(self) -> LiveAcceptanceReport:
        report = LiveAcceptanceReport(
            provider=self.settings.llm_provider,
            model=(
                self.settings.deepseek_model
                if self.settings.llm_provider == "deepseek"
                else self.settings.hkgai_model or "unknown"
            ),
            api_key_configured=_key_configured(self.settings),
            live_api_allowed=self.allow_live_api,
            checked_at=utc_now(),
        )
        report.checks = self._base_checks()
        if not report.api_key_configured or not self.allow_live_api:
            reasons = []
            if not report.api_key_configured:
                reasons.append("no API key configured")
            if not self.allow_live_api:
                reasons.append("live API calls were not explicitly authorized")
            report.notes.append(
                "Live checks remain NOT_VERIFIED: " + "; ".join(reasons) + "."
            )
            report.harness_validation = await self._validate_harness()
            report.status = "NOT_VERIFIED"
            self._write(report)
            return report

        config = BenchmarkConfig(
            dataset_path=self.dataset_path,
            output_dir=str(self.output_dir / "live"),
            provider_kind=ProviderKind.LIVE,
            provider_name=self.settings.llm_provider,
            task_ids=self.task_ids,
            repeats=1,
            live_api_allowed=True,
        )
        runner = BenchmarkRunner(config)
        outcome = await runner.run_batch()
        by_id = {run.task_id: run for run in outcome.runs}
        for check in report.checks:
            check.status, check.detail, check.metrics = self._evaluate_check(
                check.check_id, outcome.runs, by_id
            )
        passed = sum(1 for check in report.checks if check.status == "PASSED")
        failed = sum(1 for check in report.checks if check.status == "FAILED")
        if failed:
            report.status = "FAILED"
        elif passed == len(report.checks):
            report.status = "PASSED"
        else:
            report.status = "PARTIAL"
        report.notes.append(f"Live benchmark batch: {outcome.batch_dir}")
        report.notes.append(
            "Human responses in live HITL tasks are scripted simulations, not "
            "real users."
        )
        self._write(report)
        return report

    @staticmethod
    def _base_checks() -> list[LiveCheck]:
        descriptions = {
            "complex_goal_decomposition": "Complex goal produces a valid plan DAG.",
            "structured_json_action": "Agent actions are valid structured JSON.",
            "tool_calling": "Tools are called with schema-valid arguments.",
            "python_code_generation": "Generated Python passes real execution tests.",
            "tool_result_interpretation": "Agent correctly uses real tool stdout/errors.",
            "agent_to_agent_delegation": "Delegated child work completes and returns.",
            "reviewer_evaluation": "Governance review task completes.",
            "clarification_request": "Workflow pauses and resumes from a human answer.",
            "approval_request": "Destructive action waits for and respects approval.",
            "memory_retrieval_and_reuse": "Memory is retrieved and changes a later run.",
        }
        return [
            LiveCheck(check_id=check_id, description=description)
            for check_id, description in descriptions.items()
        ]

    async def _validate_harness(self) -> dict:
        config = BenchmarkConfig(
            dataset_path="benchmarks/dev/tasks.jsonl",
            output_dir=str(self.output_dir / "live_harness"),
            provider_kind=ProviderKind.FIXTURE,
            provider_name="mock",
            task_ids=[
                "file_exact_create",
                "approval_delete_rejected",
                "clarification_retention",
            ],
            repeats=1,
        )
        try:
            outcome = await BenchmarkRunner(config).run_batch()
        except Exception as exc:
            return {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }
        aggregate = outcome.aggregate
        return {
            "status": "PASSED",
            "batch_dir": str(outcome.batch_dir),
            "runs": aggregate.total_runs,
            "passed": aggregate.passed,
            "failed": aggregate.failed,
            "needs_review": aggregate.needs_review,
            "skipped": aggregate.skipped,
            "note": (
                "Fixture decisions validated the harness; this is not an LLM "
                "capability result."
            ),
        }

    @staticmethod
    def _evaluate_check(
        check_id: str,
        runs: list[BenchmarkRun],
        by_id: dict[str, BenchmarkRun],
    ) -> tuple[str, str, dict]:
        def run_passed(task_id: str) -> bool:
            run = by_id.get(task_id)
            return run is not None and run.run_status == RunStatus.PASSED

        if check_id == "complex_goal_decomposition":
            run = by_id.get("plan_research_to_coding")
            if run is None:
                return "SKIPPED", "task not run", {}
            passed = run.run_status == RunStatus.PASSED
            return (
                "PASSED" if passed else "FAILED",
                f"planning task status={run.run_status.value}",
                {"plan_validation_failure_rate": run.metrics.plan_validation_failure_rate},
            )
        if check_id == "structured_json_action":
            rate = _mean([run.metrics.invalid_json_rate for run in runs])
            return (
                "PASSED" if rate <= 0.2 else "FAILED",
                f"invalid JSON rate={rate:.3f}",
                {"invalid_json_rate": rate},
            )
        if check_id == "tool_calling":
            total = sum(run.metrics.tool_calls for run in runs)
            rate = _mean(
                [
                    run.metrics.tool_calling_success_rate
                    for run in runs
                    if run.metrics.tool_calls
                ]
            )
            passed = total > 0 and rate >= 0.8
            return (
                "PASSED" if passed else "FAILED",
                f"tool calls={total}, success rate={rate:.3f}",
                {"tool_calls": total, "tool_calling_success_rate": rate},
            )
        if check_id == "python_code_generation":
            return _simple_run_check(run_passed("codegen_add_function"))
        if check_id == "tool_result_interpretation":
            return _simple_run_check(run_passed("execute_compute_sum"))
        if check_id == "agent_to_agent_delegation":
            run = by_id.get("delegation_single_level")
            if run is None:
                return "SKIPPED", "task not run", {}
            passed = run.run_status == RunStatus.PASSED and run.metrics.delegation_count > 0
            return (
                "PASSED" if passed else "FAILED",
                f"delegations={run.metrics.delegation_count}",
                {"delegation_count": run.metrics.delegation_count},
            )
        if check_id == "reviewer_evaluation":
            run = by_id.get("plan_research_to_coding")
            if run is None:
                return "SKIPPED", "task not run", {}
            passed = run.metrics.agent_completion_rate >= 1.0
            return (
                "PASSED" if passed else "FAILED",
                f"agent completion rate={run.metrics.agent_completion_rate:.3f}",
                {"agent_completion_rate": run.metrics.agent_completion_rate},
            )
        if check_id == "clarification_request":
            return _simple_run_check(run_passed("clarification_retention"))
        if check_id == "approval_request":
            return _simple_run_check(run_passed("approval_delete_rejected"))
        if check_id == "memory_retrieval_and_reuse":
            retrievals = sum(run.metrics.memory_retrieval_count for run in runs)
            used = sum(run.metrics.memory_usage_count for run in runs)
            return (
                "NOT_VERIFIED",
                "requires a separate live learning/evaluation pair to prove reuse",
                {"retrievals_observed": retrievals, "used_observed": used},
            )
        return "SKIPPED", "unknown check", {}

    def _write(self, report: LiveAcceptanceReport) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = self.output_dir / f"live_{stamp}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "live_report.json").write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        lines = [
            "# Live-LLM Acceptance Report",
            "",
            f"- Provider: `{report.provider}`",
            f"- Model: `{report.model}`",
            f"- API key configured: {report.api_key_configured}",
            f"- Live API explicitly allowed: {report.live_api_allowed}",
            f"- Status: **{report.status}**",
            "",
            "| Check | Status | Detail |",
            "|---|---|---|",
        ]
        for check in report.checks:
            lines.append(
                f"| {check.check_id} | {check.status} | {check.detail} |"
            )
        if report.harness_validation:
            lines.append("")
            lines.append("## Fixture harness validation")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(report.harness_validation, indent=2))
            lines.append("```")
        if report.notes:
            lines.append("")
            lines.append("## Notes")
            lines.append("")
            for note in report.notes:
                lines.append(f"- {note}")
        (root / "LIVE_ACCEPTANCE_REPORT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _simple_run_check(passed: bool) -> tuple[str, str, dict]:
    if passed:
        return "PASSED", "verified task passed", {}
    return "FAILED", "verified task did not pass", {}
