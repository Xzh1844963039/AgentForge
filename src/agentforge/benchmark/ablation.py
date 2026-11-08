"""Single-agent / multi-agent / memory ablation framework."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agentforge.benchmark.models import (
    AblationConfig,
    AblationResult,
    BenchmarkConfig,
    BenchmarkMode,
    ProviderKind,
)
from agentforge.benchmark.report import render_ablation_section
from agentforge.benchmark.runner import BenchmarkRunner
from agentforge.memory.evolution_store import EvolutionMemoryStore


DEFAULT_ABLATION_CONFIGS = [
    AblationConfig(
        name="A_single_agent",
        mode=BenchmarkMode.SINGLE_AGENT,
        memory=False,
        description="One all-capability agent, same tool framework, no memory.",
    ),
    AblationConfig(
        name="B_multi_agent",
        mode=BenchmarkMode.MULTI_AGENT,
        memory=False,
        description="Default four-agent runtime without memory.",
    ),
    AblationConfig(
        name="C_multi_agent_memory",
        mode=BenchmarkMode.MULTI_AGENT,
        memory=True,
        description="Default four-agent runtime with learning memory.",
    ),
]

DEFAULT_EVAL_TASKS = [
    "research_basic_facts",
    "codegen_add_function",
    "debug_off_by_one",
    "file_exact_create",
    "execute_compute_sum",
    "plan_research_to_coding",
]

DEFAULT_LEARNING_TASKS = [
    "file_multi_artifact",
    "file_revision_copy",
]


class AblationRunner:
    def __init__(
        self,
        *,
        dataset_path: str = "benchmarks/dev/tasks.jsonl",
        output_dir: str = "benchmarks/runs",
        eval_task_ids: list[str] | None = None,
        learning_task_ids: list[str] | None = None,
        repeats: int = 1,
        provider_kind: ProviderKind = ProviderKind.FIXTURE,
        provider_name: str = "mock",
    ) -> None:
        self.dataset_path = dataset_path
        self.output_dir = Path(output_dir)
        self.eval_task_ids = eval_task_ids or list(DEFAULT_EVAL_TASKS)
        self.learning_task_ids = learning_task_ids or list(DEFAULT_LEARNING_TASKS)
        self.repeats = repeats
        self.provider_kind = provider_kind
        self.provider_name = provider_name

    async def run(self) -> AblationResult:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ablation_id = f"ablation_{stamp}_{len(self.eval_task_ids)}x{self.repeats}"
        root = self.output_dir / ablation_id
        root.mkdir(parents=True, exist_ok=True)
        shared_memory = root / "shared_memory.db"
        result = AblationResult(
            ablation_id=ablation_id,
            dataset_id=Path(self.dataset_path).stem,
            task_ids=list(self.eval_task_ids),
            learning_task_ids=list(self.learning_task_ids),
            repeats=self.repeats,
            configs=list(DEFAULT_ABLATION_CONFIGS),
            notes=[
                "Same dataset, provider kind/model, temperature, tool framework "
                "and execution budgets across configurations.",
                "Learning tasks for C are not included in evaluation aggregates.",
            ],
        )
        for config in DEFAULT_ABLATION_CONFIGS:
            if config.memory:
                learning = BenchmarkRunner(
                    BenchmarkConfig(
                        dataset_path=self.dataset_path,
                        output_dir=str(root / "learning"),
                        mode=config.mode,
                        provider_kind=self.provider_kind,
                        provider_name=self.provider_name,
                        task_ids=self.learning_task_ids,
                        repeats=1,
                        memory_evolution=True,
                        memory_recall=False,
                        shared_memory_file=str(shared_memory),
                    )
                )
                learning_outcome = await learning.run_batch()
                result.notes.append(
                    f"C learning phase: {len(learning_outcome.runs)} task(s), "
                    f"{learning_outcome.aggregate.passed} passed."
                )
            config_output = root / config.name.split("_")[0]
            runner = BenchmarkRunner(
                BenchmarkConfig(
                    dataset_path=self.dataset_path,
                    output_dir=str(config_output),
                    mode=config.mode,
                    provider_kind=self.provider_kind,
                    provider_name=self.provider_name,
                    task_ids=self.eval_task_ids,
                    repeats=self.repeats,
                    memory_evolution=False,
                    memory_recall=config.memory,
                    shared_memory_file=str(shared_memory) if config.memory else None,
                )
            )
            outcome = await runner.run_batch()
            result.runs[config.name] = outcome.runs
            result.aggregates[config.name] = outcome.aggregate
        if shared_memory.exists():
            store = EvolutionMemoryStore(shared_memory, retrieval_enabled=False)
            try:
                result.initial_memory_entries = store.total_count()
            finally:
                store.close()
        (root / "ablation.json").write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "ablation.md").write_text(
            "# Ablation Study\n\n" + render_ablation_section(result) + "\n",
            encoding="utf-8",
        )
        return result


def ablation_summary_line(result: AblationResult, name: str) -> str:
    aggregate = result.aggregates.get(name)
    if aggregate is None:
        return f"{name}: no runs"
    return (
        f"{name}: success={aggregate.goal_completion_rate:.1%} "
        f"avg_e2e={aggregate.average_end_to_end_seconds:.3f}s "
        f"avg_tokens={aggregate.average_tokens:.1f}"
    )
