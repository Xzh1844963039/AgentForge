# Benchmark Design (Phase 5)

The benchmark harness measures AgentForge on new tasks without changing the
Phase 1–4 runtime contract. It is deliberately separate from the production CLI
flow: tasks, verifiers, metrics, exports and ablation live under
`src/agentforge/benchmark/` and `benchmarks/`.

## 1. Goals and non-goals

Goals:

- run one task or a whole dataset without copying Demo-specific behavior;
- verify success from external artifacts/audit instead of trusting the LLM or
  `workflow.status`;
- isolate each task's database, workspaces and memory;
- collect comparable time, token, tool, delegation, HITL and recovery metrics;
- export reproducible JSON, JSONL, CSV and Markdown records;
- resume an interrupted batch;
- support single-agent, multi-agent and memory ablations with identical tasks;
- expose a real-provider acceptance entry point without silently spending money.

Non-goals:

- proving real-LLM capability with the deterministic fixture provider;
- hiding Docker/API prerequisites or converting skipped checks into passes;
- adding a web dashboard or distributed benchmark scheduler.

## 2. Package layout

```text
src/agentforge/benchmark/
├── models.py        typed task, verifier, run, aggregate and ablation contracts
├── dataset.py       JSON/JSONL loading and task selection
├── execution.py     isolated runtime creation; single-agent baseline
├── provider.py      fixture policy + token-budget wrapper
├── verification.py  file, Python, tool-audit, workflow, HITL, graph checks
├── metrics.py       per-run collection and aggregation
├── failures.py      deterministic failure classification
├── runner.py        resumable batch runner and exports
├── ablation.py      A/B/C comparison with separate learning/eval tasks
├── live.py          explicit real-provider acceptance
└── report.py        Markdown/JSONL/CSV writers
```

Entry points:

- Python: `BenchmarkRunner`, `BenchmarkVerifier`, `AblationRunner`,
  `LiveAcceptanceRunner`;
- CLI: `agentforge benchmark list|run|compare|report|failures|live`.

## 3. Task schema

`BenchmarkTask` includes:

| Field | Purpose |
|---|---|
| `task_id`, `title` | stable identity |
| `category`, `difficulty` | reporting and stratification |
| `goal` | exact user goal sent to the runtime |
| `description`, `success_criteria` | human-readable acceptance definition |
| `initial_workspace` | isolated files seeded per agent/role |
| `available_agents` | declarative agent fleet |
| `allowed_tools` | per-task tool allow-list enforced by `ToolExecutor` |
| `plan` | optional plan fixture used only by the fixture provider |
| `time_budget_seconds` | monotonic solver budget |
| `token_budget` | provider-level budget |
| `tool_budget` | allowed dispatcher invocations |
| `max_delegation_depth`, `max_execution_steps` | runtime limits |
| `expected_artifacts` | paths recorded with each run |
| `verification` | objective file/Python/audit/state checks |
| `verification_method` | `objective_checks` or `manual_review` |
| `ground_truth`, `reference_checks` | explicit reference facts/acceptance notes |
| `human_script` | simulated approval/clarification answers |
| `mock_actions` | deterministic fixture actions (never used for live models) |
| `behavior` | pause/cancel injection for recovery tasks |

`allowed_tools` is enforced in `ToolExecutor` before role permissions; a call
outside the task list returns `denied` with `reason=tool_not_allowed` and is
audited. The task list is not a prompt suggestion.

## 4. Internal development dataset

`benchmarks/dev/tasks.jsonl` contains **34 tasks** generated reproducibly by
`benchmarks/dev/build_tasks.py`.

| Category | Tasks | Basic | Hard |
|---|---:|---:|---:|
| Research / information retrieval | 3 | 1 | 2 |
| Python code generation | 3 | 1 | 2 |
| Python debugging | 3 | 1 | 2 |
| File creation / editing | 4 | 1 | 3 |
| Multi-step planning | 3 | 1 | 2 |
| Multi-agent delegation | 3 | 1 | 2 |
| Code execution / testing | 4 | 1 | 3 |
| Human approval | 3 | 1 | 2 |
| Clarification | 3 | 1 | 2 |
| Failure recovery / safety | 5 | 1 | 4 |

Coverage includes normal success, invalid arguments, unsafe paths, permission
denial, missing files, schema rejection, tool timeout, dependency failure,
unachievable requests, cancellation, approval rejection/acceptance, unanswered
clarification, delegation depth limits and two-level delegation.

The tasks are not the Phase 1–4 primes/note demos. They use generic goals,
provided source notes, real file/Python artifacts, state transitions and audit
facts.

## 5. Verification

