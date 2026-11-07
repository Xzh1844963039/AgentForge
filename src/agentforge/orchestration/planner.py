"""Execution-plan validation and dependency ordering."""

from __future__ import annotations

from agentforge.models.task import ExecutionPlan, SubtaskSpec
from agentforge.orchestration.errors import PlanValidationError


def validate_plan(
    plan: ExecutionPlan, *, max_subtasks: int
) -> list[SubtaskSpec]:
    """Validate the plan DAG and return subtasks in topological order.

    Checks: size limit, unique keys, known dependencies, no self-dependencies,
    and no cycles. Duplicate keys are already rejected by the Pydantic model.
    """
    if not plan.subtasks:
        raise PlanValidationError("execution plan contains no subtasks")
    if len(plan.subtasks) > max_subtasks:
        raise PlanValidationError(
            f"execution plan has {len(plan.subtasks)} subtasks, exceeding the "
            f"configured maximum of {max_subtasks}"
        )

    by_key = {subtask.key: subtask for subtask in plan.subtasks}
    order_index = {subtask.key: index for index, subtask in enumerate(plan.subtasks)}

    for subtask in plan.subtasks:
        if subtask.key in subtask.depends_on:
            raise PlanValidationError(
                f"subtask {subtask.key!r} depends on itself"
            )
        unknown = [dep for dep in subtask.depends_on if dep not in by_key]
        if unknown:
            raise PlanValidationError(
                f"subtask {subtask.key!r} references unknown dependencies {unknown}"
            )

    indegree = {key: len(subtask.depends_on) for key, subtask in by_key.items()}
    dependents: dict[str, list[str]] = {key: [] for key in by_key}
    for subtask in plan.subtasks:
        for dependency in subtask.depends_on:
            dependents[dependency].append(subtask.key)

    ready = sorted(
        [key for key, degree in indegree.items() if degree == 0],
        key=lambda key: order_index[key],
    )
    ordered: list[SubtaskSpec] = []
    while ready:
        key = ready.pop(0)
        ordered.append(by_key[key])
        for dependent in dependents[key]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=lambda item: order_index[item])

    if len(ordered) != len(plan.subtasks):
        cyclic = sorted(key for key, degree in indegree.items() if degree > 0)
        raise PlanValidationError(
            f"execution plan contains a dependency cycle involving {cyclic}"
        )

    return ordered
