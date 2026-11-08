"""Controlled with/without-memory experiment (pipeline verification)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentforge.config.settings import Settings
from agentforge.llm.base import LLMResponse, TokenUsage
from agentforge.llm.mock import MockProvider
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import MemoryType, PromotionState, VerificationLevel
from agentforge.runtime import AgentForgeRuntime

logger = logging.getLogger("agentforge.memory.experiment")

EXPERIMENT_GOAL = "Create a note and verify it"
LEARNING_PATH = "notes/verified.md"
DEFAULT_PATH = "notes/default.md"


class MemoryExperimentResult(BaseModel):
    experiment_dir: str
    task: str
    llm_provider: str
    llm_model: str
    llm_is_mock: bool = True
    learning_runs: int
    learning_workflow_ids: list[str] = Field(default_factory=list)
    candidate_support: int = 0
    promoted_memory_id: str | None = None
    promoted_verification: str | None = None
    promoted_confidence: float | None = None
    with_memory_path: str | None = None
    without_memory_path: str | None = None
    action_changed: bool = False
    with_memory_completed: bool = False
    without_memory_completed: bool = False
    with_memory_tool_calls: int = 0
    without_memory_tool_calls: int = 0
    with_memory_saw_procedural: bool = False
    without_memory_saw_procedural: bool = False
    with_memory_prompt_tokens: int = 0
    without_memory_prompt_tokens: int = 0
    with_memory_seconds: float = 0.0
    without_memory_seconds: float = 0.0
    feedback_used: bool = False
    feedback_success: bool = False
    feedback_confidence_delta: float = 0.0
    feedback_count: int = 0
    controlled: dict[str, Any] = Field(default_factory=dict)
    pipeline_verified: bool = False
    real_execution_verified: bool = False
    real_llm_learning_verified: bool = False
    notes: str = ""


class MemoryAwareFileProvider(MockProvider):
    """Deterministic mock policy that reads procedural memory when present.

    This is a test fixture for the memory *pipeline*, not evidence of real LLM
    learning. Tool calls themselves are real file operations.
    """

    def __init__(self, *, bootstrap_path: str | None = None) -> None:
        super().__init__()
        self.bootstrap_path = bootstrap_path
        self.chosen_paths: list[str] = []
        self.saw_procedural_memory = False

    def _resp(self, content: str) -> LLMResponse:
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            latency_ms=0.0,
        )

    async def complete(self, messages, *, purpose=None, temperature=None, max_tokens=None):
        prompt = "\n\n".join(message.content for message in messages)
        if purpose == "plan":
            return self._resp(
                json.dumps(
                    {
                        "goal_summary": "Memory-aware note task",
                        "subtasks": [
                            {
                                "key": "note",
                                "description": (
                                    "Create a note and verify it by reading it back."
                                ),
                                "required_capabilities": ["coding"],
                                "depends_on": [],
                                "kind": "coding",
                            }
                        ],
                    }
                )
            )

        if purpose and purpose.startswith("execute:coding:coding"):
            evidence_count = prompt.count("[TOOL_RESULTS]")
            if evidence_count == 0:
                memory_hit = (
                    "[procedural|REAL_EXECUTION" in prompt
                    and LEARNING_PATH in prompt
                )
                self.saw_procedural_memory = self.saw_procedural_memory or memory_hit
                if memory_hit:
                    path = LEARNING_PATH
                elif self.bootstrap_path is not None:
                    path = self.bootstrap_path
                else:
                    path = DEFAULT_PATH
                self.chosen_paths.append(path)
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": path,
                                        "content": "# Memory experiment\nnote",
                                        "overwrite": True,
                                    },
                                }
                            ],
                            "reasoning": (
                                "Use the retrieved procedural path."
                                if memory_hit
                                else "Use the default/bootstrap path."
                            ),
                        }
                    )
                )
            path = self.chosen_paths[-1] if self.chosen_paths else DEFAULT_PATH
            if evidence_count == 1:
                return self._resp(
                    json.dumps(
                        {
                            "action": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "read_file",
                                    "arguments": {"path": path},
                                }
                            ],
                            "reasoning": "Verify the note content.",
                        }
                    )
                )
            return self._resp(
                json.dumps(
                    {
                        "action": "complete",
                        "content": (
                            f"Created and verified {path} using real file tools. "
                            f"Memory-guided={self.saw_procedural_memory}."
                        ),
                        "reasoning": "done",
                    }
                )
            )

        return await super().complete(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class MemoryExperimentRunner:
    """Controlled ablation: same model/task/tools/budget; only memory differs."""

    def __init__(
        self,
        base_settings: Settings,
        *,
        learning_runs: int = 2,
        experiment_dir: Path | None = None,
    ) -> None:
        self.base_settings = base_settings
        self.learning_runs = max(2, learning_runs)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.experiment_dir = (
            Path(experiment_dir)
            if experiment_dir is not None
            else Path(base_settings.data_dir) / "memory_experiment" / stamp
        )
        self.experiment_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> MemoryExperimentResult:
        common = self._settings(
            memory_recall=False, memory_evolution=True, provider="mock"
        )
        learning_provider = MemoryAwareFileProvider(bootstrap_path=LEARNING_PATH)
        learning_ids: list[str] = []
        async with AgentForgeRuntime(common, provider=learning_provider) as runtime:
            for _index in range(self.learning_runs):
                workflow = await runtime.submit_goal(EXPERIMENT_GOAL)
                learning_ids.append(workflow.workflow_id)
                if workflow.status.value != "completed":
                    raise RuntimeError(
                        f"learning workflow failed: {workflow.error}"
                    )

        memory_audit = EvolutionMemoryStore(
            common.resolved_memory_file, retrieval_enabled=False
        )
        try:
            promoted = [
                memory
                for memory in memory_audit.list_memories(
                    agent_id="coding",
                    memory_type=MemoryType.PROCEDURAL,
                    verification=VerificationLevel.REAL_EXECUTION,
                )
                if memory.promotion_state == PromotionState.PROMOTED
            ]
            candidates = memory_audit.list_candidates(agent_id="coding")
            promoted_memory = promoted[0] if promoted else None
            candidate_support = max(
                (candidate.support_count for candidate in candidates), default=0
            )
        finally:
            memory_audit.close()

        with_settings = self._settings(
            memory_recall=True, memory_evolution=False, provider="mock"
        )
        with_provider = MemoryAwareFileProvider()
        with_result: dict[str, Any] = {}
        async with AgentForgeRuntime(with_settings, provider=with_provider) as runtime:
            workflow = await runtime.submit_goal(EXPERIMENT_GOAL)
            runtime.memory_engine.process_workflow(workflow.workflow_id, learn=False)
            with_result = self._collect_run(runtime, workflow, with_provider)
            with_result["feedback"] = runtime.memory.list_feedback(
                workflow_id=workflow.workflow_id, agent_id="coding"
            )

        control_settings = self._settings(
            memory_recall=False, memory_evolution=False, provider="mock"
        )
        control_provider = MemoryAwareFileProvider()
        async with AgentForgeRuntime(
            control_settings, provider=control_provider
        ) as runtime:
            workflow = await runtime.submit_goal(EXPERIMENT_GOAL)
            runtime.memory_engine.process_workflow(workflow.workflow_id, learn=False)
            control_result = self._collect_run(runtime, workflow, control_provider)

        feedback = with_result.get("feedback") or []
        action_changed = (
            with_result.get("path") is not None
            and control_result.get("path") is not None
            and with_result["path"] != control_result["path"]
        )
        pipeline_verified = (
            with_result["completed"]
            and control_result["completed"]
            and action_changed
            and any(item.used and item.success for item in feedback)
        )
        real_execution = bool(
            promoted_memory is not None
            and promoted_memory.verification_level == VerificationLevel.REAL_EXECUTION
        )

        return MemoryExperimentResult(
            experiment_dir=str(self.experiment_dir),
            task=EXPERIMENT_GOAL,
            llm_provider=learning_provider.name,
            llm_model=learning_provider.model,
            llm_is_mock=True,
            learning_runs=self.learning_runs,
            learning_workflow_ids=learning_ids,
            candidate_support=candidate_support,
            promoted_memory_id=promoted_memory.memory_id if promoted_memory else None,
            promoted_verification=(
                promoted_memory.verification_level.value if promoted_memory else None
            ),
            promoted_confidence=(
                promoted_memory.confidence if promoted_memory else None
            ),
            with_memory_path=with_result.get("path"),
            without_memory_path=control_result.get("path"),
            action_changed=action_changed,
            with_memory_completed=with_result["completed"],
            without_memory_completed=control_result["completed"],
            with_memory_tool_calls=with_result["tool_calls"],
            without_memory_tool_calls=control_result["tool_calls"],
            with_memory_saw_procedural=bool(
                with_result.get("saw_procedural_memory")
            ),
            without_memory_saw_procedural=bool(
                control_result.get("saw_procedural_memory")
            ),
            with_memory_prompt_tokens=with_result["prompt_tokens"],
            without_memory_prompt_tokens=control_result["prompt_tokens"],
            with_memory_seconds=with_result["seconds"],
            without_memory_seconds=control_result["seconds"],
            feedback_used=any(item.used for item in feedback),
            feedback_success=any(item.used and item.success for item in feedback),
            feedback_confidence_delta=sum(
                item.confidence_delta for item in feedback
            ),
            feedback_count=len(feedback),
            controlled={
                "same_goal": True,
                "same_provider_class": True,
                "same_llm_provider": "mock",
                "same_tool_registry": True,
                "same_tool_permissions": True,
                "same_max_tool_calls": self.base_settings.max_tool_calls_per_task,
                "same_max_execution_steps": self.base_settings.max_execution_steps,
                "only_memory_recall_differs": True,
                "memory_evolution_disabled_during_evaluation": True,
            },
            pipeline_verified=pipeline_verified,
            real_execution_verified=real_execution,
            real_llm_learning_verified=False,
            notes=(
                "Mock LLM decision policy; file tool execution is real. This "
                "verifies reflection/storage/retrieval/action/feedback plumbing, "
                "not real-LLM learning performance."
            ),
        )

    def _settings(
        self, *, memory_recall: bool, memory_evolution: bool, provider: str
    ) -> Settings:
        return self.base_settings.model_copy(
            deep=True,
            update={
                "data_dir": self.experiment_dir,
                "sqlite_file": self.experiment_dir / "agentforge.db",
                "events_file": self.experiment_dir / "events.jsonl",
                "workspace_root": self.experiment_dir / "workspaces",
                "artifacts_root": self.experiment_dir / "artifacts",
                "memory_file": self.experiment_dir / "memory_evolution.db",
                "log_level": "WARNING",
                "llm_provider": provider,
                "memory_recall_enabled": memory_recall,
                "memory_evolution_enabled": memory_evolution,
            },
        )

    @staticmethod
    def _collect_run(
        runtime: AgentForgeRuntime,
        workflow,
        provider: MemoryAwareFileProvider,
    ) -> dict[str, Any]:
        records = runtime.list_tool_calls(workflow.workflow_id)
        write = next(
            (record for record in records if record.tool_name == "write_file"),
            None,
        )
        return {
            "completed": workflow.status.value == "completed",
            "path": (
                write.arguments.get("path") if write is not None else None
            ),
            "tool_calls": len(records),
            "prompt_tokens": workflow.metrics.prompt_tokens,
            "seconds": workflow.duration_seconds() or 0.0,
            "saw_procedural_memory": provider.saw_procedural_memory,
            "chosen_paths": list(provider.chosen_paths),
            "workflow_id": workflow.workflow_id,
        }