Success is never inferred from an agent saying it succeeded, or from
`workflow.status == completed` alone. `BenchmarkVerifier` returns:

- `PASSED` — all objective checks passed;
- `FAILED` — at least one check failed;
- `NEEDS_REVIEW` — objective checks passed but a human-quality judgment is
  explicitly required;
- `SKIPPED` — the objective check could not run (for example Docker missing).

Check types:

- **File checks**: existence/absence, exact content, required/forbidden text,
  minimum bytes, workspace-scoped path.
- **Python checks**: the verifier writes an independent test into the agent's
  workspace and executes it in the same Docker sandbox; missing Docker is a
  skip, never a pass.
- **Tool audit checks**: tool name, status, permission decision, approval status,
  schema-error reason, error/output substrings and count bounds.
- **Workflow checks**: expected status, final/error text and lifecycle counts.
- **Approval / clarification checks**: status, count, consumption and answer.
- **Task-graph checks**: task count, completion/failure, delegated tasks, depth
  and reassignments.
- **Duplicate-effect checks**: repeated SUCCESS records for the same tool +
  argument digest.
- **Manual review**: recorded separately; it does not inflate completion rate.

No verifier reads MockProvider self-reports as ground truth. Research tasks are
checked against seeded source facts and citations; the one intentionally
open-ended task is `NEEDS_REVIEW`.

## 6. Execution and isolation

Each task run gets its own directory:

```text
batch_<time>/
├── batch.json
├── runs.jsonl / runs.json / runs.csv
├── failures.json
├── report.md
└── runs/<run_id>/
    ├── agentforge.db
    ├── memory_evolution.db
    └── workspaces/<agent>/
```

The runner creates a fresh `AgentForgeRuntime` per task with:
database, event log, workspace root, artifacts, memory (unless shared by an
ablation) and budgets isolated. Initial files are seeded only inside the mapped
agent workspace.

`available_agents` and `allowed_tools` are declarative task constraints; the
default multi-agent fleet is the Phase 1 four-role fleet, while the single-agent
baseline replaces the registry with one all-capability Coordinator (same
dispatcher, permissions, sandbox, persistence and governance).

## 7. Budgets and interruptions

- **Time**: `asyncio.wait_for` around solver calls only; timeouts trigger
  cancellation and a `timeout` record, not a silent success.
- **Tokens**: `BudgetedProvider` stops further model calls after the budget; the
  overshoot is recorded.
- **Tools**: the per-task allow-list plus `max_tool_calls_per_task`; exceeding the
  total tool budget marks the run failed.
- **Delegation**: task-specific `max_delegation_depth`.

Verification runs after solver timing stops, so Docker test execution does not
inflate the solver's E2E metric.

## 8. Batch persistence and resume

`batch.json` stores dataset, config, Git commit, task IDs and status. Each run is
appended to `runs.jsonl`; `(task_id, repeat_index)` pairs already present are
skipped when `--resume` is used with the same `--batch-dir`. A process crash
mid-task leaves no run record, so that task is retried; completed runs are not
re-executed.

Exports:

- JSONL/JSON for tools and regression analysis;
- CSV for spreadsheets;
- Markdown for the formal report.

## 9. Fixture vs live boundary

`FixturePolicyProvider` is a deterministic harness driver. It is labeled
`benchmark-fixture`; every report with it carries a prominent
**MOCK FIXTURE RESULT** warning. Fixture scores validate verifiers, tools, state
transitions, budgets and metrics — not model capability.

`benchmarks/live/tasks.jsonl` contains 9 tasks with fixture actions and fault
behavior removed. `memory_tasks.jsonl` adds a separate learning/evaluation pair. `LiveAcceptanceRunner` invokes DeepSeek/HKGAI only when both
an API key exists and `--allow-live-api` is explicitly supplied. Otherwise it
runs a three-task fixture harness validation and marks all live checks
`NOT_VERIFIED`.

## 10. Adding tasks

1. Add a task through `benchmarks/dev/build_tasks.py` or a new JSONL entry.
2. Define objective checks; use manual review only when no reliable ground truth
   exists.
3. Keep fixture actions separate from success criteria.
4. Run `agentforge benchmark run --task-id <id>`.
5. Add a regression test if the task exposes a new failure mode.
6. Regenerate JSONL with `python benchmarks/dev/build_tasks.py`.

## 11. Known limitations

- Fixture actions are deterministic and do not measure LLM generalization.
- The dev set is small; per-category rates are descriptive, not statistically
  powered.
- Live provider behavior is NOT VERIFIED in this environment.
- Sequential batch execution is used for comparable resource measurements;
  bounded DAG concurrency is future work.
- Docker startup dominates Python-task latency; the harness reports it rather
  than hiding it.
