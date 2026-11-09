"""CLI commands for the Phase 5 benchmark harness."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agentforge.benchmark.ablation import AblationRunner
from agentforge.benchmark.dataset import load_dataset
from agentforge.benchmark.models import (
    BenchmarkConfig,
    BenchmarkMode,
    BenchmarkRun,
    ProviderKind,
    TaskCategory,
)
from agentforge.benchmark.report import (
    read_batch_manifest,
    read_jsonl,
    render_benchmark_report,
)
from agentforge.benchmark.runner import BenchmarkRunner


def latest_batch(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None
    candidates = [
        path for path in output_dir.iterdir() if path.is_dir() and (path / "batch.json").exists()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _print_outcome(console: Console, outcome) -> None:
    aggregate = outcome.aggregate
    table = Table(title="Benchmark summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Runs", str(aggregate.total_runs))
    table.add_row("Verified passed", str(aggregate.passed))
    table.add_row("Failed", str(aggregate.failed))
    table.add_row("Needs review", str(aggregate.needs_review))
    table.add_row("Skipped", str(aggregate.skipped))
    table.add_row("Goal completion", f"{aggregate.goal_completion_rate:.1%}")
    table.add_row("Average E2E", f"{aggregate.average_end_to_end_seconds:.3f}s")
    table.add_row("Median E2E", f"{aggregate.median_end_to_end_seconds:.3f}s")
    table.add_row("Average tokens", f"{aggregate.average_tokens:.1f}")
    table.add_row("Tool failure rate", f"{aggregate.tool_failure_rate:.1%}")
    console.print(table)
    if outcome.runs and outcome.runs[0].provider_kind == ProviderKind.FIXTURE:
        console.print(
            Text(
                "Fixture-provider run: harness validation only, NOT real-LLM performance.",
                style="bold yellow",
            )
        )
    console.print(Text(f"Batch dir: {outcome.batch_dir}"))
    console.print(Text(f"Report: {outcome.report_path}"))
    console.print(Text(f"JSONL: {outcome.jsonl_path}"))
    console.print(Text(f"JSON: {outcome.json_path}"))
    console.print(Text(f"CSV: {outcome.csv_path}"))


async def run_benchmark_command(
    args: argparse.Namespace,
    *,
    settings_factory,
    console: Console,
) -> int:
    command = args.benchmark_command
    if command == "list":
        return _list(args, console)
    if command == "run":
        return await _run(args, settings_factory, console)
    if command == "compare":
        return await _compare(args, console)
    if command == "report":
        return _report(args, console)
    if command == "failures":
        return _failures(args, console)
    if command == "live":
        return await _live(args, settings_factory, console)
    console.print(Text("usage: agentforge benchmark <list|run|compare|report|failures|live>"))
    return 2


def _list(args: argparse.Namespace, console: Console) -> int:
    dataset = load_dataset(args.dataset)
    table = Table(title=f"Dataset {dataset.dataset_id} ({len(dataset.tasks)} tasks)")
    table.add_column("Task")
    table.add_column("Category")
    table.add_column("Difficulty")
    table.add_column("Title")
    for task in dataset.tasks:
        if args.category and task.category.value != args.category:
            continue
        table.add_row(
            task.task_id,
            task.category.value,
            task.difficulty.value,
            task.title,
        )
    console.print(table)
    return 0


async def _run(args: argparse.Namespace, settings_factory, console: Console) -> int:
    provider_name = args.provider or "mock"
    live = provider_name in {"deepseek", "hkgai"}
    if live and not args.allow_live_api:
        console.print(
            Text(
                "Refusing paid live API calls: pass --allow-live-api explicitly.",
                style="bold red",
            )
        )
        return 2
    config = BenchmarkConfig(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        mode=BenchmarkMode(args.mode),
        provider_kind=ProviderKind.LIVE if live else ProviderKind.FIXTURE,
        provider_name=provider_name,
        task_ids=args.task_id or [],
        categories=[TaskCategory(item) for item in (args.category or [])],
        repeats=args.repeats,
        max_tasks=args.max_tasks,
        batch_dir=args.batch_dir,
        resume=args.resume,
        live_api_allowed=args.allow_live_api,
        memory_evolution=args.memory,
        memory_recall=args.memory,
    )
    outcome = await BenchmarkRunner(config).run_batch()
    _print_outcome(console, outcome)
    return 0 if outcome.aggregate.failed == 0 else 1


async def _compare(args: argparse.Namespace, console: Console) -> int:
    runner = AblationRunner(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        eval_task_ids=args.task_id or None,
        repeats=args.repeats,
    )
    result = await runner.run()
    table = Table(title="Ablation")
    table.add_column("Config")
    table.add_column("Mode")
    table.add_column("Memory")
    table.add_column("Passed", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Avg E2E", justify="right")
    table.add_column("Avg tokens", justify="right")
    for config in result.configs:
        aggregate = result.aggregates.get(config.name)
        table.add_row(
            config.name,
            config.mode.value,
            str(config.memory),
            f"{aggregate.passed}/{aggregate.total_runs}" if aggregate else "-",
            f"{aggregate.goal_completion_rate:.1%}" if aggregate else "-",
            f"{aggregate.average_end_to_end_seconds:.3f}s" if aggregate else "-",
            f"{aggregate.average_tokens:.1f}" if aggregate else "-",
        )
    console.print(table)
    console.print(
        Text(
            "Same dataset/tasks/provider/budgets. Fixture results validate plumbing, "
            "not real-LLM learning; no statistical significance is claimed.",
            style="yellow",
        )
    )
    console.print(Text(f"Ablation JSON: {Path(args.output_dir) / result.ablation_id / 'ablation.json'}"))
    return 0


def _load_runs(batch_dir: Path | None, output_dir: str) -> tuple[Path | None, list[BenchmarkRun]]:
    if batch_dir is None:
        batch_dir = latest_batch(Path(output_dir))
    if batch_dir is None:
        return None, []
    return batch_dir, read_jsonl(batch_dir / "runs.jsonl")


def _report(args: argparse.Namespace, console: Console) -> int:
    batch_dir, runs = _load_runs(args.batch_dir, args.output_dir)
    if batch_dir is None:
        console.print(Text("No benchmark batch found.", style="red"))
        return 1
    if not runs:
        console.print(Text("Batch has no runs.", style="red"))
        return 1
    batch = read_batch_manifest(batch_dir / "batch.json")
    content = render_benchmark_report(batch, runs)
    output = Path(args.output) if args.output else batch_dir / "report.md"
    output.write_text(content, encoding="utf-8")
    console.print(Text(f"Wrote {output}"))
    return 0


def _failures(args: argparse.Namespace, console: Console) -> int:
    batch_dir, runs = _load_runs(args.batch_dir, args.output_dir)
    if batch_dir is None:
        console.print(Text("No benchmark batch found.", style="red"))
        return 1
    failed = [run for run in runs if run.failure is not None]
    if not failed:
        console.print(Text("No classified failures.", style="green"))
        return 0
    table = Table(title="Failures")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("Category")
    table.add_column("Reason")
    for run in failed:
        table.add_row(
            run.task_id,
            run.run_status.value,
            run.failure.category if run.failure else "-",
            (run.failure.reason if run.failure else "")[:120],
        )
    console.print(table)
    return 0


async def _live(args: argparse.Namespace, settings_factory, console: Console) -> int:
    from agentforge.benchmark.live import LiveAcceptanceRunner

    settings = settings_factory(args)
    stage = getattr(args, "stage", None)
    if stage is not None:
        from agentforge.benchmark.live_stages import LiveStageRunner

        start_stage = 1
        end_stage = 6
        if stage != "all":
            start_stage = end_stage = int(stage)
        stage_runner = LiveStageRunner(
            settings,
            allow_live_api=args.allow_live_api,
            output_dir=args.output_dir,
            max_requests=args.max_requests,
            max_tokens=args.max_tokens,
            start_stage=start_stage,
            end_stage=end_stage,
            run_memory_experiment=not args.skip_memory,
        )
        stage_report = await stage_runner.run()
        table = Table(
            title=f"Staged live acceptance: {stage_report.overall_status}"
        )
        table.add_column("Stage", justify="right")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Detail")
        for result in stage_report.stages:
            table.add_row(
                str(result.stage),
                result.name,
                result.status,
                result.detail[:140],
            )
        console.print(table)
        console.print(
            Text(
                f"Budget: requests={stage_report.budget.get('requests', 0)}/"
                f"{stage_report.budget.get('max_requests', args.max_requests)}, "
                f"tokens={stage_report.budget.get('tokens', 0)}/"
                f"{stage_report.budget.get('max_tokens', args.max_tokens)}"
            )
        )
        if stage_report.overall_status == "NOT_VERIFIED":
            console.print(
                Text(
                    "NOT VERIFIED: no API key or explicit --allow-live-api; "
                    "no paid API call was made.",
                    style="bold yellow",
                )
            )
        return (
            0
            if stage_report.overall_status in {"PASSED", "NOT_VERIFIED"}
            else 1
        )

    runner = LiveAcceptanceRunner(
        settings,
        allow_live_api=args.allow_live_api,
        output_dir=args.output_dir,
    )
    report = await runner.run()
    table = Table(title=f"Live acceptance: {report.status}")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in report.checks:
        table.add_row(check.check_id, check.status, check.detail[:120])
    console.print(table)
    if not report.api_key_configured or not report.live_api_allowed:
        console.print(
            Text(
                "NOT VERIFIED: no paid API call was made. Fixture harness validation "
                "is included in the live report.",
                style="bold yellow",
            )
        )
    return 0
