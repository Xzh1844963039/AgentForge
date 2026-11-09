"""Live memory-reuse experiment: learn on separate tasks, evaluate with/without."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentforge.benchmark.models import (
    BenchmarkConfig,
    BenchmarkRun,
    ProviderKind,
    RunStatus,
)
from agentforge.benchmark.runner import BenchmarkRunner
from agentforge.config.settings import Settings
from agentforge.llm.base import BaseLLMProvider
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.persistence.database import SQLiteStore


class LiveMemoryResult(BaseModel):
    status: str = "NOT_VERIFIED"
    learning_runs: list[BenchmarkRun] = Field(default_factory=list)
    off_run: BenchmarkRun | None = None
    on_run: BenchmarkRun | None = None
    promoted_memory_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    initial_memory_entries: int = 0
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    applied_memory_ids: list[str] = Field(default_factory=list)
    action_changed: bool = False
    off_actions: list[dict[str, Any]] = Field(default_factory=list)
    on_actions: list[dict[str, Any]] = Field(default_factory=list)
    off_goal_success: bool = False
    on_goal_success: bool = False
    notes: list[str] = Field(default_factory=list)


class LiveMemoryExperimentRunner:
    def __init__(
        self,
        settings: Settings,
        provider: BaseLLMProvider,
        *,
        output_dir: str | Path = "benchmarks/runs/live_memory",
        learning_repeats: int = 2,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.output_dir = Path(output_dir)
        self.learning_repeats = max(2, learning_repeats)

    async def run(self) -> LiveMemoryResult:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = self.output_dir / f"live_memory_{stamp}"
        root.mkdir(parents=True, exist_ok=True)
        shared_memory = root / "shared_memory.db"
        result = LiveMemoryResult()
        try:
            learning = await BenchmarkRunner(
                BenchmarkConfig(
                    dataset_path="benchmarks/live/memory_tasks.jsonl",
                    output_dir=str(root / "learning"),
                    provider_kind=ProviderKind.LIVE,
                    provider_name=self.settings.llm_provider,
                    task_ids=["live_memory_learning"],
                    repeats=self.learning_repeats,
                    memory_evolution=True,
                    memory_recall=False,
                    shared_memory_file=str(shared_memory),
                ),
                provider_override=self.provider,
            ).run_batch()
            result.learning_runs = learning.runs
            store = EvolutionMemoryStore(shared_memory, retrieval_enabled=False)
            try:
                result.initial_memory_entries = store.total_count()
                result.promoted_memory_ids = [
                    memory.memory_id
                    for memory in store.list_memories(
                        agent_id="coding",
                        include_expired=True,
                    )
                    if memory.promotion_state.value == "promoted"
                ]
                result.candidate_ids = [
                    candidate.candidate_id
                    for candidate in store.list_candidates(agent_id="coding")
                ]
            finally:
                store.close()

            off = await BenchmarkRunner(
                BenchmarkConfig(
                    dataset_path="benchmarks/live/memory_tasks.jsonl",
                    output_dir=str(root / "off"),
                    provider_kind=ProviderKind.LIVE,
                    provider_name=self.settings.llm_provider,
                    task_ids=["live_memory_eval"],
                    repeats=1,
                    memory_evolution=False,
                    memory_recall=False,
                    memory_feedback=True,
                    shared_memory_file=str(shared_memory),
                ),
                provider_override=self.provider,
            ).run_batch()
            on = await BenchmarkRunner(
                BenchmarkConfig(
                    dataset_path="benchmarks/live/memory_tasks.jsonl",
                    output_dir=str(root / "on"),
                    provider_kind=ProviderKind.LIVE,
                    provider_name=self.settings.llm_provider,
                    task_ids=["live_memory_eval"],
                    repeats=1,
                    memory_evolution=False,
                    memory_recall=True,
                    memory_feedback=True,
                    shared_memory_file=str(shared_memory),
                ),
                provider_override=self.provider,
            ).run_batch()
            result.off_run = off.runs[0] if off.runs else None
            result.on_run = on.runs[0] if on.runs else None
            if result.on_run is not None:
                result.off_actions = self._actions(result.off_run)
                result.on_actions = self._actions(result.on_run)
                result.off_goal_success = (
                    result.off_run is not None
                    and result.off_run.run_status == RunStatus.PASSED
                )
                result.on_goal_success = (
                    result.on_run.run_status == RunStatus.PASSED
                )
                result.action_changed = (
                    result.off_actions != result.on_actions
                )
                store = EvolutionMemoryStore(
                    shared_memory, retrieval_enabled=False
                )
                try:
                    if result.on_run.workflow_id:
                        events = store.list_retrieval_events(
                            workflow_id=result.on_run.workflow_id
                        )
                        result.retrieved_memory_ids = sorted(
                            {
                                memory_id
                                for event in events
                                for memory_id in event.memory_ids
                            }
                        )
                        feedback = store.list_feedback(
                            workflow_id=result.on_run.workflow_id
                        )
                        result.applied_memory_ids = sorted(
                            {
                                item.memory_id
                                for item in feedback
                                if item.used
                            }
                        )
                finally:
                    store.close()
            if not result.promoted_memory_ids:
                result.notes.append(
                    "No promoted procedural memory was created; the memory "
                    "experiment is inconclusive."
                )
            if result.on_run is not None and result.on_run.metrics.memory_retrieval_count == 0:
                result.notes.append(
                    "The ON evaluation performed no memory retrieval."
                )
            result.status = (
                "OBSERVED"
                if result.on_run is not None and result.off_run is not None
                else "INCOMPLETE"
            )
        finally:
            (root / "live_memory_result.json").write_text(
                json.dumps(
                    result.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return result

    @staticmethod
    def _actions(run: BenchmarkRun | None) -> list[dict[str, Any]]:
        if run is None or not run.run_data_dir or not run.workflow_id:
            return []
        db_path = Path(run.run_data_dir) / "agentforge.db"
        if not db_path.exists():
            return []
        store = SQLiteStore(db_path)
        try:
            return [
                {
                    "tool": record.tool_name,
                    "arguments": record.arguments,
                    "status": record.status.value,
                }
                for record in store.list_tool_calls(run.workflow_id)
                if record.tool_name in {"write_file", "read_file", "update_file"}
            ]
        finally:
            store.close()
