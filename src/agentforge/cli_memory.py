"""Phase 3 memory CLI commands."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agentforge.config.settings import Settings
from agentforge.memory.embeddings import HashingEmbeddingProvider
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.experiment import MemoryExperimentRunner
from agentforge.memory.models import (
    MemoryRetrievalConfig,
    MemoryType,
    PromotionState,
    VerificationLevel,
)


async def run_memory_command(
    args: argparse.Namespace,
    *,
    settings_factory: Callable[[argparse.Namespace], Settings],
    console: Console,
) -> int:
    command = getattr(args, "memory_command", None)
    if not command:
        console.print(
            Text(
                "usage: agentforge memory "
                "{list,search,stats,candidates,feedback,clear,experiment} ...",
                style="yellow",
            )
        )
        return 0
    if command == "experiment":
        settings = settings_factory(args)
        runner = MemoryExperimentRunner(
            settings,
            learning_runs=max(2, args.learning_runs),
            experiment_dir=(
                Path(args.experiment_dir) if args.experiment_dir else None
            ),
        )
        console.rule("[bold]Phase 3 Memory Experiment[/bold]")
        console.print(
            Text(
                "Pipeline verification: deterministic Mock LLM policy + REAL file "
                "tool execution. Real-LLM learning performance is NOT VERIFIED.",
                style="bold yellow",
            )
        )
        result = await runner.run()
        _print_experiment(console, result)
        return 0 if result.pipeline_verified else 1

    settings = settings_factory(args)
    settings.ensure_directories()
    store = _open_store(settings)
    try:
        if command == "list":
            memories = store.list_memories(
                agent_id=args.agent,
                namespace=args.namespace,
                memory_type=MemoryType(args.type) if args.type else None,
                verification=(
                    VerificationLevel(args.verification)
                    if args.verification
                    else None
                ),
                limit=args.limit,
            )
            _print_memories(console, memories)
            return 0
        if command == "search":
            memories = store.search(
                args.query,
                agent_id=args.agent,
                namespace=args.namespace,
                include_mock=args.include_mock,
                include_unverified=args.include_unverified,
                limit=args.limit,
            )
            _print_memories(console, memories)
            return 0
        if command == "stats":
            _print_stats(console, store.stats(agent_id=args.agent))
            return 0
        if command == "candidates":
            candidates = store.list_candidates(
                agent_id=args.agent,
                status=PromotionState(args.status) if args.status else None,
                limit=args.limit,
            )
            _print_candidates(console, candidates)
            return 0
        if command == "feedback":
            feedback = store.list_feedback(
                workflow_id=args.workflow,
                agent_id=args.agent,
                limit=args.limit,
            )
            _print_feedback(console, feedback)
            return 0
        if command == "clear":
            if not args.confirm:
                console.print(
                    Text("Refusing to clear memory without --confirm.", style="red")
                )
                return 2
            counts = store.clear(agent_id=args.agent, namespace=args.namespace)
            console.print(
                Text(
                    "Memory cleared: "
                    + ", ".join(f"{key}={value}" for key, value in counts.items()),
                    style="yellow",
                )
            )
            return 0
    finally:
        store.close()
    console.print(Text(f"unknown memory command {command!r}", style="red"))
    return 2


def _open_store(settings: Settings) -> EvolutionMemoryStore:
    return EvolutionMemoryStore(
        settings.resolved_memory_file,
        embedding_provider=HashingEmbeddingProvider(
            settings.memory_embedding_dimensions
        ),
        retrieval_config=MemoryRetrievalConfig(enabled=False),
        retrieval_enabled=False,
        decay_half_life_days=settings.memory_decay_half_life_days,
        forget_threshold=settings.memory_forget_threshold,
        working_ttl_seconds=settings.memory_working_ttl_seconds,
    )


def _verification_style(value: str) -> str:
    return {
        "REAL_EXECUTION": "bold green",
        "MOCK_SIMULATION": "yellow",
        "UNVERIFIED": "dim",
    }.get(value, "white")


def _print_memories(output: Console, memories: list) -> None:
    if not memories:
        output.print(Text("No memories found.", style="dim"))
        return
    table = Table(title=f"Memories ({len(memories)})", show_lines=True)
    table.add_column("ID", style="dim")
    table.add_column("Agent")
    table.add_column("NS")
    table.add_column("Type")
    table.add_column("Verification")
    table.add_column("Conf", justify="right")
    table.add_column("Support", justify="right")
    table.add_column("Promotion")
    table.add_column("Source WF", style="dim")
    table.add_column("Content")
    for memory in memories:
        source = memory.source
        content = memory.content.replace("\n", " ")
        if len(content) > 180:
            content = content[:180] + "..."
        table.add_row(
            memory.memory_id[:8],
            memory.agent_id,
            memory.namespace,
            memory.memory_type.value,
            Text(
                memory.verification_level.value,
                style=_verification_style(memory.verification_level.value),
            ),
            f"{memory.confidence:.2f}",
            str(memory.support_count),
            memory.promotion_state.value,
            (source.workflow_id[:8] if source and source.workflow_id else "-"),
            content,
        )
    output.print(table)


def _print_stats(output: Console, stats) -> None:
    table = Table(title="Memory statistics", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Total / active", f"{stats.total} / {stats.active}")
    table.add_row("Candidates / promoted", f"{stats.candidates} / {stats.promoted}")
    table.add_row(
        "Feedback used / success",
        f"{stats.feedback_used} / {stats.feedback_success}",
    )
    table.add_row(
        "By agent",
        ", ".join(f"{key}={value}" for key, value in sorted(stats.by_agent.items()))
        or "-",
    )
    table.add_row(
        "By type",
        ", ".join(f"{key}={value}" for key, value in sorted(stats.by_type.items()))
        or "-",
    )
    table.add_row(
        "By verification",
        ", ".join(
            f"{key}={value}" for key, value in sorted(stats.by_verification.items())
        )
        or "-",
    )
    output.print(table)


def _print_candidates(output: Console, candidates: list) -> None:
    if not candidates:
        output.print(Text("No procedural candidates.", style="dim"))
        return
    table = Table(title="Procedural candidates", show_lines=True)
    table.add_column("ID", style="dim")
    table.add_column("Agent")
    table.add_column("Verification")
    table.add_column("Support", justify="right")
    table.add_column("Status")
    table.add_column("Promoted")
    table.add_column("Content")
    for candidate in candidates:
        content = candidate.content.replace("\n", " ")
        if len(content) > 220:
            content = content[:220] + "..."
        table.add_row(
            candidate.candidate_id[:8],
            candidate.agent_id,
            Text(
                candidate.verification_level.value,
                style=_verification_style(candidate.verification_level.value),
            ),
            str(candidate.support_count),
            candidate.status.value,
            (candidate.promoted_memory_id[:8] if candidate.promoted_memory_id else "-"),
            content,
        )
    output.print(table)


def _print_feedback(output: Console, feedback: list) -> None:
    if not feedback:
        output.print(Text("No memory feedback.", style="dim"))
        return
    table = Table(title=f"Memory feedback ({len(feedback)})")
    table.add_column("Memory", style="dim")
    table.add_column("Agent")
    table.add_column("Workflow", style="dim")
    table.add_column("Used")
    table.add_column("Success")
    table.add_column("Δ confidence", justify="right")
    for item in feedback:
        table.add_row(
            item.memory_id[:8],
            item.agent_id,
            (item.workflow_id[:8] if item.workflow_id else "-"),
            "yes" if item.used else "no",
            "yes" if item.success else "no",
            f"{item.confidence_delta:+.2f}",
        )
    output.print(table)


def _print_experiment(output: Console, result) -> None:
    table = Table(show_header=False)
    table.add_column("Item", style="bold")
    table.add_column("Value")
    table.add_row("Experiment dir", result.experiment_dir)
    table.add_row("Task", result.task)
    table.add_row("LLM", f"{result.llm_provider} ({result.llm_model})")
    table.add_row("Learning workflows", str(len(result.learning_workflow_ids)))
    table.add_row("Candidate support", str(result.candidate_support))
    table.add_row("Promoted memory", result.promoted_memory_id or "(none)")
    table.add_row(
        "Promoted verification",
        (
            f"{result.promoted_verification} confidence="
            f"{result.promoted_confidence:.2f}"
            if result.promoted_confidence is not None
            else "(none)"
        ),
    )
    table.add_row("With memory path", result.with_memory_path or "-")
    table.add_row("Without memory path", result.without_memory_path or "-")
    table.add_row(
        "Procedural memory retrieved",
        f"with={result.with_memory_saw_procedural} "
        f"without={result.without_memory_saw_procedural}",
    )
    table.add_row("Action changed", "YES" if result.action_changed else "NO")
    table.add_row(
        "Feedback",
        f"used={result.feedback_used} success={result.feedback_success} "
        f"delta={result.feedback_confidence_delta:+.2f}",
    )
    table.add_row(
        "Pipeline verified", "YES" if result.pipeline_verified else "NO"
    )
    table.add_row(
        "Real execution verified",
        "YES" if result.real_execution_verified else "NO",
    )
    table.add_row(
        "Real LLM learning effect",
        "VERIFIED" if result.real_llm_learning_verified else "NOT VERIFIED",
    )
    output.print(table)
    output.print(Text(result.notes, style="yellow"))
