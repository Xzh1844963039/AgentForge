# AgentForge Benchmark Report

- Batch: `batch_d5b87d064f4541a1bc58c63178185482`
- Dataset: `dev` (`D:\5202nlp课程project\benchmarks\dev\tasks.jsonl`)
- Git commit: `f4e8c4965e01279a621b37ea9045e8f93d1806c4`
- Mode: `multi_agent`
- Provider: `mock` (fixture)
- Repeats: 1
- Runs: 3

- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.12.10`
- Executable: `D:\5202nlp课程project\.venv\Scripts\python.exe`

> **MOCK FIXTURE RESULT.** LLM actions are deterministic fixtures used only to validate the benchmark harness, tools, state machine and verifiers. These scores are NOT real-LLM capability and must not be reported as model performance.

## 1. Aggregate results

| Metric | Value |
|---|---:|
| Goal completion rate (verified) | 100.0% |
| Passed / failed | 3 / 0 |
| Needs review / skipped | 0 / 0 |
| Timeout / runtime error | 0 / 0 |
| Average E2E seconds | 0.120 |
| Median E2E seconds | 0.125 |
| p95 E2E seconds | 0.125 |
| Average active seconds | 0.052 |
| Total simulated human wait | 0.000s |
| Total LLM calls | 20 |
| Total / average tokens | 45839 / 15279.7 |
| Total tool calls | 4 |
| Tool failure rate | 50.0% |
| Retries (repeated identical calls) | 1 |
| Delegations / success rate | 0 / 0.0% |
| Memory retrievals / used | 0 / 0 |
| Approvals / clarifications | 1 / 1 |
| Recovery success rate | 0.0% |
| Crash/timeout rate | 0.0% |

## 2. Results by category

| Category | Total | Passed | Success | Avg E2E | Avg active | Avg tools | Avg LLM calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| approval | 1 | 1 | 100.0% | 0.125s | 0.047s | 2.00 | 7.00 |
| clarification | 1 | 1 | 100.0% | 0.110s | 0.047s | 1.00 | 7.00 |
| file_editing | 1 | 1 | 100.0% | 0.125s | 0.062s | 1.00 | 6.00 |

## 3. Per-task runs

| Task | Category | Diff | Status | Verified | E2E | Active | Tokens | Tools | Failure |
|---|---|---|---|---|---:|---:|---:|---:|---|
| `file_exact_create` | file_editing | basic | passed | passed | 0.125s | 0.062s | 15158 | 1 | - |
| `approval_delete_rejected` | approval | basic | passed | passed | 0.125s | 0.047s | 15522 | 2 | - |
| `clarification_retention` | clarification | basic | passed | passed | 0.110s | 0.047s | 15159 | 1 | - |

## 4. Failure modes

No classified failures.

## 5. Metric definitions

- **Goal completion** is decided only by the task's objective verifier, not by `workflow.status == completed`.
- **E2E seconds** are monotonic wall-clock from task start to final observation; timestamps remain in the JSON record for audit.
- **Active seconds** exclude scripted human waiting. **Human wait** is simulated and explicitly labeled.
- **Token usage** for fixture runs is an estimate from prompt/response length; live runs use provider usage.
- **Retries** count repeated identical `(tool, arguments)` invocations beyond the first.

## 6. Notes and limitations

- Fixture-provider runs validate the harness only; they are not evidence of real-LLM task performance.
- Human responses in benchmark tasks are scripted simulations.
