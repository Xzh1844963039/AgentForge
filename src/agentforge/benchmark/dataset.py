"""Benchmark dataset loading and selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentforge.benchmark.models import (
    BenchmarkDataset,
    BenchmarkTask,
    TaskCategory,
)


class BenchmarkDatasetError(ValueError):
    pass


def load_dataset(path: str | Path) -> BenchmarkDataset:
    source = Path(path)
    if not source.exists():
        raise BenchmarkDatasetError(f"dataset not found: {source}")
    if source.is_dir():
        candidates = sorted(source.glob("*.jsonl")) + sorted(source.glob("*.json"))
        if not candidates:
            raise BenchmarkDatasetError(f"no .jsonl or .json files in {source}")
        tasks: list[BenchmarkTask] = []
        dataset_id = source.name
        version = "1.0"
        description = ""
        metadata: dict[str, Any] = {}
        for candidate in candidates:
            dataset = load_dataset(candidate)
            tasks.extend(dataset.tasks)
            dataset_id = dataset.dataset_id or dataset_id
            version = dataset.version
            description = description or dataset.description
            metadata.update(dataset.metadata)
        return BenchmarkDataset(
            dataset_id=dataset_id,
            version=version,
            description=description,
            tasks=tasks,
            source_path=str(source),
            metadata=metadata,
        )

    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".jsonl":
        raw_tasks = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw_tasks.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise BenchmarkDatasetError(
                    f"invalid JSONL at {source}:{line_number}: {exc}"
                ) from exc
        payload: dict[str, Any] = {
            "dataset_id": source.parent.name or "dataset",
            "description": f"Tasks loaded from {source.name}",
            "tasks": raw_tasks,
        }
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BenchmarkDatasetError(f"invalid JSON dataset {source}: {exc}") from exc
        if isinstance(payload, list):
            payload = {
                "dataset_id": source.parent.name or "dataset",
                "description": f"Tasks loaded from {source.name}",
                "tasks": payload,
            }
    try:
        dataset = BenchmarkDataset.model_validate(payload)
    except Exception as exc:
        raise BenchmarkDatasetError(f"invalid benchmark dataset {source}: {exc}") from exc
    dataset.source_path = str(source)
    return dataset


def select_tasks(
    dataset: BenchmarkDataset,
    *,
    task_ids: list[str] | None = None,
    categories: list[TaskCategory] | None = None,
    max_tasks: int | None = None,
) -> list[BenchmarkTask]:
    tasks = list(dataset.tasks)
    if task_ids:
        wanted = set(task_ids)
        missing = wanted - {task.task_id for task in tasks}
        if missing:
            raise BenchmarkDatasetError(
                "unknown task ids: " + ", ".join(sorted(missing))
            )
        tasks = [task for task in tasks if task.task_id in wanted]
    if categories:
        selected = set(categories)
        tasks = [task for task in tasks if task.category in selected]
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    if not tasks:
        raise BenchmarkDatasetError("task selection is empty")
    return tasks
