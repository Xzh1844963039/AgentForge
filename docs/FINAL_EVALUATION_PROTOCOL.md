# Final Evaluation Protocol

This protocol defines how to run AgentForge on the official course test set when
the teacher publishes it. It **does not assume or embed any official task**. No
result in this repository is claimed for a test set that has not been received.

## 1. Inputs

Required from the course:

1. the official test dataset and its expected output format;
2. the permitted LLM provider(s), base URL, model name and credentials;
3. any human-review rubric for open-ended tasks;
4. time/cost limits and the required submission format.

If only a subset is available, run only the supported stages and report the
missing inputs explicitly.

## 2. Dataset adapter

Official tasks may be JSON, JSONL or a document-based format. Do not modify the
runner to special-case task IDs. Write a one-off adapter that maps each official
record into `BenchmarkTask`:

| BenchmarkTask field | Official mapping |
|---|---|
| `task_id` | stable official ID |
| `category`, `difficulty` | official category, or `unknown`/`hard` with a note |
| `goal` | exact user prompt; do not rewrite task intent |
| `initial_workspace` | official seed files copied into the mapped agent workspace |
| `available_agents` | official agent list, or the default four-role fleet |
| `allowed_tools` | official tool allow-list; empty means all registered tools |
| `time_budget_seconds`, `token_budget`, `tool_budget` | official limits |
| `max_delegation_depth` | official limit |
| `expected_artifacts` | official expected outputs |
| `verification` | objective file/Python/audit/state checks derived from official requirements |
| `human_script` | deterministic simulated human only for approval/clarification |
| `mock_actions` | empty for official evaluation |
| `behavior` | null for official evaluation |

Rules:

- Official task IDs, goals and seed data must be preserved.
- Do not insert fixture `mock_actions` or task-specific answers for official
  evaluation.
- If the official format cannot be mapped to objective checks, mark the task
  `manual_review_reason` and do not count it as an automatic pass.
- Keep the adapter and the official dataset version/hash in the run manifest.

## 3. Objective verification

Success is determined by `BenchmarkVerifier`, never by model prose or
`workflow.completed` alone.

| Official requirement | Verifier |
|---|---|
| File contents/paths | `FileCheck` (exact/contains/absent/min bytes) |
| Python behavior | independent `PythonCheck` in Docker |
| Tool use/errors/approvals | `ToolCallCheck` from SQLite audit |
| Workflow safety/state | `WorkflowCheck` |
| Approval/clarification | `ApprovalCheck` / `ClarificationCheck` |
| Delegation/task graph | `TaskGraphCheck` |
| No duplicate effects | `DuplicateEffectCheck` |
| Open-ended quality | explicit manual review, reported separately |

If Docker is unavailable, Python checks are `SKIPPED`; they are never converted
to passes. If a check cannot be objectively defined, record `NEEDS_REVIEW`.

## 4. Run protocol

### 4.1 Offline harness validation

Before using paid APIs:

```powershell
python -m pytest -q
python -m pytest -m docker -q
python -m ruff check src tests --select F,E9
python -m agentforge benchmark run --dataset benchmarks/dev --output-dir benchmarks/runs/dev_full
```

This validates the harness, tools, state machine and report generation. It is
**not** an official score.

### 4.2 Live provider acceptance

Only with course-authorized credentials and explicit permission:

```powershell
# Stage ladder, stop on first failure
python -m agentforge benchmark live --provider deepseek --stage all `
  --allow-live-api --max-requests 120 --max-tokens 250000

# Or run the official dataset through the live benchmark
python -m agentforge benchmark run --dataset <official-adapter-dataset> `
  --provider deepseek --allow-live-api --output-dir benchmarks/runs/official
```

For HKGAI, use `--provider hkgai` and the course-provided base URL/model.

Do not run without a budget. The provider is never silently replaced by Mock.

### 4.3 Repeats and budget

- Use at least 3 repeats for live runs when budget permits.
- Report mean/median/p95 and raw per-run failures; n<10 is exploratory only.
- Do not delete failed runs or retry until success and report only the best.
- Record provider model, temperature, Git commit, dataset hash, request/token
  usage and wall-clock.

## 5. Official metrics

Required outputs:

- verified goal completion rate;
- subtask completion rate;
- end-to-end, active and human-wait time;
- LLM calls and token usage (real provider usage for live);
- tool calls, failures and retries;
- delegation count/success;
- memory retrieval/use;
- approval/clarification counts;
- recovery success and crash/timeout rate;
- per-category results;
- failure taxonomy and raw evidence.

The final report must state sample size and uncertainty. `NEEDS_REVIEW` and
`SKIPPED` are not successes. Fixture scores and live scores must be separate.

## 6. HITL protocol

- Approval/clarification scripts are deterministic and marked simulated.
- A real user study, if required by the course, must be run separately with
  consent and must not be represented as simulated data.
- Approval must be bound to exact tool/arguments; a human decision cannot expand
  agent permissions.
- Live workflow resume must be tested after process restart.

## 7. Reliability protocol

Run the fault-injection suite and record:

- invalid JSON/action repair and exhaustion;
- provider timeout/rate-limit/auth errors;
- repeated action and token/LLM/tool limits;
- Docker timeout/container death;
- cancellation and approval expiry;
- unanswered clarification;
- SQLite audit/reconciliation;
- process restart and side-effect replay;
- memory retrieval failure;
- container cleanup.

Do not claim exactly-once execution. Unresolved high-risk effects must remain
`NEEDS_RECONCILIATION`.

## 8. Acceptance gates

An official run is accepted only if:

1. the dataset adapter preserves official task intent;
2. all objective checks execute or are explicitly `SKIPPED`/`NEEDS_REVIEW`;
3. the provider is real and authorized, or the report is explicitly
   `NOT_VERIFIED`;
4. every raw run record, failure and configuration is preserved;
5. no failed task is hidden or silently retried into the final average;
6. secrets are absent from logs/events/reports;
7. Git commit and environment are recorded.

## 9. If credentials or official data are unavailable

- Keep all offline tests and fixture harness results.
- Mark live stages `NOT_VERIFIED`.
- State exactly which environment variables or course inputs are missing.
- Do not call ZCode/internal models or other unauthorized APIs.
- Do not fabricate official scores.
