"""Benchmark exports and Markdown reports."""

from __future__ import annotations

import csv
import json
import platform
import sys
from pathlib import Path
from typing import Iterable

from agentforge.benchmark.models import (
    AblationResult,
    AggregateMetrics,
    BenchmarkBatch,
    BenchmarkRun,
    ProviderKind,
)
from agentforge.benchmark.metrics import aggregate_runs


def write_jsonl(path: Path, records: Iterable[BenchmarkRun]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")


def append_jsonl(path: Path, record: BenchmarkRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")


def read_jsonl(path: Path) -> list[BenchmarkRun]:
    if not path.exists():
        return []
    records: list[BenchmarkRun] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(BenchmarkRun.model_validate_json(line))
    return records


def write_csv(path: Path, records: Iterable[BenchmarkRun]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(records)
    fieldnames = [
        "run_id",
        "batch_id",
        "task_id",
        "category",
        "difficulty",
        "repeat_index",
        "mode",
        "provider_kind",
        "provider_name",
        "model",
        "run_status",
        "verification_status",
        "workflow_status",
        "goal_success",
        "end_to_end_seconds",
        "active_seconds",
        "human_wait_seconds",
        "llm_calls",
        "total_tokens",
        "tool_calls",
        "tool_failures",
        "retry_count",
        "delegation_count",
        "memory_retrieval_count",
        "memory_usage_count",
        "approval_count",
        "clarification_count",
        "failure_category",
        "failure_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for run in rows:
            writer.writerow(
                {
                    "run_id": run.run_id,
                    "batch_id": run.batch_id,
                    "task_id": run.task_id,
                    "category": run.category.value,
                    "difficulty": run.difficulty.value,
                    "repeat_index": run.repeat_index,
                    "mode": run.mode.value,
                    "provider_kind": run.provider_kind.value,
                    "provider_name": run.provider_name,
                    "model": run.model,
                    "run_status": run.run_status.value,
                    "verification_status": (
                        run.verification.status.value
                        if run.verification
                        else ""
                    ),
                    "workflow_status": (
                        run.workflow_status.value if run.workflow_status else ""
                    ),
                    "goal_success": run.metrics.goal_success,
                    "end_to_end_seconds": f"{run.metrics.end_to_end_seconds:.6f}",
                    "active_seconds": f"{run.metrics.active_seconds:.6f}",
                    "human_wait_seconds": f"{run.metrics.human_wait_seconds:.6f}",
                    "llm_calls": run.metrics.llm_calls,
                    "total_tokens": run.metrics.total_tokens,
                    "tool_calls": run.metrics.tool_calls,
                    "tool_failures": run.metrics.tool_failures,
                    "retry_count": run.metrics.retry_count,
                    "delegation_count": run.metrics.delegation_count,
                    "memory_retrieval_count": run.metrics.memory_retrieval_count,
                    "memory_usage_count": run.metrics.memory_usage_count,
                    "approval_count": run.metrics.approval_count,
                    "clarification_count": run.metrics.clarification_count,
                    "failure_category": (
                        run.failure.category if run.failure else ""
                    ),
                    "failure_reason": (
                        run.failure.reason if run.failure else ""
                    ),
                }
            )


def write_batch_manifest(path: Path, batch: BenchmarkBatch) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(batch.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_batch_manifest(path: Path) -> BenchmarkBatch:
    return BenchmarkBatch.model_validate_json(path.read_text(encoding="utf-8"))


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _environment_lines() -> list[str]:
    return [
        f"- OS: `{platform.platform()}`",
        f"- Python: `{sys.version.split()[0]}`",
        f"- Executable: `{sys.executable}`",
    ]


def render_benchmark_report(
    batch: BenchmarkBatch,
    runs: list[BenchmarkRun],
    *,
    aggregate: AggregateMetrics | None = None,
    ablation: AblationResult | None = None,
    extra_notes: list[str] | None = None,
) -> str:
    aggregate = aggregate or aggregate_runs(runs)
    fixture = batch.config.provider_kind == ProviderKind.FIXTURE
    lines: list[str] = []
    lines.append("# AgentForge Benchmark Report")
    lines.append("")
    lines.append(f"- Batch: `{batch.batch_id}`")
    lines.append(f"- Dataset: `{batch.dataset_id}` (`{batch.dataset_path}`)")
    lines.append(f"- Git commit: `{batch.git_commit or 'unknown'}`")
    lines.append(f"- Mode: `{batch.config.mode.value}`")
    lines.append(
        f"- Provider: `{batch.config.provider_name}` "
        f"({batch.config.provider_kind.value})"
    )
    lines.append(f"- Repeats: {batch.config.repeats}")
    lines.append(f"- Runs: {aggregate.total_runs}")
    lines.append("")
    lines.extend(_environment_lines())
    lines.append("")
    if fixture:
        lines.append(
            "> **MOCK FIXTURE RESULT.** LLM actions are deterministic fixtures used "
            "only to validate the benchmark harness, tools, state machine and "
            "verifiers. These scores are NOT real-LLM capability and must not be "
            "reported as model performance."
        )
    else:
        lines.append(
            "> **LIVE PROVIDER RESULT.** Actions were produced by the configured "
            "real LLM provider. API errors and model variability remain possible."
        )
    lines.append("")
    lines.append("## 1. Aggregate results")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Goal completion rate (verified) | {_pct(aggregate.goal_completion_rate)} |")
    lines.append(f"| Passed / failed | {aggregate.passed} / {aggregate.failed} |")
    lines.append(f"| Needs review / skipped | {aggregate.needs_review} / {aggregate.skipped} |")
    lines.append(f"| Timeout / runtime error | {aggregate.timeout} / {aggregate.errors} |")
    lines.append(f"| Average E2E seconds | {aggregate.average_end_to_end_seconds:.3f} |")
    lines.append(f"| Median E2E seconds | {aggregate.median_end_to_end_seconds:.3f} |")
    lines.append(f"| p95 E2E seconds | {aggregate.p95_end_to_end_seconds:.3f} |")
    lines.append(f"| Average active seconds | {aggregate.average_active_seconds:.3f} |")
    lines.append(f"| Total simulated human wait | {aggregate.total_human_wait_seconds:.3f}s |")
    lines.append(f"| Total LLM calls | {aggregate.total_llm_calls} |")
    lines.append(f"| Total / average tokens | {aggregate.total_tokens} / {aggregate.average_tokens:.1f} |")
    lines.append(f"| Total tool calls | {aggregate.total_tool_calls} |")
    lines.append(f"| Tool failure rate | {_pct(aggregate.tool_failure_rate)} |")
    lines.append(f"| Retries (repeated identical calls) | {aggregate.total_retries} |")
    lines.append(
        f"| Structured-output repairs | {aggregate.total_structured_repairs} |"
    )
    lines.append(f"| Delegations / success rate | {aggregate.total_delegations} / {_pct(aggregate.delegation_success_rate)} |")
    lines.append(f"| Memory retrievals / used | {aggregate.total_memory_retrievals} / {aggregate.total_memory_usage} |")
    lines.append(f"| Approvals / clarifications | {aggregate.total_approvals} / {aggregate.total_clarifications} |")
    lines.append(f"| Recovery success rate | {_pct(aggregate.recovery_success_rate)} |")
    lines.append(f"| Crash/timeout rate | {_pct(aggregate.crash_or_timeout_rate)} |")
    lines.append("")
    lines.append(
        f"Sample size: **{aggregate.total_runs} runs**. Per-category rates are "
        "descriptive; no confidence intervals or statistical significance are "
        "claimed. `needs_review` and `skipped` are not successes."
    )
    lines.append("")
    lines.append(
        "Token counts come from provider usage for live runs and prompt/response "
        "length estimates for fixture runs; they must not be compared as if both "
        "were billed API tokens."
    )
    lines.append("")

    lines.append("## 2. Results by category")
    lines.append("")
    lines.append("| Category | Total | Passed | Success | Avg E2E | Avg active | Avg tools | Avg LLM calls |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for item in aggregate.by_category:
        lines.append(
            f"| {item.category.value} | {item.total} | {item.passed} | "
            f"{_pct(item.success_rate)} | {item.average_end_to_end_seconds:.3f}s | "
            f"{item.average_active_seconds:.3f}s | {item.average_tool_calls:.2f} | "
            f"{item.average_llm_calls:.2f} |"
        )
    lines.append("")

    lines.append("## 3. Per-task runs")
    lines.append("")
    lines.append("| Task | Category | Diff | Status | Verified | E2E | Active | Tokens | Tools | Failure |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|---|")
    for run in runs:
        verification = (
            run.verification.status.value if run.verification else "-"
        )
        failure = run.failure.category if run.failure else "-"
        lines.append(
            f"| `{run.task_id}` | {run.category.value} | {run.difficulty.value} | "
            f"{run.run_status.value} | {verification} | "
            f"{run.metrics.end_to_end_seconds:.3f}s | "
            f"{run.metrics.active_seconds:.3f}s | {run.metrics.total_tokens} | "
            f"{run.metrics.tool_calls} | {failure} |"
        )
    lines.append("")

    lines.append("## 4. Failure modes")
    lines.append("")
    counts: dict[str, int] = {}
    for run in runs:
        if run.failure is not None:
            counts[run.failure.category] = counts.get(run.failure.category, 0) + 1
    if counts:
        lines.append("| Failure category | Count |")
        lines.append("|---|---:|")
        for category, count in sorted(counts.items(), key=lambda item: -item[1]):
            lines.append(f"| {category} | {count} |")
    else:
        lines.append("No classified failures.")
    lines.append("")

    if ablation is not None:
        lines.append(render_ablation_section(ablation))
        lines.append("")

    lines.append("## 5. Metric definitions")
    lines.append("")
    lines.append(
        "- **Goal completion** is decided only by the task's objective verifier, "
        "not by `workflow.status == completed`."
    )
    lines.append(
        "- **E2E seconds** are monotonic wall-clock from task start to final "
        "observation; timestamps remain in the JSON record for audit."
    )
    lines.append(
        "- **Active seconds** exclude scripted human waiting. **Human wait** is "
        "simulated and explicitly labeled."
    )
    lines.append(
        "- **Token usage** for fixture runs is an estimate from prompt/response "
        "length; live runs use provider usage."
    )
    lines.append(
        "- **Retries** count repeated identical `(tool, arguments)` invocations "
        "beyond the first."
    )
    lines.append("")

    if extra_notes:
        lines.append("## 6. Notes and limitations")
        lines.append("")
        for note in extra_notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def render_ablation_section(ablation: AblationResult) -> str:
    lines = ["## Ablation study", ""]
    lines.append(
        "All configurations use the same task set, provider kind/model, "
        "temperature, tool framework and budgets."
    )
    lines.append("")
    lines.append("| Config | Mode | Memory | Runs | Verified success | Avg E2E | Avg active | Avg tokens | Tool failures |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for config in ablation.configs:
        aggregate = ablation.aggregates.get(config.name)
        if aggregate is None:
            lines.append(
                f"| {config.name} | {config.mode.value} | {config.memory} | "
                "0 | - | - | - | - | - |"
            )
            continue
        lines.append(
            f"| {config.name} | {config.mode.value} | {config.memory} | "
            f"{aggregate.total_runs} | {_pct(aggregate.goal_completion_rate)} | "
            f"{aggregate.average_end_to_end_seconds:.3f}s | "
            f"{aggregate.average_active_seconds:.3f}s | "
            f"{aggregate.average_tokens:.1f} | "
            f"{_pct(aggregate.tool_failure_rate)} |"
        )
    lines.append("")
    lines.append(
        f"Initial shared memory entries before evaluation: "
        f"{ablation.initial_memory_entries}. Learning tasks are excluded from "
        "the evaluation aggregates."
    )
    if any(
        config.name == "C_multi_agent_memory" for config in ablation.configs
    ):
        lines.append("")
        lines.append(
            "With the deterministic fixture policy, memory is not used to choose "
            "answers, so any completion difference is plumbing/noise rather than "
            "evidence of real-LLM learning. Statistical significance is not "
            "claimed."
        )
    return "\n".join(lines)
