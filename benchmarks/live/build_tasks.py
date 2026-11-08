"""Build the live-provider acceptance dataset from trusted dev tasks.

Fixture actions and fault-injection behavior are removed so a real model must
solve the tasks itself. Human scripts remain and are explicitly simulated.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV = ROOT.parent / "dev" / "tasks.jsonl"
LIVE_IDS = [
    "research_basic_facts",
    "plan_research_to_coding",
    "codegen_add_function",
    "execute_compute_sum",
    "execute_failing_test",
    "delegation_single_level",
    "clarification_retention",
    "approval_delete_rejected",
]


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
    print(f"wrote {len(tasks)} live tasks to {output}")


if __name__ == "__main__":
    main()
