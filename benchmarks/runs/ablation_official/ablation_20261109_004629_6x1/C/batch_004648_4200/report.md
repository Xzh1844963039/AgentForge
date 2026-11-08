# AgentForge Benchmark Report

- Batch: `batch_6ba11cd477c74f62bafd4c1a887256a2`
- Dataset: `dev` (`D:\5202nlp课程project\benchmarks\dev`)
- Git commit: `f4e8c4965e01279a621b37ea9045e8f93d1806c4`
- Mode: `multi_agent`
- Provider: `mock` (fixture)
- Repeats: 1
- Runs: 6

- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.12.10`
- Executable: `D:\5202nlp课程project\.venv\Scripts\python.exe`

> **MOCK FIXTURE RESULT.** LLM actions are deterministic fixtures used only to validate the benchmark harness, tools, state machine and verifiers. These scores are NOT real-LLM capability and must not be reported as model performance.

## 1. Aggregate results

| Metric | Value |
|---|---:|
| Goal completion rate (verified) | 100.0% |
| Passed / failed | 6 / 0 |
| Needs review / skipped | 0 / 0 |
| Timeout / runtime error | 0 / 0 |
| Average E2E seconds | 1.135 |
| Median E2E seconds | 1.148 |
| p95 E2E seconds | 2.140 |
| Average active seconds | 1.078 |
| Total simulated human wait | 0.000s |
| Total LLM calls | 42 |
| Total / average tokens | 120590 / 20098.3 |
| Total tool calls | 11 |
| Tool failure rate | 0.0% |
| Retries (repeated identical calls) | 0 |
| Delegations / success rate | 0 / 0.0% |
| Memory retrievals / used | 5 / 0 |
| Approvals / clarifications | 0 / 0 |
| Recovery success rate | 0.0% |
| Crash/timeout rate | 0.0% |

## 2. Results by category

| Category | Total | Passed | Success | Avg E2E | Avg active | Avg tools | Avg LLM calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| code_execution | 1 | 1 | 100.0% | 2.125s | 2.063s | 2.00 | 7.00 |
| code_generation | 1 | 1 | 100.0% | 2.140s | 2.093s | 1.00 | 6.00 |
| debugging | 1 | 1 | 100.0% | 2.140s | 2.078s | 2.00 | 7.00 |
| file_editing | 1 | 1 | 100.0% | 0.109s | 0.062s | 1.00 | 6.00 |
| planning | 1 | 1 | 100.0% | 0.172s | 0.110s | 3.00 | 9.00 |
| research | 1 | 1 | 100.0% | 0.125s | 0.063s | 2.00 | 7.00 |

## 3. Per-task runs

| Task | Category | Diff | Status | Verified | E2E | Active | Tokens | Tools | Failure |
|---|---|---|---|---|---:|---:|---:|---:|---|
| `research_basic_facts` | research | basic | passed | passed | 0.125s | 0.063s | 19204 | 2 | - |
| `codegen_add_function` | code_generation | basic | passed | passed | 2.140s | 2.093s | 16422 | 1 | - |
| `debug_off_by_one` | debugging | basic | passed | passed | 2.140s | 2.078s | 20706 | 2 | - |
| `file_exact_create` | file_editing | basic | passed | passed | 0.109s | 0.062s | 16170 | 1 | - |
| `plan_research_to_coding` | planning | hard | passed | passed | 0.172s | 0.110s | 27383 | 3 | - |
| `execute_compute_sum` | code_execution | basic | passed | passed | 2.125s | 2.063s | 20705 | 2 | - |

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
