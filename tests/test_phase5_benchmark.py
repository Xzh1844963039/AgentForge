"""Phase 5 benchmark harness, ablation, CLI and live-report tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agentforge.benchmark.ablation import AblationRunner
from agentforge.benchmark.dataset import load_dataset
from agentforge.benchmark.live import LiveAcceptanceRunner
from agentforge.benchmark.models import (
    BenchmarkConfig,
    BenchmarkMode,
    RunStatus,
    TaskCategory,
)
from agentforge.benchmark.runner import BenchmarkRunner
from agentforge.config.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dev_dataset_has_required_coverage_and_verifiers():
    dataset = load_dataset("benchmarks/dev/tasks.jsonl")
    assert len(dataset.tasks) >= 30
    categories = {task.category for task in dataset.tasks}
    assert categories == set(TaskCategory)
    assert all(task.verification.has_objective_checks() for task in dataset.tasks)
    assert any(task.verification.manual_review_reason for task in dataset.tasks)
    assert all(task.mock_actions for task in dataset.tasks)
    difficulties = {task.difficulty.value for task in dataset.tasks}
    assert difficulties == {"basic", "hard"}
    # Every category needs at least one basic and one hard task.
    for category in TaskCategory:
        per_category = {
            task.difficulty.value
            for task in dataset.tasks
            if task.category == category
        }
        assert per_category == {"basic", "hard"}


async def test_fixture_batch_runs_verifies_and_exports(tmp_path):
    tasks = [
        "file_exact_create",
        "approval_delete_rejected",
        "clarification_unanswered",
        "recovery_pause_resume",
        "recovery_cancel",
    ]
    config = BenchmarkConfig(
        dataset_path="benchmarks/dev/tasks.jsonl",
        output_dir=str(tmp_path / "runs"),
        task_ids=tasks,
    )
    outcome = await BenchmarkRunner(config).run_batch()
    assert outcome.aggregate.total_runs == len(tasks)
    assert outcome.aggregate.failed == 0
    assert all(run.run_status == RunStatus.PASSED for run in outcome.runs)
    assert outcome.report_path.exists()
    assert outcome.jsonl_path.exists()
    assert outcome.json_path.exists()
    assert outcome.csv_path.exists()
    assert outcome.failures_path.exists()
    and_report = outcome.report_path.read_text(encoding="utf-8")
    assert "MOCK FIXTURE RESULT" in and_report
    assert "Goal completion rate" in and_report

    cancelled = next(run for run in outcome.runs if run.task_id == "recovery_cancel")
    assert cancelled.metrics.goal_success is True
    assert cancelled.metrics.workflow_completed is False
    paused = next(
        run for run in outcome.runs if run.task_id == "clarification_unanswered"
    )
    assert paused.metrics.goal_success is True
    assert paused.metrics.workflow_completed is False
    for run in outcome.runs:
        assert run.metrics.end_to_end_seconds >= run.metrics.active_seconds
        assert run.metrics.end_to_end_seconds >= 0

    batch_dir = outcome.batch_dir
    resume_config = BenchmarkConfig(
        dataset_path="benchmarks/dev/tasks.jsonl",
        output_dir=str(tmp_path / "runs"),
        task_ids=tasks,
        batch_dir=str(batch_dir),
        resume=True,
    )
    resumed = await BenchmarkRunner(resume_config).run_batch()
    assert len(resumed.runs) == len(outcome.runs)
    assert resumed.batch.batch_id == outcome.batch.batch_id


async def test_single_agent_baseline_uses_real_tool_framework(tmp_path):
    config = BenchmarkConfig(
        dataset_path="benchmarks/dev/tasks.jsonl",
        output_dir=str(tmp_path / "single"),
        task_ids=["file_exact_create"],
        mode=BenchmarkMode.SINGLE_AGENT,
    )
    outcome = await BenchmarkRunner(config).run_batch()
    run = outcome.runs[0]
    assert run.run_status == RunStatus.PASSED
    assert run.mode == BenchmarkMode.SINGLE_AGENT
    assert run.metrics.tool_calls == 1


async def test_ablation_framework_runs_abc(tmp_path):
    runner = AblationRunner(
        dataset_path="benchmarks/dev/tasks.jsonl",
        output_dir=str(tmp_path / "ablation"),
        eval_task_ids=["file_exact_create", "research_basic_facts"],
        learning_task_ids=["file_multi_artifact"],
        repeats=1,
    )
    result = await runner.run()
    assert set(result.aggregates) == {
        "A_single_agent",
        "B_multi_agent",
        "C_multi_agent_memory",
    }
    for aggregate in result.aggregates.values():
        assert aggregate.total_runs == 2
        assert aggregate.failed == 0
    assert result.initial_memory_entries > 0
    assert (Path(tmp_path / "ablation") / result.ablation_id / "ablation.json").exists()
    assert (Path(tmp_path / "ablation") / result.ablation_id / "ablation.md").exists()


async def test_live_acceptance_without_key_is_not_verified_and_validates_harness(
    tmp_path,
):
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        data_dir=tmp_path / "data",
        log_level="WARNING",
    )
    report = await LiveAcceptanceRunner(
        settings,
        allow_live_api=False,
        output_dir=str(tmp_path / "live"),
    ).run()
    assert report.status == "NOT_VERIFIED"
    assert report.api_key_configured is False
    assert report.live_api_allowed is False
    assert all(check.status == "NOT_VERIFIED" for check in report.checks)
    assert report.harness_validation["status"] == "PASSED"
    assert report.harness_validation["failed"] == 0
    files = list((tmp_path / "live").rglob("LIVE_ACCEPTANCE_REPORT.md"))
    assert len(files) == 1


def test_cli_benchmark_list_and_run(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "200"
    env["AGENTFORGE_LLM_PROVIDER"] = "mock"
    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentforge",
            "benchmark",
            "list",
            "--dataset",
            "benchmarks/dev",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert listed.returncode == 0, listed.stderr
    assert "research_basic_facts" in listed.stdout
    assert "codegen_add_function" in listed.stdout

    output_dir = tmp_path / "cli-runs"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentforge",
            "benchmark",
            "run",
            "--dataset",
            "benchmarks/dev",
            "--task-id",
            "file_exact_create",
            "--output-dir",
            str(output_dir),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert run.returncode == 0, run.stderr
    assert "Fixture-provider run" in run.stdout
    assert "Goal completion" in run.stdout
    assert list(output_dir.glob("batch_*/report.md"))
