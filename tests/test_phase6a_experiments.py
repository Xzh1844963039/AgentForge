"""Offline validation and safety-gate tests for HKGAI control experiments."""

from __future__ import annotations

import json

import pytest

from agentforge.benchmark.hkgai_experiments import (
    HKGAIExperimentRunner,
    validate_experiment_a,
    validate_experiment_b,
)
from agentforge.config.settings import Settings


def test_experiment_a_validator_accepts_minimal_object_only():
    assert validate_experiment_a('{"status": "ok", "value": 1}') == (
        True,
        True,
    )
    assert validate_experiment_a("not json") == (False, False)
    assert validate_experiment_a(
        '{"status": "ok", "value": 1, "extra": true}'
    ) == (True, False)


def test_experiment_b_validator_accepts_one_task_dag():
    plan = {
        "goal_summary": "Create a simple note.",
        "subtasks": [
            {
                "key": "write",
                "description": "Create notes/simple.md containing hello.",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            }
        ],
    }
    assert validate_experiment_b(json.dumps(plan)) == (True, True, 1)


def test_experiment_b_validator_rejects_multi_task_or_invalid_dag():
    two_tasks = {
        "goal_summary": "Two tasks",
        "subtasks": [
            {
                "key": "one",
                "description": "one",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            },
            {
                "key": "two",
                "description": "two",
                "required_capabilities": ["coding"],
                "depends_on": ["one"],
                "kind": "coding",
            },
        ],
    }
    assert validate_experiment_b(json.dumps(two_tasks))[1] is False
    invalid_dep = {
        "goal_summary": "Bad dependency",
        "subtasks": [
            {
                "key": "one",
                "description": "one",
                "required_capabilities": ["coding"],
                "depends_on": ["missing"],
                "kind": "coding",
            }
        ],
    }
    assert validate_experiment_b(json.dumps(invalid_dep))[1] is False


async def test_b_is_gated_until_a_passes(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="hkgai",
        hkgai_api_key="dummy-not-real",
        hkgai_base_url="https://example.invalid/v1",
        hkgai_model="dummy-model",
    )
    runner = HKGAIExperimentRunner(
        settings, allow_live_api=True, output_dir=tmp_path / "experiments"
    )
    with pytest.raises(PermissionError, match="gated"):
        await runner.run("B")


async def test_experiments_require_explicit_authorization(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="hkgai",
        hkgai_api_key="dummy-not-real",
        hkgai_base_url="https://example.invalid/v1",
        hkgai_model="dummy-model",
    )
    runner = HKGAIExperimentRunner(
        settings, allow_live_api=False, output_dir=tmp_path / "experiments"
    )
    with pytest.raises(PermissionError, match="allow-live-api"):
        await runner.run("A")
