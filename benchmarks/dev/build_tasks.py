"""Generate the 34-task Phase 5 development benchmark dataset.

The JSONL is deterministic output of this script. Fixture actions are harness
drivers only; every success is judged by an independent verifier. Run:

    python benchmarks/dev/build_tasks.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentforge.benchmark.models import BenchmarkTask

ROOT = Path(__file__).resolve().parent
DEFAULT_SOLUTION_TEST = """
import sys
sys.path.insert(0, "/workspace")
from solution import add
assert add(2, 3) == 5
assert add(-4, 4) == 0
assert add(0, 0) == 0
print("ADD_OK")
""".strip()


def complete(content: str) -> dict[str, Any]:
    return {"action": "complete", "content": content}


def fail(error: str) -> dict[str, Any]:
    return {"action": "fail", "error": error, "content": error}


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "action": "tool_call",
        "tool_calls": [{"name": name, "arguments": arguments}],
    }


def calls(*items: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "action": "tool_call",
        "tool_calls": [
            {"name": name, "arguments": arguments} for name, arguments in items
        ],
    }


def clarify(question: str, context: str, options: list[str]) -> dict[str, Any]:
    return {
        "action": "clarify",
        "question": question,
        "clarification_context": context,
        "possible_interpretations": options,
    }


def delegate(description: str, capability: str) -> dict[str, Any]:
    return {
        "action": "delegate",
        "delegation": {
            "reason": "specialised delegated work",
            "subtasks": [
                {
                    "description": description,
                    "required_capabilities": [capability],
                    "kind": "delegated",
                }
            ],
        },
    }


def pyexec(filename: str, code: str, *, timeout: int = 30) -> dict[str, Any]:
    return call(
        "python_execute",
        code=code,
        filename=filename,
        timeout_seconds=timeout,
    )


def act(
    kind: str,
    action: dict[str, Any],
    *,
    after: int | None = None,
    clarified: bool | None = None,
    delegated: bool | None = None,
    contains: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "after_tool_results": after,
        "clarification_answered": clarified,
        "delegated_results": delegated,
        "prompt_contains": contains,
        "action": action,
    }


def one_agent_plan(
    description: str, capability: str, kind: str
) -> list[dict[str, Any]]:
    return [
        {
            "key": "main",
            "description": description,
            "required_capabilities": [capability],
            "depends_on": [],
            "kind": kind,
        }
    ]


def file_check(
    path: str,
    *,
    agent: str = "coding",
    contains: list[str] | None = None,
    not_contains: list[str] | None = None,
    exact: str | None = None,
    must_exist: bool = True,
    must_not_exist: bool = False,
    min_bytes: int = 0,
) -> dict[str, Any]:
    return {
        "agent": agent,
        "path": path,
        "must_exist": must_exist,
        "must_not_exist": must_not_exist,
        "contains": contains or [],
        "not_contains": not_contains or [],
        "exact_content": exact,
        "min_bytes": min_bytes,
    }


def python_check(
    source: str, test_code: str, *, agent: str = "coding", description: str = ""
) -> dict[str, Any]:
    return {
        "agent": agent,
        "source_path": source,
        "test_code": test_code,
        "timeout_seconds": 30,
        "description": description,
    }


def task(
    task_id: str,
    title: str,
    category: str,
    difficulty: str,
    goal: str,
    *,
    plan: list[dict[str, Any]],
    verification: dict[str, Any],
    mock_actions: list[dict[str, Any]],
    initial_workspace: list[dict[str, Any]] | None = None,
    allowed_tools: list[str] | None = None,
    human_script: dict[str, Any] | None = None,
    behavior: dict[str, Any] | None = None,
    time_budget: float = 60.0,
    tool_budget: int = 15,
    token_budget: int = 200_000,
    max_delegation_depth: int = 2,
    expected_artifacts: list[str] | None = None,
    success_criteria: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "task_id": task_id,
        "title": title,
        "category": category,
        "difficulty": difficulty,
        "goal": goal,
        "description": success_criteria,
        "initial_workspace": initial_workspace or [],
        "available_agents": [
            "coordinator",
            "research",
            "coding",
            "reviewer",
        ],
        "allowed_tools": allowed_tools,
        "plan": plan,
        "time_budget_seconds": time_budget,
        "token_budget": token_budget,
        "tool_budget": tool_budget,
        "max_delegation_depth": max_delegation_depth,
        "max_execution_steps": 30,
        "expected_artifacts": expected_artifacts or [],
        "success_criteria": success_criteria,
        "verification_method": (
            "manual_review"
            if verification.get("manual_review_reason")
            else "objective_checks"
        ),
        "ground_truth": {},
        "reference_checks": [success_criteria] if success_criteria else [],
        "verification": verification,
        "human_script": human_script,
        "mock_actions": mock_actions,
        "behavior": behavior,
        "tags": tags or [],
    }
    # Validate eagerly so a broken fixture fails at generation time.
    BenchmarkTask.model_validate(payload)
    return payload


TASKS: list[dict[str, Any]] = []

# ---------------------------------------------------------------- research (3)
TASKS.append(
    task(
        "research_basic_facts",
        "Grounded research brief",
        "research",
        "basic",
        (
            "Read research/notes/source.md and write research/notes/brief.md. "
            "Include the 40% latency result and the 3x cost finding, and cite source.md."
        ),
        plan=one_agent_plan(
            "Read the source notes and write a grounded research brief.",
            "research",
            "research",
        ),
        initial_workspace=[
            {
                "agent": "research",
                "path": "notes/source.md",
                "content": (
                    "# Source notes\n\n"
                    "- Alpha cache reduced latency by 40% in the controlled trial.\n"
                    "- Beta cache costs 3x more than Alpha.\n"
                ),
            }
        ],
        allowed_tools=["read_file", "write_file"],
        mock_actions=[
            act("research", call("read_file", path="notes/source.md"), after=0),
            act(
                "research",
                call(
                    "write_file",
                    path="notes/brief.md",
                    content=(
                        "# Research brief\n\n"
                        "Alpha cache reduced latency by 40% in the controlled trial.\n"
                        "Beta cache costs 3x more than Alpha.\n\n"
                        "Source: source.md\n"
                    ),
                ),
                after=1,
            ),
            act("research", complete("Grounded brief written."), after=2),
        ],
        verification={
            "files": [
                file_check(
                    "notes/brief.md",
                    agent="research",
                    contains=["40%", "3x", "source.md"],
                    min_bytes=80,
                )
            ],
            "tool_calls": [
                {"tool_name": "read_file", "status": "success", "min_count": 1},
                {"tool_name": "write_file", "status": "success", "min_count": 1},
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["research/notes/brief.md"],
        success_criteria="Brief cites both seeded facts and the source.",
        tags=["grounded", "sources"],
    )
)

TASKS.append(
    task(
        "research_conflicting_sources",
        "Conflict-aware research brief",
        "research",
        "hard",
        (
            "Read research/notes/source_a.md and source_b.md, then write "
            "research/notes/conflict_brief.md. Explicitly report the conflict, "
            "include both 30% and 45% latency figures, and cite both source files."
        ),
        plan=one_agent_plan(
            "Compare two sources, identify a conflict and write a brief.",
            "research",
            "research",
        ),
        initial_workspace=[
            {
                "agent": "research",
                "path": "notes/source_a.md",
                "content": "# Source A\nAlpha reduced latency by 30%.\n",
            },
            {
                "agent": "research",
                "path": "notes/source_b.md",
                "content": "# Source B\nAlpha reduced latency by 45%.\n",
            },
        ],
        allowed_tools=["read_file", "write_file"],
        mock_actions=[
            act(
                "research",
                calls(
                    ("read_file", {"path": "notes/source_a.md"}),
                    ("read_file", {"path": "notes/source_b.md"}),
                ),
                after=0,
            ),
            act(
                "research",
                call(
                    "write_file",
                    path="notes/conflict_brief.md",
                    content=(
                        "# Conflict brief\n\n"
                        "Source A reports 30% latency reduction.\n"
                        "Source B reports 45% latency reduction.\n"
                        "This is a material conflict that requires verification.\n\n"
                        "Citations: source_a.md, source_b.md\n"
                    ),
                ),
                after=1,
            ),
            act("research", complete("Conflict documented."), after=2),
        ],
        verification={
            "files": [
                file_check(
                    "notes/conflict_brief.md",
                    agent="research",
                    contains=[
                        "30%",
                        "45%",
                        "conflict",
                        "source_a.md",
                        "source_b.md",
                    ],
                    min_bytes=100,
                )
            ],
            "tool_calls": [
                {"tool_name": "read_file", "status": "success", "min_count": 2}
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["research/notes/conflict_brief.md"],
        success_criteria="Both conflicting figures and citations are present.",
        tags=["conflict", "evidence"],
    )
)

TASKS.append(
    task(
        "research_manual_review",
        "Open-ended implications brief",
        "research",
        "hard",
        (
            "Write research/notes/open_brief.md assessing the broader implications "
            "of persistent agent memory. State assumptions and open questions. This "
            "is intentionally open-ended and requires human quality review."
        ),
        plan=one_agent_plan(
            "Write an open-ended implications brief with assumptions.",
            "research",
            "research",
        ),
        allowed_tools=["write_file"],
        mock_actions=[
            act(
                "research",
                call(
                    "write_file",
                    path="notes/open_brief.md",
                    content=(
                        "# Implications of persistent agent memory\n\n"
                        "## Assumptions\n"
                        "Memory is scoped per agent and evidence is audited.\n\n"
                        "## Implications\n"
                        "Persistent memory can improve reuse but may preserve stale "
                        "procedures. Open question: how should conflicting memories "
                        "be reconciled?\n"
                    ),
                ),
                after=0,
            ),
            act("research", complete("Open-ended brief written."), after=1),
        ],
        verification={
            "files": [
                file_check(
                    "notes/open_brief.md",
                    agent="research",
                    contains=["Assumptions", "Implications", "Open question"],
                    min_bytes=120,
                )
            ],
            "workflow": {"status": "completed"},
            "manual_review_reason": (
                "Open-ended analytical quality has no reliable automatic ground "
                "truth; a human must review the brief."
            ),
        },
        expected_artifacts=["research/notes/open_brief.md"],
        success_criteria="Objective structure passes; quality requires human review.",
        tags=["manual-review", "open-ended"],
    )
)

# ----------------------------------------------------------- code generation (3)
TASKS.append(
    task(
        "codegen_add_function",
        "Implement add()",
        "code_generation",
        "basic",
        (
            "Use python_execute to create and run solution.py containing add(a, b) "
            "that returns the numeric sum. Report the real execution result."
        ),
        plan=one_agent_plan(
            "Create and execute solution.py with an add(a,b) function.",
            "coding",
            "coding",
        ),
        allowed_tools=["python_execute", "read_file"],
        mock_actions=[
            act(
                "coding",
                pyexec(
                    "solution.py",
                    (
                        "def add(a, b):\n"
                        "    \"\"\"Return the sum of two numbers.\"\"\"\n"
                        "    return a + b\n"
                    ),
                ),
                after=0,
            ),
            act("coding", complete("solution.py created and executed."), after=1),
        ],
        verification={
            "python": [
                python_check(
                    "scripts/solution.py",
                    (
                        "import sys\nsys.path.insert(0, '/workspace/scripts')\n"
                        "from solution import add\n"
                        "assert add(2, 3) == 5\n"
                        "assert add(-4, 4) == 0\n"
                        "assert add(0, 0) == 0\n"
                        "print('ADD_OK')"
                    ),
                    description="Execute add() with positive, negative and zero inputs.",
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/scripts/solution.py"],
        success_criteria="A real Docker-run test imports and executes add().",
        time_budget=120,
        tool_budget=8,
    )
)

TASKS.append(
    task(
        "codegen_stats_edge_cases",
        "Implement mean/median",
        "code_generation",
        "hard",
        (
            "Use python_execute to create stats.py defining mean(values) and "
            "median(values). Empty input must return None; median must handle odd "
            "and even lengths."
        ),
        plan=one_agent_plan(
            "Create and execute stats.py with mean and median.",
            "coding",
            "coding",
        ),
        allowed_tools=["python_execute", "read_file"],
        mock_actions=[
            act(
                "coding",
                pyexec(
                    "stats.py",
                    (
                        "def mean(values):\n"
                        "    if not values:\n"
                        "        return None\n"
                        "    return sum(values) / len(values)\n\n\n"
                        "def median(values):\n"
                        "    if not values:\n"
                        "        return None\n"
                        "    ordered = sorted(values)\n"
                        "    middle = len(ordered) // 2\n"
                        "    if len(ordered) % 2:\n"
                        "        return ordered[middle]\n"
                        "    return (ordered[middle - 1] + ordered[middle]) / 2\n"
                    ),
                ),
                after=0,
            ),
            act("coding", complete("stats.py created and executed."), after=1),
        ],
        verification={
            "python": [
                python_check(
                    "scripts/stats.py",
                    (
                        "import sys\nsys.path.insert(0, '/workspace/scripts')\n"
                        "from stats import mean, median\n"
                        "assert mean([1,2,3]) == 2\n"
                        "assert mean([]) is None\n"
                        "assert median([3,1,2]) == 2\n"
                        "assert median([1,2,3,4]) == 2.5\n"
                        "assert median([]) is None\n"
                        "print('STATS_OK')"
                    ),
                    description="Test empty input and odd/even median.",
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/scripts/stats.py"],
        success_criteria="Docker tests cover empty, odd and even cases.",
        time_budget=120,
        tool_budget=8,
    )
)

TASKS.append(
    task(
        "codegen_word_count",
        "Implement word counting",
        "code_generation",
        "hard",
        (
            "Use python_execute to create wordcount.py defining count_words(text). "
            "Lowercase the words, strip surrounding punctuation, and return a dict "
            "of counts."
        ),
        plan=one_agent_plan(
            "Create and execute wordcount.py with count_words(text).",
            "coding",
            "coding",
        ),
        allowed_tools=["python_execute", "read_file"],
        mock_actions=[
            act(
                "coding",
                pyexec(
                    "wordcount.py",
                    (
                        "import re\n\n\n"
                        "def count_words(text):\n"
                        "    counts = {}\n"
                        "    for word in text.lower().split():\n"
                        "        cleaned = re.sub(r'^[^a-z0-9]+|[^a-z0-9]+$', '', word)\n"
                        "        if cleaned:\n"
                        "            counts[cleaned] = counts.get(cleaned, 0) + 1\n"
                        "    return counts\n"
                    ),
                ),
                after=0,
            ),
            act("coding", complete("wordcount.py created and executed."), after=1),
        ],
        verification={
            "python": [
                python_check(
                    "scripts/wordcount.py",
                    (
                        "import sys\nsys.path.insert(0, '/workspace/scripts')\n"
                        "from wordcount import count_words\n"
                        "assert count_words('A b, a!') == {'a': 2, 'b': 1}\n"
                        "assert count_words('') == {}\n"
                        "print('WORDCOUNT_OK')"
                    ),
                    description="Test punctuation and empty input.",
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/scripts/wordcount.py"],
        success_criteria="Docker tests verify punctuation and empty input.",
        time_budget=120,
        tool_budget=8,
    )
)

# ----------------------------------------------------------------- debugging (3)
TASKS.append(
    task(
        "debug_off_by_one",
        "Fix an off-by-one bug",
        "debugging",
        "basic",
        (
            "Read notes/buggy.md, diagnose the bug, then use python_execute to "
            "create and run fixed.py with corrected add(a, b) behavior."
        ),
        plan=one_agent_plan(
            "Read the buggy note and create fixed.py.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/buggy.md",
                "content": "```python\ndef add(a, b):\n    return a - b\n```\n",
            }
        ],
        allowed_tools=["read_file", "python_execute"],
        mock_actions=[
            act("coding", call("read_file", path="notes/buggy.md"), after=0),
            act(
                "coding",
                pyexec("fixed.py", "def add(a, b):\n    return a + b\n"),
                after=1,
            ),
            act("coding", complete("Bug fixed in fixed.py."), after=2),
        ],
        verification={
            "python": [
                python_check(
                    "scripts/fixed.py",
                    (
                        "import sys\nsys.path.insert(0, '/workspace/scripts')\n"
                        "from fixed import add\n"
                        "assert add(2,3) == 5\n"
                        "assert add(-2,3) == 1\n"
                        "print('FIXED_OK')"
                    ),
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/scripts/fixed.py"],
        success_criteria="The fixed module passes real execution.",
        time_budget=120,
        tool_budget=8,
    )
)

TASKS.append(
    task(
        "debug_two_bugs",
        "Fix two defensive-programming bugs",
        "debugging",
        "hard",
        (
            "Read notes/calculator.md, fix the divide-by-zero and empty-average "
            "bugs, then use python_execute to create and run calculator_fixed.py."
        ),
        plan=one_agent_plan(
            "Read the buggy calculator note and create calculator_fixed.py.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/calculator.md",
                "content": (
                    "```python\n"
                    "def divide(a, b):\n"
                    "    return a / b\n\n\n"
                    "def average(values):\n"
                    "    return sum(values) / len(values)\n"
                    "```\n"
                ),
            }
        ],
        allowed_tools=["read_file", "python_execute"],
        mock_actions=[
            act("coding", call("read_file", path="notes/calculator.md"), after=0),
            act(
                "coding",
                pyexec(
                    "calculator_fixed.py",
                    (
                        "def divide(a, b):\n"
                        "    if b == 0:\n"
                        "        return None\n"
                        "    return a / b\n\n\n"
                        "def average(values):\n"
                        "    if not values:\n"
                        "        return None\n"
                        "    return sum(values) / len(values)\n"
                    ),
                ),
                after=1,
            ),
            act("coding", complete("Both bugs fixed."), after=2),
        ],
        verification={
            "python": [
                python_check(
                    "scripts/calculator_fixed.py",
                    (
                        "import sys\nsys.path.insert(0, '/workspace/scripts')\n"
                        "from calculator_fixed import divide, average\n"
                        "assert divide(6,3) == 2\n"
                        "assert divide(1,0) is None\n"
                        "assert average([]) is None\n"
                        "assert average([2,4]) == 3\n"
                        "print('CALC_OK')"
                    ),
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/scripts/calculator_fixed.py"],
        success_criteria="Docker tests cover zero division and empty input.",
        time_budget=120,
        tool_budget=8,
    )
)

TASKS.append(
    task(
        "debug_syntax_error",
        "Repair a syntax error",
        "debugging",
        "hard",
        (
            "Read notes/broken.md, repair the syntax error, and use python_execute "
            "to create and run repaired.py with the intended greeting behavior."
        ),
        plan=one_agent_plan(
            "Read the broken note and create repaired.py.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/broken.md",
                "content": (
                    "```python\n"
                    "def greet(name)\n"
                    "    return \"Hello, \" + name + \"!\"\n"
                    "```\n"
                ),
            }
        ],
        allowed_tools=["read_file", "python_execute", "python_syntax_check"],
        mock_actions=[
            act("coding", call("read_file", path="notes/broken.md"), after=0),
            act(
                "coding",
                pyexec(
                    "repaired.py",
                    "def greet(name):\n    return \"Hello, \" + name + \"!\"\n",
                ),
                after=1,
            ),
            act("coding", complete("Syntax repaired."), after=2),
        ],
        verification={
            "python": [
                python_check(
                    "scripts/repaired.py",
                    (
                        "import sys\nsys.path.insert(0, '/workspace/scripts')\n"
                        "from repaired import greet\n"
                        "assert greet('Ada') == 'Hello, Ada!'\n"
                        "print('REPAIRED_OK')"
                    ),
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/scripts/repaired.py"],
        success_criteria="Repaired code imports and executes.",
        time_budget=120,
        tool_budget=8,
    )
)

# -------------------------------------------------------------- file editing (4)
TASKS.append(
    task(
        "file_exact_create",
        "Create an exact Markdown file",
        "file_editing",
        "basic",
        (
            "Create notes/hello.md with exactly: '# Hello', a blank line, "
            "'AgentForge benchmark.', a blank line, and 'trusted content'."
        ),
        plan=one_agent_plan(
            "Create notes/hello.md with the requested exact content.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/hello.md",
                    content="# Hello\n\nAgentForge benchmark.\n\ntrusted content\n",
                ),
                after=0,
            ),
            act("coding", complete("Exact file created."), after=1),
        ],
        verification={
            "files": [
                file_check(
                    "notes/hello.md",
                    exact="# Hello\n\nAgentForge benchmark.\n\ntrusted content\n",
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/hello.md"],
        success_criteria="Byte-for-byte expected Markdown content.",
    )
)

TASKS.append(
    task(
        "file_multi_artifact",
        "Create a two-file report set",
        "file_editing",
        "hard",
        (
            "Create notes/summary.md containing 'Summary: verified' and "
            "notes/details.md containing 'Detail: reproducible'."
        ),
        plan=one_agent_plan(
            "Create the summary and detail Markdown files.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                calls(
                    (
                        "write_file",
                        {
                            "path": "notes/summary.md",
                            "content": "# Summary\n\nSummary: verified\n",
                        },
                    ),
                    (
                        "write_file",
                        {
                            "path": "notes/details.md",
                            "content": "# Details\n\nDetail: reproducible\n",
                        },
                    ),
                ),
                after=0,
            ),
            act("coding", complete("Both artifacts created."), after=1),
        ],
        verification={
            "files": [
                file_check("notes/summary.md", contains=["Summary: verified"]),
                file_check("notes/details.md", contains=["Detail: reproducible"]),
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=[
            "coding/notes/summary.md",
            "coding/notes/details.md",
        ],
        success_criteria="Both files exist with required content.",
    )
)

TASKS.append(
    task(
        "file_revision_copy",
        "Create a revised document",
        "file_editing",
        "hard",
        (
            "Read notes/draft.md and create notes/revision.md containing "
            "'Revision v2' and 'decision: accepted', without the old conclusion."
        ),
        plan=one_agent_plan(
            "Read the draft and write a revised document.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/draft.md",
                "content": "# Draft v1\n\nold conclusion\n",
            }
        ],
        allowed_tools=["read_file", "write_file"],
        mock_actions=[
            act("coding", call("read_file", path="notes/draft.md"), after=0),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/revision.md",
                    content=(
                        "# Revision v2\n\n"
                        "decision: accepted\n\n"
                        "The previous conclusion was superseded after review.\n"
                    ),
                ),
                after=1,
            ),
            act("coding", complete("Revision created."), after=2),
        ],
        verification={
            "files": [
                file_check(
                    "notes/revision.md",
                    contains=["Revision v2", "decision: accepted"],
                    not_contains=["old conclusion"],
                )
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/revision.md"],
        success_criteria="Revision contains the new decision and omits old text.",
    )
)

TASKS.append(
    task(
        "file_invalid_path_recovery",
        "Recover from an invalid path",
        "file_editing",
        "hard",
        (
            "Attempt to write /absolute.md (which the workspace must reject), then "
            "write notes/safe.md containing 'safe path used' and complete."
        ),
        plan=one_agent_plan(
            "Trigger the absolute-path rejection and then write a safe file.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                call("write_file", path="/absolute.md", content="bad"),
                after=0,
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/safe.md",
                    content="safe path used\n",
                ),
                after=1,
            ),
            act("coding", complete("Recovered with a safe path."), after=2),
        ],
        verification={
            "files": [file_check("notes/safe.md", contains=["safe path used"])],
            "tool_calls": [
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "error_contains": "absolute paths are not allowed",
                    "min_count": 1,
                },
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "min_count": 1,
                },
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/safe.md"],
        success_criteria="Invalid path is rejected and a safe write succeeds.",
    )
)

# ------------------------------------------------------------------ planning (3)
TASKS.append(
    task(
        "plan_two_dependent_files",
        "Two dependent planning steps",
        "planning",
        "basic",
        (
            "Create the first planning artifact in notes/step1.md, then create "
            "notes/step2.md after the first artifact is complete."
        ),
        plan=[
            {
                "key": "first",
                "description": "Create the first planning artifact in notes/step1.md",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            },
            {
                "key": "second",
                "description": (
                    "Create the second artifact after the first in notes/step2.md"
                ),
                "required_capabilities": ["coding"],
                "depends_on": ["first"],
                "kind": "coding",
            },
        ],
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/step1.md",
                    content="step one complete\n",
                ),
                after=0,
                contains="[TASK]\nCreate the first planning artifact",
            ),
            act(
                "coding",
                complete("First step complete."),
                after=1,
                contains="[TASK]\nCreate the first planning artifact",
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/step2.md",
                    content="step two complete\n",
                ),
                after=0,
                contains="[TASK]\nCreate the second artifact",
            ),
            act(
                "coding",
                complete("Second step complete."),
                after=1,
                contains="[TASK]\nCreate the second artifact",
            ),
        ],
        verification={
            "files": [
                file_check("notes/step1.md", contains=["step one"]),
                file_check("notes/step2.md", contains=["step two"]),
            ],
            "task_graph": {
                "min_tasks": 4,
                "min_completed": 5,
                "all_completed": True,
            },
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/step1.md", "coding/notes/step2.md"],
        success_criteria="Dependent steps both execute and all governance tasks complete.",
    )
)

TASKS.append(
    task(
        "plan_research_to_coding",
        "Research-to-coding plan",
        "planning",
        "hard",
        (
            "First read research/notes/input.md and write research/notes/research.md, "
            "then write coding/notes/plan.md containing 'Research basis: latency'."
        ),
        plan=[
            {
                "key": "research",
                "description": "Read input.md and write the research note.",
                "required_capabilities": ["research"],
                "depends_on": [],
                "kind": "research",
            },
            {
                "key": "coding",
                "description": (
                    "Write coding/notes/plan.md using the completed research note"
                ),
                "required_capabilities": ["coding"],
                "depends_on": ["research"],
                "kind": "coding",
            },
        ],
        initial_workspace=[
            {
                "agent": "research",
                "path": "notes/input.md",
                "content": "Latency is the primary constraint.\n",
            }
        ],
        allowed_tools=["read_file", "write_file"],
        mock_actions=[
            act("research", call("read_file", path="notes/input.md"), after=0),
            act(
                "research",
                call(
                    "write_file",
                    path="notes/research.md",
                    content="Research finding: latency matters.\n",
                ),
                after=1,
            ),
            act("research", complete("Research note complete."), after=2),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/plan.md",
                    content="Research basis: latency\nPlan: optimise latency.\n",
                ),
                after=0,
                contains="Write coding/notes/plan.md",
            ),
            act(
                "coding",
                complete("Plan complete."),
                after=1,
                contains="Write coding/notes/plan.md",
            ),
        ],
        verification={
            "files": [
                file_check(
                    "notes/research.md",
                    agent="research",
                    contains=["latency"],
                ),
                file_check("notes/plan.md", contains=["Research basis: latency"]),
            ],
            "task_graph": {"min_tasks": 4, "all_completed": True},
            "workflow": {"status": "completed"},
        },
        expected_artifacts=[
            "research/notes/research.md",
            "coding/notes/plan.md",
        ],
        success_criteria="Research output is produced before coding and both persist.",
    )
)

TASKS.append(
    task(
        "plan_dependency_failure",
        "Dependency failure propagation",
        "planning",
        "hard",
        (
            "Plan two dependent coding steps. The first must fail honestly; the "
            "second depends on it and must not execute."
        ),
        plan=[
            {
                "key": "first",
                "description": "This first planning step cannot be completed",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "kind": "coding",
            },
            {
                "key": "second",
                "description": "This second step depends on the failed first step",
                "required_capabilities": ["coding"],
                "depends_on": ["first"],
                "kind": "coding",
            },
        ],
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                fail("the first step cannot be completed with available inputs"),
                after=0,
                contains="first planning step",
            )
        ],
        verification={
            "workflow": {
                "status": "failed",
                "error_contains": "first step cannot be completed",
            },
            "task_graph": {"min_tasks": 4, "min_failed": 5},
        },
        expected_artifacts=[],
        success_criteria="The workflow fails and reports dependency propagation.",
    )
)

# ----------------------------------------------------------------- delegation (3)
TASKS.append(
    task(
        "delegation_single_level",
        "Single-level delegation",
        "delegation",
        "basic",
        (
            "Delegate a research note to Research, then after it completes write "
            "coding/notes/parent.md containing 'parent used delegated result'."
        ),
        plan=one_agent_plan(
            "Coordinate a delegated research note and write the parent summary.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                delegate("Write delegated research note", "research"),
                after=0,
            ),
            act(
                "delegated",
                call(
                    "write_file",
                    path="notes/delegated.md",
                    content="delegated research complete\n",
                ),
                after=0,
                contains="delegated research note",
            ),
            act(
                "delegated",
                complete("Delegated research complete."),
                after=1,
                contains="delegated research note",
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/parent.md",
                    content="parent used delegated result\n",
                ),
                after=0,
                delegated=True,
            ),
            act(
                "coding",
                complete("Parent complete."),
                after=1,
                delegated=True,
            ),
        ],
        verification={
            "files": [
                file_check(
                    "notes/delegated.md",
                    agent="research",
                    contains=["delegated"],
                ),
                file_check("notes/parent.md", contains=["parent used delegated"]),
            ],
            "task_graph": {
                "min_delegated_tasks": 1,
                "min_max_depth": 2,
                "all_completed": True,
            },
            "workflow": {"status": "completed"},
        },
        expected_artifacts=[
            "research/notes/delegated.md",
            "coding/notes/parent.md",
        ],
        success_criteria="A real child task completes before the parent continues.",
    )
)

TASKS.append(
    task(
        "delegation_two_levels",
        "Two-level agent-to-agent delegation",
        "delegation",
        "hard",
        (
            "Coding delegates a level-one research note; Research then delegates a "
            "level-two review note; each parent writes its final artifact."
        ),
        plan=one_agent_plan(
            "Coordinate a two-level delegated chain and write the top artifact.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        max_delegation_depth=3,
        mock_actions=[
            act(
                "coding",
                delegate("Write level one research note", "research"),
                after=0,
            ),
            act(
                "delegated",
                delegate("Write level two review note", "review"),
                after=0,
                contains="level one research",
            ),
            act(
                "delegated",
                call(
                    "write_file",
                    path="notes/review.md",
                    content="level two review complete\n",
                ),
                after=0,
                contains="level two review",
            ),
            act(
                "delegated",
                complete("Level two review complete."),
                after=1,
                contains="level two review",
            ),
            act(
                "delegated",
                call(
                    "write_file",
                    path="notes/level1.md",
                    content="level one complete after review\n",
                ),
                after=0,
                delegated=True,
                contains="level one research",
            ),
            act(
                "delegated",
                complete("Level one research complete."),
                after=1,
                delegated=True,
                contains="level one research",
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/level0.md",
                    content="level zero complete after two delegations\n",
                ),
                after=0,
                delegated=True,
            ),
            act("coding", complete("Top-level complete."), after=1, delegated=True),
        ],
        verification={
            "files": [
                file_check(
                    "notes/level1.md",
                    agent="research",
                    contains=["level one"],
                ),
                file_check("notes/level0.md", contains=["level zero"]),
            ],
            "task_graph": {
                "min_delegated_tasks": 2,
                "min_max_depth": 3,
                "all_completed": True,
            },
            "workflow": {"status": "completed"},
        },
        expected_artifacts=[
            "research/notes/level1.md",
            "coding/notes/level0.md",
        ],
        success_criteria="Two nested A2A delegations complete at distinct depths.",
    )
)

TASKS.append(
    task(
        "delegation_depth_limit",
        "Delegation depth limit failure",
        "delegation",
        "hard",
        (
            "Attempt a delegation beyond the configured maximum depth. The "
            "platform must reject it instead of looping."
        ),
        plan=one_agent_plan(
            "Attempt a delegation that exceeds the configured depth limit.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        max_delegation_depth=1,
        mock_actions=[
            act(
                "coding",
                delegate("This delegation should exceed depth", "research"),
                after=0,
            )
        ],
        verification={
            "workflow": {
                "status": "failed",
                "error_contains": "maximum delegation depth",
            }
        },
        expected_artifacts=[],
        success_criteria="The depth limit is enforced and the workflow fails safely.",
    )
)

# ----------------------------------------------------------- code execution (4)
TASKS.append(
    task(
        "execute_compute_sum",
        "Execute Python and report stdout",
        "code_execution",
        "basic",
        (
            "Execute Python that prints 'SUM=15' in the Docker sandbox, then write "
            "notes/execution.md containing the observed output and success status."
        ),
        plan=one_agent_plan(
            "Execute the compute snippet and record the real output.",
            "coding",
            "coding",
        ),
        allowed_tools=["python_execute", "write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                call(
                    "python_execute",
                    code="print('SUM=15')\n",
                    filename="sum_task.py",
                    timeout_seconds=20,
                ),
                after=0,
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/execution.md",
                    content="Observed stdout: SUM=15\nExit code: 0\n",
                ),
                after=1,
            ),
            act("coding", complete("Execution recorded."), after=2),
        ],
        verification={
            "files": [
                file_check(
                    "notes/execution.md",
                    contains=["SUM=15", "Exit code: 0"],
                )
            ],
            "tool_calls": [
                {
                    "tool_name": "python_execute",
                    "status": "success",
                    "output_contains": "SUM=15",
                    "min_count": 1,
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/execution.md"],
        success_criteria="Real Docker stdout is quoted in the artifact.",
        time_budget=120,
    )
)

TASKS.append(
    task(
        "execute_failing_test",
        "Interpret a failing test",
        "code_execution",
        "hard",
        (
            "Run a Python unittest that intentionally fails. Record that it failed, "
            "write notes/test_failure.md, and complete without claiming success."
        ),
        plan=one_agent_plan(
            "Run a failing test and report the real failure.",
            "coding",
            "coding",
        ),
        allowed_tools=["python_test", "write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                call(
                    "python_test",
                    code=(
                        "import unittest\n\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_failure(self):\n"
                        "        self.assertEqual(1, 2)\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                    filename="test_expected_failure.py",
                    timeout_seconds=20,
                ),
                after=0,
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/test_failure.md",
                    content=(
                        "# Test failure observed\n\n"
                        "The test exited with a non-zero code and failed as expected.\n"
                    ),
                ),
                after=1,
            ),
            act("coding", complete("Failure reported honestly."), after=2),
        ],
        verification={
            "files": [
                file_check("notes/test_failure.md", contains=["failed"])
            ],
            "tool_calls": [
                {
                    "tool_name": "python_test",
                    "status": "error",
                    "error_contains": "exited with code",
                    "min_count": 1,
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/test_failure.md"],
        success_criteria="A real non-zero test exit is recorded, not hidden.",
        time_budget=120,
    )
)

TASKS.append(
    task(
        "execute_timeout",
        "Handle a sandboxed execution timeout",
        "code_execution",
        "hard",
        (
            "Execute an infinite-loop snippet with a 2-second timeout, then record "
            "the timeout in notes/timeout.md and complete."
        ),
        plan=one_agent_plan(
            "Trigger a sandbox timeout and report it.",
            "coding",
            "coding",
        ),
        allowed_tools=["python_execute", "write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                call(
                    "python_execute",
                    code="while True:\n    pass\n",
                    filename="timeout_task.py",
                    timeout_seconds=2,
                ),
                after=0,
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/timeout.md",
                    content="# Timeout\n\nThe sandboxed execution timed out.\n",
                ),
                after=1,
            ),
            act("coding", complete("Timeout recorded."), after=2),
        ],
        verification={
            "files": [file_check("notes/timeout.md", contains=["timed out"])],
            "tool_calls": [
                {
                    "tool_name": "python_execute",
                    "status": "error",
                    "error_contains": "timed out",
                    "min_count": 1,
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/timeout.md"],
        success_criteria="The timeout is real and persisted.",
        time_budget=120,
    )
)

TASKS.append(
    task(
        "execute_invalid_arguments",
        "Recover from invalid tool arguments",
        "code_execution",
        "hard",
        (
            "Attempt read_file with a non-string path, observe schema validation, "
            "then write notes/schema_error.md describing the rejection."
        ),
        plan=one_agent_plan(
            "Trigger schema validation and then write a safe artifact.",
            "coding",
            "coding",
        ),
        allowed_tools=["read_file", "write_file"],
        mock_actions=[
            act("coding", call("read_file", path=123), after=0),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/schema_error.md",
                    content="Schema validation rejected a non-string path.\n",
                ),
                after=1,
            ),
            act("coding", complete("Schema failure recorded."), after=2),
        ],
        verification={
            "files": [
                file_check("notes/schema_error.md", contains=["Schema validation"])
            ],
            "tool_calls": [
                {
                    "tool_name": "read_file",
                    "status": "error",
                    "metadata_reason": "schema_validation",
                    "min_count": 1,
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/schema_error.md"],
        success_criteria="Invalid arguments never reach the tool and are audited.",
    )
)

# ------------------------------------------------------------------- approval (3)
TASKS.append(
    task(
        "approval_delete_rejected",
        "Reject a destructive deletion",
        "approval",
        "basic",
        (
            "Request deletion of notes/delete_me.md. The simulated human will reject "
            "it; do not claim the file was deleted."
        ),
        plan=one_agent_plan(
            "Request deletion and respect the human rejection.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/delete_me.md",
                "content": "preserve this file\n",
            }
        ],
        allowed_tools=["delete_file", "write_file", "read_file"],
        human_script={"approval_decision": "reject", "approval_comment": "keep it"},
        mock_actions=[
            act(
                "coding",
                call("delete_file", path="notes/delete_me.md"),
                after=0,
            ),
            act(
                "coding",
                complete("The deletion was rejected; file preserved."),
                after=1,
            ),
        ],
        verification={
            "files": [
                file_check("notes/delete_me.md", contains=["preserve this file"])
            ],
            "tool_calls": [
                {
                    "tool_name": "delete_file",
                    "status": "denied",
                    "approval_status": "rejected",
                    "min_count": 1,
                }
            ],
            "approvals": [
                {
                    "tool_name": "delete_file",
                    "status": "rejected",
                    "min_count": 1,
                }
            ],
            "workflow": {
                "status": "completed",
                "min_pauses": 1,
                "min_resumes": 1,
            },
        },
        expected_artifacts=["coding/notes/delete_me.md"],
        success_criteria="Deletion is denied and the file remains unchanged.",
    )
)

TASKS.append(
    task(
        "approval_overwrite_approved",
        "Approve an explicit overwrite",
        "approval",
        "hard",
        (
            "Request overwriting notes/overwrite.md with 'new approved content'. "
            "The simulated human will approve it; execute exactly once."
        ),
        plan=one_agent_plan(
            "Request overwrite approval and execute after approval.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/overwrite.md",
                "content": "old content\n",
            }
        ],
        allowed_tools=["write_file", "read_file"],
        human_script={"approval_decision": "approve", "approval_comment": "approved"},
        mock_actions=[
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/overwrite.md",
                    content="new approved content\n",
                    overwrite=True,
                ),
                after=0,
            ),
            act("coding", complete("Overwrite approved and executed."), after=1),
        ],
        verification={
            "files": [
                file_check(
                    "notes/overwrite.md",
                    exact="new approved content\n",
                )
            ],
            "approvals": [
                {
                    "tool_name": "write_file",
                    "status": "approved",
                    "min_count": 1,
                    "consumed": True,
                }
            ],
            "duplicate_effects": {"max_per_signature": 1, "tools": ["write_file"]},
            "workflow": {
                "status": "completed",
                "min_pauses": 1,
                "min_resumes": 1,
            },
        },
        expected_artifacts=["coding/notes/overwrite.md"],
        success_criteria="Approval is consumed once and content changes exactly once.",
    )
)

TASKS.append(
    task(
        "approval_update_rejected",
        "Reject an in-place update",
        "approval",
        "hard",
        (
            "Request an in-place update of notes/stability.md. The simulated human "
            "will reject it; the original content must remain."
        ),
        plan=one_agent_plan(
            "Request an update and respect the rejection.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/stability.md",
                "content": "original stable content\n",
            }
        ],
        allowed_tools=["update_file", "write_file", "read_file"],
        human_script={"approval_decision": "reject", "approval_comment": "no change"},
        mock_actions=[
            act(
                "coding",
                call(
                    "update_file",
                    path="notes/stability.md",
                    content="changed content\n",
                    mode="replace",
                ),
                after=0,
            ),
            act(
                "coding",
                complete("Update rejected; original preserved."),
                after=1,
            ),
        ],
        verification={
            "files": [
                file_check(
                    "notes/stability.md",
                    exact="original stable content\n",
                )
            ],
            "tool_calls": [
                {
                    "tool_name": "update_file",
                    "status": "denied",
                    "approval_status": "rejected",
                    "min_count": 1,
                }
            ],
            "approvals": [
                {
                    "tool_name": "update_file",
                    "status": "rejected",
                    "min_count": 1,
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/stability.md"],
        success_criteria="No in-place change occurs without approval.",
    )
)

# --------------------------------------------------------------- clarification (3)
TASKS.append(
    task(
        "clarification_retention",
        "Answer a retention question",
        "clarification",
        "basic",
        (
            "Ask whether retention is 30 or 90 days; the simulated human answers "
            "'90 days'. Write notes/retention.md with that answer."
        ),
        plan=one_agent_plan(
            "Ask the retention question and write the answer.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        human_script={"clarification_answer": "90 days"},
        mock_actions=[
            act(
                "coding",
                clarify(
                    "Should retention be 30 or 90 days?",
                    "The goal omits the retention period.",
                    ["30 days", "90 days"],
                ),
                after=0,
                clarified=False,
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/retention.md",
                    content="Retention policy: 90 days.\n",
                ),
                after=0,
                clarified=True,
            ),
            act(
                "coding",
                complete("Retention answer recorded."),
                after=1,
                clarified=True,
            ),
        ],
        verification={
            "files": [
                file_check("notes/retention.md", contains=["90 days"])
            ],
            "clarifications": [
                {
                    "status": "answered",
                    "min_count": 1,
                    "answer_contains": "90 days",
                }
            ],
            "workflow": {
                "status": "completed",
                "min_pauses": 1,
                "min_resumes": 1,
            },
        },
        expected_artifacts=["coding/notes/retention.md"],
        success_criteria="The persisted human answer drives the artifact.",
    )
)

TASKS.append(
    task(
        "clarification_output_format",
        "Resolve an ambiguous output format",
        "clarification",
        "hard",
        (
            "Ask whether the summary should be Markdown or JSON; the simulated "
            "human answers 'Markdown'. Write notes/format.md with that decision."
        ),
        plan=one_agent_plan(
            "Resolve the ambiguous format and write the decision.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        human_script={"clarification_answer": "Markdown"},
        mock_actions=[
            act(
                "coding",
                clarify(
                    "Should the summary use Markdown or JSON?",
                    "The requested format is ambiguous.",
                    ["Markdown", "JSON"],
                ),
                after=0,
                clarified=False,
            ),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/format.md",
                    content="Output format decision: Markdown.\n",
                ),
                after=0,
                clarified=True,
            ),
            act(
                "coding",
                complete("Format decision recorded."),
                after=1,
                clarified=True,
            ),
        ],
        verification={
            "files": [
                file_check("notes/format.md", contains=["Markdown"])
            ],
            "clarifications": [
                {
                    "status": "answered",
                    "min_count": 1,
                    "answer_contains": "Markdown",
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/format.md"],
        success_criteria="Ambiguity is resolved by the human answer.",
    )
)

TASKS.append(
    task(
        "clarification_unanswered",
        "Leave an unanswered clarification paused",
        "clarification",
        "hard",
        (
            "Ask a critical question and stop. The simulated human intentionally "
            "does not answer; the workflow must remain paused with an open question."
        ),
        plan=one_agent_plan(
            "Ask a blocking question and wait for a human.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        human_script={"clarification_answer": None},
        mock_actions=[
            act(
                "coding",
                clarify(
                    "Which compliance regime applies?",
                    "No jurisdiction or policy is specified.",
                    ["EU GDPR", "US SOC2"],
                ),
                after=0,
                clarified=False,
            )
        ],
        verification={
            "clarifications": [{"status": "open", "min_count": 1}],
            "workflow": {"status": "paused", "min_pauses": 1},
        },
        expected_artifacts=[],
        success_criteria="The workflow stays safely paused; no answer is invented.",
    )
)

# -------------------------------------------------------------- failure recovery (5)
TASKS.append(
    task(
        "recovery_pause_resume",
        "Pause and resume without duplicating effects",
        "failure_recovery",
        "basic",
        (
            "A human pause interrupts the coding task before its file write. After "
            "resume, write notes/resumed.md exactly once."
        ),
        plan=one_agent_plan(
            "Write the resumed artifact after a human pause.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        behavior={"pause_on_kind": "coding"},
        mock_actions=[
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/resumed.md",
                    content="resumed once\n",
                ),
                after=0,
            ),
            act("coding", complete("Resumed and completed."), after=1),
        ],
        verification={
            "files": [file_check("notes/resumed.md", exact="resumed once\n")],
            "duplicate_effects": {"max_per_signature": 1, "tools": ["write_file"]},
            "workflow": {
                "status": "completed",
                "min_pauses": 1,
                "min_resumes": 1,
            },
        },
        expected_artifacts=["coding/notes/resumed.md"],
        success_criteria="Pause/resume completes with a single write.",
    )
)

TASKS.append(
    task(
        "recovery_tool_error_retry",
        "Recover from a tool error",
        "failure_recovery",
        "hard",
        (
            "Read a missing file, observe the error, then read an existing file and "
            "write notes/recovered.md with 'recovered after tool error'."
        ),
        plan=one_agent_plan(
            "Recover from a missing-file error and complete.",
            "coding",
            "coding",
        ),
        initial_workspace=[
            {
                "agent": "coding",
                "path": "notes/available.md",
                "content": "available source\n",
            }
        ],
        allowed_tools=["read_file", "write_file"],
        mock_actions=[
            act("coding", call("read_file", path="notes/missing.md"), after=0),
            act("coding", call("read_file", path="notes/available.md"), after=1),
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/recovered.md",
                    content="recovered after tool error\n",
                ),
                after=2,
            ),
            act("coding", complete("Recovered from tool error."), after=3),
        ],
        verification={
            "files": [
                file_check(
                    "notes/recovered.md",
                    contains=["recovered after tool error"],
                )
            ],
            "tool_calls": [
                {
                    "tool_name": "read_file",
                    "status": "error",
                    "min_count": 1,
                },
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "min_count": 1,
                },
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["coding/notes/recovered.md"],
        success_criteria="A failed read is followed by a successful recovery.",
    )
)

TASKS.append(
    task(
        "recovery_unachievable",
        "Fail honestly on an impossible request",
        "failure_recovery",
        "hard",
        (
            "Attempt a request that cannot be completed with the available tools and "
            "fail explicitly instead of fabricating a result."
        ),
        plan=one_agent_plan(
            "Fail honestly when the request cannot be completed.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        mock_actions=[
            act(
                "coding",
                fail("the request cannot be completed with the available tools"),
                after=0,
            )
        ],
        verification={
            "workflow": {
                "status": "failed",
                "error_contains": "cannot be completed",
            }
        },
        expected_artifacts=[],
        success_criteria="The workflow fails with an explicit reason.",
    )
)

TASKS.append(
    task(
        "recovery_cancel",
        "Cancel before a side effect",
        "failure_recovery",
        "hard",
        (
            "The simulated operator cancels the workflow before the coding task can "
            "write notes/cancelled.md. No artifact may be created."
        ),
        plan=one_agent_plan(
            "Attempt a file write that must be cancelled.",
            "coding",
            "coding",
        ),
        allowed_tools=["write_file", "read_file"],
        behavior={"cancel_on_kind": "coding"},
        mock_actions=[
            act(
                "coding",
                call(
                    "write_file",
                    path="notes/cancelled.md",
                    content="must not exist\n",
                ),
                after=0,
            )
        ],
        verification={
            "files": [
                file_check(
                    "notes/cancelled.md",
                    must_exist=False,
                    must_not_exist=True,
                )
            ],
            "workflow": {
                "status": "cancelled",
                "min_cancellations": 1,
                "error_contains": "cancel",
            },
        },
        expected_artifacts=[],
        success_criteria="Cancellation is terminal and prevents the side effect.",
    )
)

TASKS.append(
    task(
        "recovery_permission_denied",
        "Recover from a permission denial",
        "failure_recovery",
        "hard",
        (
            "As the Research agent, attempt delete_file on notes/protected.md. The "
            "platform must deny it; then write notes/permission.md explaining the "
            "denial and complete."
        ),
        plan=one_agent_plan(
            "Trigger a role permission denial and document it.",
            "research",
            "research",
        ),
        initial_workspace=[
            {
                "agent": "research",
                "path": "notes/protected.md",
                "content": "protected\n",
            }
        ],
        allowed_tools=["delete_file", "write_file", "read_file"],
        mock_actions=[
            act(
                "research",
                call("delete_file", path="notes/protected.md"),
                after=0,
            ),
            act(
                "research",
                call(
                    "write_file",
                    path="notes/permission.md",
                    content="delete_file was denied by the permission policy.\n",
                ),
                after=1,
            ),
            act("research", complete("Permission denial documented."), after=2),
        ],
        verification={
            "files": [
                file_check(
                    "notes/protected.md",
                    agent="research",
                    contains=["protected"],
                ),
                file_check(
                    "notes/permission.md",
                    contains=["permission policy"],
                    agent="research",
                ),
            ],
            "tool_calls": [
                {
                    "tool_name": "delete_file",
                    "status": "denied",
                    "permission_decision": "denied",
                    "min_count": 1,
                }
            ],
            "workflow": {"status": "completed"},
        },
        expected_artifacts=["research/notes/permission.md"],
        success_criteria="The role boundary is enforced before approval or execution.",
    )
)


def main() -> None:
    output = ROOT / "tasks.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for item in TASKS:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    categories: dict[str, int] = {}
    for item in TASKS:
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    print(f"wrote {len(TASKS)} tasks to {output}")
    for category, count in sorted(categories.items()):
        print(f"  {category}: {count}")


if __name__ == "__main__":
    main()
