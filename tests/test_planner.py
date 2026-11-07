"""Plan validation and DAG ordering tests."""

from __future__ import annotations

import pytest

from agentforge.models.task import ExecutionPlan, SubtaskSpec
from agentforge.orchestration.errors import PlanValidationError
from agentforge.orchestration.planner import validate_plan


def spec(key: str, deps: list[str] | None = None) -> SubtaskSpec:
    return SubtaskSpec(
        key=key,
        description=f"task {key}",
        required_capabilities=["research"],
        depends_on=deps or [],
    )


def test_topological_order_respects_dependencies():
    plan = ExecutionPlan(
        subtasks=[
            spec("third", ["second"]),
            spec("second", ["first"]),
            spec("first"),
        ]
    )
    ordered = validate_plan(plan, max_subtasks=8)
    assert [item.key for item in ordered] == ["first", "second", "third"]


def test_cycle_is_rejected():
    plan = ExecutionPlan(
        subtasks=[
            spec("a", ["b"]),
            spec("b", ["a"]),
        ]
    )
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(plan, max_subtasks=8)


def test_unknown_dependency_is_rejected():
    plan = ExecutionPlan(subtasks=[spec("a", ["missing"])])
    with pytest.raises(PlanValidationError, match="unknown"):
        validate_plan(plan, max_subtasks=8)


def test_self_dependency_is_rejected():
    plan = ExecutionPlan(subtasks=[spec("a", ["a"])])
    with pytest.raises(PlanValidationError, match="itself"):
        validate_plan(plan, max_subtasks=8)


def test_subtask_limit_enforced():
    plan = ExecutionPlan(subtasks=[spec("a"), spec("b")])
    with pytest.raises(PlanValidationError, match="exceeding"):
        validate_plan(plan, max_subtasks=1)
