"""Build live-provider acceptance datasets from trusted dev tasks.

Fixture actions and fault-injection behavior are removed so a real model must
solve the tasks itself. Human scripts remain and are explicitly simulated.
The memory pair is defined without fixture actions: two learning runs establish
a note convention, then an evaluation task checks whether a retrieved memory
changes the model's action.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV = ROOT.parent / "dev" / "tasks.jsonl"
LIVE_IDS = [
    "file_exact_create",
    "research_basic_facts",
    "plan_research_to_coding",
    "codegen_add_function",
    "execute_compute_sum",
    "execute_failing_test",
    "delegation_single_level",
    "clarification_retention",
    "approval_delete_rejected",
]


def _plan(description: str, capability: str, kind: str) -> list[dict]:
    return [
        {
            "key": "main",
            "description": description,
            "required_capabilities": [capability],
            "depends_on": [],
            "kind": kind,
        }
    ]


def _memory_task(task_id: str, title: str, goal: str, expected_path: str) -> dict:
    return {
        "task_id": task_id,
        "title": title,
        "category": "file_editing",
        "difficulty": "hard",
        "goal": goal,
        "description": "Live memory reuse protocol",
        "initial_workspace": [],
        "available_agents": ["coordinator", "research", "coding", "reviewer"],
        "allowed_tools": ["write_file", "read_file"],
        "plan": _plan(
            "Create and verify the requested convention note.", "coding", "coding"
        ),
        "time_budget_seconds": 300,
        "token_budget": 200_000,
        "tool_budget": 10,
        "max_delegation_depth": 0,
        "max_execution_steps": 20,
        "expected_artifacts": [f"coding/{expected_path}"],
        "success_criteria": "File exists and contains 'verified: yes'.",
        "verification_method": "objective_checks",
        "ground_truth": {},
        "reference_checks": ["The note convention marker is 'verified: yes'."],
        "verification": {
            "files": [
                {
                    "agent": "coding",
                    "path": expected_path,
                    "must_exist": True,
                    "must_not_exist": False,
                    "contains": ["verified: yes"],
                    "not_contains": [],
                    "exact_content": None,
                    "min_bytes": 10,
                }
            ],
            "tool_calls": [],
            "workflow": {"status": "completed"},
            "approvals": [],
            "clarifications": [],
            "task_graph": None,
            "python": [],
            "duplicate_effects": None,
            "manual_review_reason": None,
        },
        "human_script": None,
        "mock_actions": [],
        "behavior": None,
        "tags": ["live", "memory"],
    }


def main() -> None:
    tasks = []
    for line in DEV.read_text(encoding="utf-8").splitlines():
        task = json.loads(line)
        if task["task_id"] not in LIVE_IDS:
            continue
        task["mock_actions"] = []
        task["behavior"] = None
        task["time_budget_seconds"] = 300
        task["token_budget"] = 200_000
        task["tags"] = sorted(set(task.get("tags", []) + ["live"]))
        tasks.append(task)
    output = ROOT / "tasks.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    memory_tasks = [
        _memory_task(
            "live_memory_learning",
            "Learn a note convention",
            (
                "Create notes/convention.md and read it back. The note must contain "
                "the convention marker 'verified: yes' and the text "
                "'convention: strict'. This is a learning run for later memory reuse."
            ),
            "notes/convention.md",
        ),
        _memory_task(
            "live_memory_eval",
            "Reuse the learned note convention",
            (
                "Create notes/live_eval.md using the note convention learned from "
                "earlier work. Include the remembered verification marker. The file "
                "must contain 'verified: yes'."
            ),
            "notes/live_eval.md",
        ),
    ]
    memory_output = ROOT / "memory_tasks.jsonl"
    with memory_output.open("w", encoding="utf-8") as handle:
        for task in memory_tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    print(
        f"wrote {len(tasks)} live tasks to {output} and "
        f"{len(memory_tasks)} memory tasks to {memory_output}"
    )


if __name__ == "__main__":
    main()
