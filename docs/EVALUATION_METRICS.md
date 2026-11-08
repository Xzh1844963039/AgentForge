# Evaluation Metrics (Phase 5)

This document defines every benchmark metric precisely. The authoritative
implementation is `src/agentforge/benchmark/metrics.py`; raw records live in
`runs.jsonl` / `runs.json` and include audit timestamps.

## 1. Outcome statuses

| Status | Definition |
|---|---|
| `passed` | All objective verifier checks passed. This is the only status counted as **goal success**. |
| `failed` | At least one objective check failed, or a budget was exceeded. |
| `needs_review` | Objective structure passed, but a declared human-quality judgment is required. **Not success.** |
| `skipped` | An objective check could not run (for example Docker unavailable). **Not success.** |
| `timeout` | Solver exceeded its monotonic time budget; cancellation was attempted. |
| `error` | Runtime/configuration exception prevented a valid measurement. |

`workflow_completed` is recorded separately and is **not** the success metric.

## 2. Completion metrics

### Goal Completion Rate

```text
goal_completion_rate = passed_runs / total_runs
```

- Numerator: verifier status `PASSED`.
- Denominator: all attempted runs, including failed, skipped, timeout and
  needs-review runs.
- A workflow that completes while producing wrong artifacts is `failed`.
- A safe expected failure (e.g. permission denial or impossible request) is a
  `passed` benchmark task if the verifier confirms the safe behavior.

### Subtask Completion Rate

```text
completed substantive tasks / all substantive tasks
```

The synthetic root task is excluded. A cancelled/failed task is not completed.

### Recovery Success Rate

```text
passed failure_recovery tasks / all failure_recovery tasks
```

Recovery tasks include pause/resume, tool-error retry, cancellation,
permission-denial recovery and honest failure. Success still requires the task
verifier.

### Crash / Timeout Rate

```text
(timeout_runs + error_runs) / total_runs
```

An exception inside the workflow that is recovered into a normal failed workflow
is not a runner crash; it is classified by failure analysis.

## 3. Time metrics

- **End-to-end seconds**: monotonic `time.monotonic()` from immediately before
  the solver starts to the last workflow observation. Verifier/Docker check time
  is excluded. Auditable UTC start/finish timestamps are stored separately.
- **Active seconds**: sum of solver segments (`submit_goal`, each `resume_workflow`)
  measured monotonically. Simulated human wait is excluded.
- **Human wait seconds**: simulated wait from scripted human interactions,
  including explicit expiry waits. Always labeled simulated.

Reports include mean, median and p95 end-to-end time. Percentiles use linear
interpolation between ordered observations. No statistical significance claim is
made for small samples.

## 4. LLM metrics

- **LLM calls**: number of persisted `agent_executions` rows for the workflow.
  Each planning attempt and each task-loop turn is a row.
- **Prompt / completion tokens**: sum of provider usage. For fixture runs these
  are estimates derived from prompt/response length and are labeled as such.
- **Invalid JSON rate**: execution errors containing `AgentOutputError` or an
  invalid action payload divided by all executions.
- **Plan validation failure rate**: failed `purpose=plan` executions divided by
  all planning executions.
- **Agent completion rate**: completed substantive tasks divided by all
  substantive tasks.

## 5. Tool metrics

- **Tool calls**: persisted dispatcher records.
- **Tool failures**: records with status other than `SUCCESS`, including expected
  denials, pending approvals, schema errors and timeouts.
- **Tool failure rate**: tool failures / tool calls.
- **Retries**: repeated identical `(tool_name, canonical arguments)` invocations
  beyond the first. This includes legitimate recovery attempts; it is not a
  quality score on its own.
- **Tool budget exceeded**: total tool records > task `tool_budget`.
- **Tool schema error rate**: records with `metadata.reason=schema_validation`
  divided by all tool calls.

Expected failure-path tasks intentionally raise this rate; compare within the
same dataset, not against zero.

## 6. Delegation metrics

- **Delegation count**: substantive tasks with `origin=delegation`.
- **Delegation success rate**: completed delegated tasks / delegated tasks.
- **Max delegation depth**: maximum task depth.
- **Reassignments**: tasks with `reassigned_from`.

## 7. Memory metrics

- **Memory retrieval count**: persisted retrieval events scoped to the workflow.
- **Memory usage count**: feedback rows with `used=true`.
- Retrieval does not imply benefit; usage and verifier outcomes are separate.

## 8. HITL metrics

- **Approval count**: persisted approval requests.
- **Clarification count**: persisted clarification requests.
- Human responses in benchmark runs are scripted and marked `simulated=true`;
  they are not real user latency or real user judgment.
- Approval wait time is reported by workflow metrics where relevant; simulated
  wait is excluded from active execution time.

## 9. Aggregation

For a batch:

- per-category success rate = category passed / category total;
- category average time/tool/LLM values are arithmetic means;
- overall median and p95 use all successful and failed runs with valid timing;
- `skipped`/`needs_review` are not silently dropped or counted as passes.

## 10. Validity boundaries

- Fixture decisions are deterministic; completion measures harness correctness,
  not LLM capability.
- Real-provider checks remain `NOT_VERIFIED` without a key and explicit
  authorization.
- Single-agent and multi-agent runs are compared on the same tasks, provider,
  model, temperature, tool framework and budgets; the single agent has the union
  of the fleet's capabilities but no reviewer.
- Per-category samples are small; report descriptive statistics only.

## 11. Optimization metrics

Performance changes are validated by microbenchmarks in
`benchmarks/performance/measure.py`:

- SQLite query median/mean/p95 before/after index creation;
- tool-catalog per-call and total time before/after caching.

The optimization is kept only when it does not change output or verifier
results. Docker container startup is reported separately because it dominates
Python-task latency.
