# AgentForge Benchmark Report

- Batch: `batch_bffd0fbdf2ce45ff97cf60ecf3a25387`
- Dataset: `dev` (`D:\5202nlp课程project\benchmarks\dev`)
- Git commit: `f4e8c4965e01279a621b37ea9045e8f93d1806c4`
- Mode: `multi_agent`
- Provider: `mock` (fixture)
- Repeats: 1
- Runs: 2

- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.12.10`
- Executable: `D:\5202nlp课程project\.venv\Scripts\python.exe`

> **MOCK FIXTURE RESULT.** LLM actions are deterministic fixtures used only to validate the benchmark harness, tools, state machine and verifiers. These scores are NOT real-LLM capability and must not be reported as model performance.

## 1. Aggregate results

| Metric | Value |
|---|---:|
| Goal completion rate (verified) | 100.0% |
| Passed / failed | 2 / 0 |
| Needs review / skipped | 0 / 0 |
| Timeout / runtime error | 0 / 0 |
| Average E2E seconds | 0.133 |
| Median E2E seconds | 0.133 |
| p95 E2E seconds | 0.140 |
| Average active seconds | 0.070 |
| Total simulated human wait | 0.000s |
| Total LLM calls | 13 |
| Total / average tokens | 34995 / 17497.5 |
| Total tool calls | 4 |
| Tool failure rate | 0.0% |
| Retries (repeated identical calls) | 0 |
| Delegations / success rate | 0 / 0.0% |
| Memory retrievals / used | 0 / 0 |
| Approvals / clarifications | 0 / 0 |
| Recovery success rate | 0.0% |
| Crash/timeout rate | 0.0% |

## 2. Results by category

| Category | Total | Passed | Success | Avg E2E | Avg active | Avg tools | Avg LLM calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| file_editing | 2 | 2 | 100.0% | 0.133s | 0.070s | 2.00 | 6.50 |

## 3. Per-task runs

| Task | Category | Diff | Status | Verified | E2E | Active | Tokens | Tools | Failure |
|---|---|---|---|---|---:|---:|---:|---:|---|
| `file_multi_artifact` | file_editing | hard | passed | passed | 0.141s | 0.078s | 16005 | 2 | - |
| `file_revision_copy` | file_editing | hard | passed | passed | 0.125s | 0.063s | 18990 | 2 | - |

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
