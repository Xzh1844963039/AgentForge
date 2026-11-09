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
    if command == "hkgai-probe":
        return await _hkgai_probe(args, settings_factory, console)
    if command == "hkgai-experiments":
        return await _hkgai_experiments(args, settings_factory, console)
    if command == "hkgai-compact-planner":
        return await _hkgai_compact_planner(args, settings_factory, console)
    console.print(
        Text(
            "usage: agentforge benchmark "
            "<list|run|compare|report|failures|live|hkgai-probe|"
            "hkgai-experiments|hkgai-compact-planner>"
        )
    )
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


async def _hkgai_probe(args: argparse.Namespace, settings_factory, console: Console) -> int:
    from agentforge.benchmark.hkgai_probe import run_hkgai_probe

    if not args.allow_live_api:
        console.print(
            Text(
                "Refusing the real HKGAI probe: pass --allow-live-api explicitly.",
                style="bold yellow",
            )
        )
        return 2
    settings = settings_factory(args)
    try:
        result = await run_hkgai_probe(
            settings,
            allow_live_api=True,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            output_dir=args.output_dir,
        )
    except PermissionError as exc:
        console.print(Text(str(exc), style="red"))
        return 2
    table = Table(title="HKGAI streaming probe")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Configured model", result.configured_model)
    table.add_row("Response model", result.response_model or "n/a")
    table.add_row("HTTP status", str(result.http_status))
    table.add_row("First byte / event / content ms", f"{result.first_byte_ms} / {result.first_event_ms} / {result.first_content_ms}")
    table.add_row("SSE events / chunks", f"{result.stream_events} / {result.chunks}")
    table.add_row("Content / reasoning chars", f"{result.content_chars} / {result.reasoning_chars}")
    table.add_row("finish_reason", str(result.finish_reason))
    table.add_row("Terminal / missing", f"{result.terminal_event} / {result.terminal_event_missing}")
    table.add_row("Usage reported", str(result.usage_reported))
    table.add_row("Tokens", f"{result.prompt_tokens}+{result.completion_tokens}={result.total_tokens}")
    table.add_row("Elapsed", f"{result.elapsed_seconds}s")
    table.add_row("Complete", str(result.complete))
    table.add_row("Error", result.error_type or "none")
    console.print(table)
    console.print(Text(f"Budget: {result.budget}"))
    console.print(Text(f"Report: {Path(args.output_dir) / ('hkgai_probe_*') / 'PROBE_REPORT.md'}"))
    return 0 if result.complete else 1


async def _hkgai_experiments(args: argparse.Namespace, settings_factory, console: Console) -> int:
    from agentforge.benchmark.hkgai_experiments import run_hkgai_experiments

    if not args.allow_live_api:
        console.print(
            Text(
                "Refusing real HKGAI experiments: pass --allow-live-api explicitly.",
                style="bold yellow",
            )
        )
        return 2
    settings = settings_factory(args)
    try:
        result = await run_hkgai_experiments(
            settings,
            allow_live_api=True,
            experiment=args.experiment,
            output_dir=args.output_dir,
            timeout=args.timeout,
        )
    except PermissionError as exc:
        console.print(Text(str(exc), style="red"))
        return 2
    table = Table(title=f"HKGAI control experiments: {result.status}")
    table.add_column("Experiment")
    table.add_column("Status")
    table.add_column("HTTP")
    table.add_column("Elapsed")
    table.add_column("Tokens")
    table.add_column("JSON")
    table.add_column("Schema")
    table.add_column("Plan")
    for item in result.experiments:
        table.add_row(
            item.experiment,
            item.status,
            str(item.http_status),
            f"{item.elapsed_seconds}s",
            str(item.total_tokens),
            str(item.valid_json),
            str(item.schema_valid),
            str(item.plan_valid),
        )
    console.print(table)
    console.print(Text(f"Output: {result.output_dir}"))
    return 0 if result.status == "PASSED" else 1


async def _hkgai_compact_planner(args: argparse.Namespace, settings_factory, console: Console) -> int:
    from agentforge.benchmark.hkgai_compact_planner import run_hkgai_compact_planner

    if not args.allow_live_api:
        console.print(
            Text(
                "Refusing Compact Planner probe: pass --allow-live-api explicitly.",
                style="bold yellow",
            )
        )
        return 2
    settings = settings_factory(args)
    try:
        result = await run_hkgai_compact_planner(
            settings,
            allow_live_api=True,
            output_dir=args.output_dir,
            timeout=args.timeout,
        )
    except PermissionError as exc:
        console.print(Text(str(exc), style="red"))
        return 2
    table = Table(title=f"HKGAI Compact Planner: {result.status}")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Configured model", result.configured_model)
    table.add_row("Response model", result.response_model or "n/a")
    table.add_row("HTTP status", str(result.http_status))
    table.add_row("Prompt chars", str(result.prompt_chars))
    table.add_row("Elapsed", f"{result.elapsed_seconds}s")
    table.add_row("Tokens", f"{result.prompt_tokens}+{result.completion_tokens}={result.total_tokens}")
    table.add_row("finish_reason", str(result.finish_reason))
    table.add_row("JSON / plan / capability", f"{result.valid_json} / {result.plan_valid} / {result.capability_valid}")
    table.add_row("Subtasks", str(result.subtask_count))
    table.add_row("Error", result.error_type or "none")
    console.print(table)
    console.print(Text(f"Output: {result.output_dir}"))
    return 0 if result.status == "PASSED" else 1
