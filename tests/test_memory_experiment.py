"""Controlled with/without-memory experiment tests."""

from __future__ import annotations

import pytest

from agentforge.config.settings import Settings
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.experiment import (
    DEFAULT_PATH,
    EXPERIMENT_GOAL,
    LEARNING_PATH,
    MemoryExperimentRunner,
)
from agentforge.memory.models import MemoryType, VerificationLevel


@pytest.fixture
def experiment_settings(tmp_path):
    return Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=tmp_path / "base",
        log_level="WARNING",
        memory_recall_limit=6,
    )


async def test_memory_retrieval_changes_subsequent_tool_action(
    experiment_settings, tmp_path
):
    runner = MemoryExperimentRunner(
        experiment_settings,
        learning_runs=2,
        experiment_dir=tmp_path / "experiment",
    )
    result = await runner.run()

    assert result.task == EXPERIMENT_GOAL
    assert result.learning_runs == 2
    assert result.candidate_support == 2
    assert result.promoted_memory_id is not None
    assert result.promoted_verification == VerificationLevel.REAL_EXECUTION.value
    assert result.real_execution_verified is True
    assert result.with_memory_completed is True
    assert result.without_memory_completed is True
    assert result.with_memory_path == LEARNING_PATH
    assert result.without_memory_path == DEFAULT_PATH
    assert result.action_changed is True
    assert result.with_memory_saw_procedural is True
    assert result.without_memory_saw_procedural is False
    assert result.feedback_count >= 1
    assert result.feedback_used is True
    assert result.feedback_success is True
    assert result.feedback_confidence_delta > 0
    assert result.pipeline_verified is True
    assert result.real_llm_learning_verified is False
    assert result.llm_is_mock is True
    assert "NOT REAL-WORLD VERIFIED" not in result.notes
    assert result.controlled["same_goal"] is True
    assert result.controlled["same_tool_permissions"] is True
    assert result.controlled["only_memory_recall_differs"] is True

    # The promoted memory and feedback are durable in the experiment DB.
    store = EvolutionMemoryStore(
        tmp_path / "experiment" / "memory_evolution.db",
        retrieval_enabled=False,
    )
    try:
        promoted = store.get_memory(result.promoted_memory_id)
        assert promoted is not None
        assert promoted.memory_type == MemoryType.PROCEDURAL
        assert promoted.verification_level == VerificationLevel.REAL_EXECUTION
        assert promoted.confidence >= 0.85
        events = store.list_retrieval_events(agent_id="coding")
        assert events
        feedback = store.list_feedback(agent_id="coding")
        assert any(item.used and item.success for item in feedback)
    finally:
        store.close()


async def test_experiment_does_not_claim_real_llm_learning(experiment_settings, tmp_path):
    runner = MemoryExperimentRunner(
        experiment_settings,
        learning_runs=2,
        experiment_dir=tmp_path / "experiment_labels",
    )
    result = await runner.run()
    assert result.llm_provider == "mock"
    assert result.real_llm_learning_verified is False
    assert "not real-LLM learning" in result.notes
    assert "real" in result.notes.lower()
